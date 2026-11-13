"""
Projeto TMDB: coleta de filmes e series -> CSVs -> SQL Server (arquivo unico).
Filtros e rankings ficam no SQL / Power BI.

Uso:
    python tmdb_projeto.py testar
    python tmdb_projeto.py coletar --paginas 3
    python tmdb_projeto.py carregar_sql      (CSVs -> SQL Server, apaga e recarrega)
    python tmdb_projeto.py atualizar --paginas 15   (coleta + carrega no SQL Server)

Dependencias: pip install requests python-dotenv pandas sqlalchemy pyodbc
Chave: arquivo .env na mesma pasta com  TMDB_API_KEY=sua_chave
       (ou TMDB_READ_TOKEN=seu_token_v4)
SQL Server (no .env): SQL_SERVER=localhost\\SQLEXPRESS  e  SQL_DATABASE=FilmesTMDB
       (login do Windows por padrao; para usuario/senha: SQL_USER e SQL_PASSWORD)
"""
import argparse
import csv
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)  # .env vale mais que variaveis do Windows
except ImportError:  # sem python-dotenv, usa so variaveis de ambiente do Windows
    pass

# =====================================================================
# 1. CONFIGURACOES  (nenhum segredo fica no codigo)
# =====================================================================
API_KEY = os.getenv("TMDB_API_KEY")
READ_TOKEN = os.getenv("TMDB_READ_TOKEN")

BASE_URL = "https://api.themoviedb.org/3"
IMAGEM_BASE_URL = "https://image.tmdb.org/t/p/w500"   # prefixo das imagens (w500 = largura 500px)
IDIOMA = "pt-BR"
REGIAO = "BR"
TIPOS = {"filme": "movie", "serie": "tv"}   # nosso nome -> trecho da URL
TIMEOUT = 15
PAUSA = 0.1                                  # pausa entre requisicoes
MAX_TENTATIVAS = 3
LIMITE_PAGINAS_API = 500                     # teto do /discover

MAX_PAGINAS = 5                              # paginas por consulta (20 itens/pagina)
MIN_VOTOS_COLETA = 200                       # piso de votos nas consultas por nota
ORDENACOES = ["popularity.desc", "vote_average.desc"]

# nome "oficial" -> nomes aceitos (minusculos) vindos do TMDB
PLATAFORMAS = {
    "Netflix": ["netflix"],
    "Amazon Prime Video": ["amazon prime video"],
    "Disney Plus": ["disney plus", "disney+"],
    "Apple TV": ["apple tv", "apple tv+", "apple tv plus"],
    "Globoplay": ["globoplay"],
    "HBO Max": ["hbo max", "max"],
    "Paramount Plus": ["paramount plus", "paramount+"],
    "Crunchyroll": ["crunchyroll"],
}

PASTA_DADOS = Path(__file__).parent / "dados"


def validar_credenciais():
    if not (API_KEY or READ_TOKEN):
        raise RuntimeError("Credencial nao encontrada. Defina TMDB_API_KEY (ou "
                           "TMDB_READ_TOKEN) no arquivo .env ou nas variaveis de ambiente.")


# =====================================================================
# 2. API  (requisicoes, retry, paginacao)
# =====================================================================
_sessao = requests.Session()


class TMDBError(Exception):
    pass


def api_get(caminho, params=None):
    """GET com retry/timeout/429. Devolve o JSON (dict)."""
    params = dict(params or {})
    headers = {"accept": "application/json"}
    if READ_TOKEN:
        headers["Authorization"] = f"Bearer {READ_TOKEN}"
    else:
        params["api_key"] = API_KEY
    url = BASE_URL + caminho

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            resp = _sessao.get(url, params=params, headers=headers, timeout=TIMEOUT)
        except (requests.Timeout, requests.ConnectionError) as erro:
            # so o NOME do erro: a mensagem completa pode conter a URL com a chave
            print(f"  [aviso] {type(erro).__name__} em {caminho} "
                  f"(tentativa {tentativa}/{MAX_TENTATIVAS})")
            time.sleep(2 ** tentativa)
            continue

        if resp.status_code == 200:
            time.sleep(PAUSA)
            try:
                return resp.json()
            except ValueError:
                raise TMDBError(f"Resposta de {caminho} nao e um JSON valido.")

        if resp.status_code == 429:
            try:
                espera = int(resp.headers.get("Retry-After", 2))
            except ValueError:
                espera = 2
            print(f"  [aviso] limite de requisicoes atingido; aguardando {espera}s")
            time.sleep(espera)
            continue

        if resp.status_code >= 500:
            print(f"  [aviso] HTTP {resp.status_code} em {caminho}; tentando de novo")
            time.sleep(2 ** tentativa)
            continue

        try:
            msg = resp.json().get("status_message", "")
        except ValueError:
            msg = ""
        raise TMDBError(f"HTTP {resp.status_code} em {caminho}: {msg}")

    raise TMDBError(f"Falha em {caminho} apos {MAX_TENTATIVAS} tentativas.")


def buscar_paginas(caminho, params, max_paginas):
    """Percorre as paginas. Devolve (resultados, info_de_cobertura)."""
    resultados = []
    coletadas = total_paginas = total_resultados = 0
    limite = min(max_paginas, LIMITE_PAGINAS_API)

    for pagina in range(1, limite + 1):
        dados = api_get(caminho, {**params, "page": pagina})
        if not isinstance(dados.get("results"), list):
            raise TMDBError(f"Resposta inesperada em {caminho} (sem lista 'results').")
        resultados.extend(dados["results"])
        coletadas = pagina
        total_paginas = dados.get("total_pages", pagina)
        total_resultados = dados.get("total_results", len(resultados))
        if pagina >= total_paginas:
            break

    return resultados, {"paginas_coletadas": coletadas, "total_paginas": total_paginas,
                        "total_resultados": total_resultados}


def listar_generos(tipo):
    return api_get(f"/genre/{TIPOS[tipo]}/list", {"language": IDIOMA}).get("genres", [])


def listar_provedores(tipo):
    return api_get(f"/watch/providers/{TIPOS[tipo]}",
                   {"watch_region": REGIAO, "language": IDIOMA}).get("results", [])


# =====================================================================
# 3. COLETA  (limpeza, deduplicacao, CSVs)
# =====================================================================
def escolher_provedores(tipo):
    escolhidos, achadas = [], set()
    for p in listar_provedores(tipo):
        nome = (p.get("provider_name") or "").strip()
        for plataforma, aceitos in PLATAFORMAS.items():
            if nome.casefold() in aceitos and p.get("provider_id") is not None:
                escolhidos.append({"tipo": tipo, "provedor_id": p["provider_id"],
                                   "provedor_nome": nome})
                achadas.add(plataforma)
    for plataforma in PLATAFORMAS:
        if plataforma not in achadas:
            print(f"  [aviso] '{plataforma}' nao encontrada entre os provedores de {tipo} "
                  f"no BR. Ajuste PLATAFORMAS.")
    print("  Provedores usados:", ", ".join(f"{p['provedor_nome']} (id {p['provedor_id']})"
                                            for p in escolhidos) or "nenhum")
    return escolhidos


def montar_url_imagem(poster_path):
    """Monta a URL completa do poster. Sem poster -> texto vazio (vira NULL no SQL)."""
    if not poster_path:
        return ""
    return IMAGEM_BASE_URL + poster_path


def limpar_item(item, tipo, hoje):
    """Converte um resultado do discover no nosso formato. None = descartar."""
    campo_data = "release_date" if tipo == "filme" else "first_air_date"
    campo_titulo = "title" if tipo == "filme" else "name"
    tmdb_id = item.get("id")
    titulo = item.get(campo_titulo)
    data = item.get(campo_data)
    if tmdb_id is None or not titulo or not data:
        return None
    if data > hoje:          # datas ISO (AAAA-MM-DD) comparam bem como texto
        return None
    return {
        "tipo": tipo, "tmdb_id": tmdb_id, "titulo": titulo, "data_lancamento": data,
        "nota_media": item.get("vote_average") or 0,
        "qtd_votos": item.get("vote_count") or 0,
        "popularidade": item.get("popularity") or 0,
        "resumo": (item.get("overview") or "").strip(),
        "url_imagem": montar_url_imagem(item.get("poster_path")),
    }


def montar_parametros(tipo, ordenacao, hoje, provedor_id=None):
    p = {"language": IDIOMA, "sort_by": ordenacao}
    if tipo == "filme":
        p["primary_release_date.lte"] = hoje
        p["include_adult"] = "false"
    else:
        p["first_air_date.lte"] = hoje
    if ordenacao.startswith("vote_average"):
        p["vote_count.gte"] = MIN_VOTOS_COLETA
    if provedor_id is not None:
        p["watch_region"] = REGIAO
        p["with_watch_providers"] = provedor_id
        p["with_watch_monetization_types"] = "flatrate"
    return p


def coletar(max_paginas):
    validar_credenciais()
    hoje = date.today().isoformat()

    titulos = {}            # (tipo, tmdb_id) -> dados  (a chave evita duplicatas)
    liga_genero = set()     # (tipo, tmdb_id, genero_id)
    liga_provedor = set()   # (tipo, tmdb_id, provedor_id)
    generos, provedores, cobertura = [], [], []
    descartados = 0

    for tipo in TIPOS:
        print(f"\n=== {tipo.upper()}S ===")
        for g in listar_generos(tipo):
            generos.append({"tipo": tipo, "genero_id": g["id"], "genero_nome": g["name"]})
        provs = escolher_provedores(tipo)
        provedores.extend(provs)

        for ordenacao in ORDENACOES:
            for prov in [None] + provs:       # None = catalogo geral
                prov_id = prov["provedor_id"] if prov else None
                rotulo = prov["provedor_nome"] if prov else "geral"
                print(f"- {tipo} | {ordenacao} | {rotulo}")
                itens, info = buscar_paginas(f"/discover/{TIPOS[tipo]}",
                                             montar_parametros(tipo, ordenacao, hoje, prov_id),
                                             max_paginas)
                for item in itens:
                    limpo = limpar_item(item, tipo, hoje)
                    if limpo is None:
                        descartados += 1
                        continue
                    titulos[(tipo, limpo["tmdb_id"])] = limpo
                    for gid in item.get("genre_ids") or []:
                        liga_genero.add((tipo, limpo["tmdb_id"], gid))
                    if prov_id is not None:
                        liga_provedor.add((tipo, limpo["tmdb_id"], prov_id))

                print(f"  paginas {info['paginas_coletadas']}/{info['total_paginas']} "
                      f"(a API tem {info['total_resultados']} resultados) | "
                      f"titulos unicos ate agora: {len(titulos)}")
                cobertura.append({
                    "tipo": tipo, "ordenacao": ordenacao,
                    "provedor_id": prov_id if prov_id is not None else "",
                    "provedor_nome": prov["provedor_nome"] if prov else "",
                    **info,
                    "min_votos_coleta": MIN_VOTOS_COLETA if ordenacao.startswith("vote") else "",
                    "data_coleta": datetime.now().isoformat(timespec="seconds"),
                })

    salvar_tudo(titulos, liga_genero, liga_provedor, generos, provedores, cobertura)
    print(f"\nConcluido: {len(titulos)} titulos unicos, {descartados} resultados descartados "
          f"(sem data/titulo ou ainda nao lancados).")
    print("ATENCAO: coleta PARCIAL (limitada as paginas configuradas), nao o catalogo completo.")


def salvar_csv(nome, colunas, linhas):
    PASTA_DADOS.mkdir(exist_ok=True)
    caminho = PASTA_DADOS / nome
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:   # Excel/Power BI leem acentos
        w = csv.DictWriter(f, fieldnames=colunas)
        w.writeheader()
        w.writerows(linhas)
    print(f"  salvo: {caminho} ({len(linhas)} linhas)")


def salvar_tudo(titulos, liga_genero, liga_provedor, generos, provedores, cobertura):
    print("\nSalvando arquivos...")
    salvar_csv("titulos.csv",
               ["tipo", "tmdb_id", "titulo", "data_lancamento", "nota_media",
                "qtd_votos", "popularidade", "resumo", "url_imagem"],
               sorted(titulos.values(), key=lambda t: (t["tipo"], t["tmdb_id"])))
    salvar_csv("generos.csv", ["tipo", "genero_id", "genero_nome"], generos)
    salvar_csv("titulo_genero.csv", ["tipo", "tmdb_id", "genero_id"],
               [{"tipo": a, "tmdb_id": b, "genero_id": c} for a, b, c in sorted(liga_genero)])
    salvar_csv("provedores.csv", ["tipo", "provedor_id", "provedor_nome"], provedores)
    salvar_csv("titulo_provedor.csv", ["tipo", "tmdb_id", "provedor_id"],
               [{"tipo": a, "tmdb_id": b, "provedor_id": c} for a, b, c in sorted(liga_provedor)])
    salvar_csv("cobertura.csv",
               ["tipo", "ordenacao", "provedor_id", "provedor_nome", "paginas_coletadas",
                "total_paginas", "total_resultados", "min_votos_coleta", "data_coleta"],
               cobertura)


# =====================================================================
# 5. TESTES SIMPLES  (sem chamadas a API)  ->  python tmdb_projeto.py testar
# =====================================================================
def testar():
    hoje = "2025-06-01"
    assert limpar_item({"id": 1, "title": "A", "release_date": "2020-01-01"}, "filme", hoje)
    assert limpar_item({"id": 1, "title": "A", "release_date": "2030-01-01"}, "filme", hoje) is None  # futuro
    assert limpar_item({"id": 1, "title": "A", "release_date": ""}, "filme", hoje) is None            # sem data
    assert limpar_item({"id": None, "title": "A", "release_date": "2020-01-01"}, "filme", hoje) is None
    assert limpar_item({"id": 2, "name": "S", "first_air_date": "2021-05-05"}, "serie", hoje)
    assert limpar_item({"id": 2, "name": "S", "first_air_date": "2021-05-05"}, "filme", hoje) is None  # campo errado
    p = montar_parametros("filme", "vote_average.desc", hoje, 8)
    assert p["with_watch_providers"] == 8 and p["watch_region"] == "BR" and p["vote_count.gte"] == MIN_VOTOS_COLETA
    assert "with_watch_providers" not in montar_parametros("serie", "popularity.desc", hoje)
    # imagem: com poster vira URL completa; sem poster fica vazio
    com_poster = limpar_item({"id": 3, "title": "B", "release_date": "2020-01-01",
                              "poster_path": "/abc.jpg"}, "filme", hoje)
    assert com_poster["url_imagem"] == IMAGEM_BASE_URL + "/abc.jpg"
    sem_poster = limpar_item({"id": 4, "title": "C", "release_date": "2020-01-01",
                              "poster_path": None}, "filme", hoje)
    assert sem_poster["url_imagem"] == ""
    print("Todos os testes passaram.")


# =====================================================================
# 5B. SQL SERVER  (pandas + SQLAlchemy: apaga e recria as tabelas a cada carga)
# =====================================================================
TABELAS_SQL = ["titulos", "generos", "titulo_genero", "provedores", "titulo_provedor", "cobertura"]

# Colunas de data (viram DATE/DATETIME em vez de texto)
PARSE_DATAS = {"titulos": ["data_lancamento"], "cobertura": ["data_coleta"]}

# Colunas inteiras que podem ficar vazias (NULL)
INTEIROS_NULOS = {"cobertura": {"provedor_id": "Int64", "min_votos_coleta": "Int64"}}


def carregar_sql():
    """Envia os 6 CSVs para o SQL Server. Tudo numa transacao: se algo falhar,
    as tabelas antigas continuam intactas."""
    try:
        import pandas as pd
        from sqlalchemy import create_engine
        from sqlalchemy.types import NVARCHAR, VARCHAR, Date
    except ImportError:
        raise RuntimeError("Faltam bibliotecas. Rode: pip install pandas sqlalchemy pyodbc")
    from urllib.parse import quote_plus

    # ---------- Configuracoes ----------
    servidor = "GustavoGomes"
    banco = os.getenv("SQL_DATABASE")
    driver = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")  # ou "ODBC Driver 18 for SQL Server"
    faltando = [n for n, v in (("SQL_SERVER", servidor), ("SQL_DATABASE", banco)) if not v]
    if faltando:
        env = Path(__file__).parent / ".env"
        raise RuntimeError(f"Sem valor: {', '.join(faltando)}. O programa procurou o .env em "
                           f"{env} (arquivo existe: {env.exists()}). "
                           f"Confira o nome da variavel e se o python-dotenv esta instalado.")

    # Tipos de texto com tamanho definido (evita NVARCHAR(MAX) em colunas de chave)
    tipos_sql = {
        "titulos": {"tipo": VARCHAR(10), "titulo": NVARCHAR(500), "data_lancamento": Date,
                    "url_imagem": VARCHAR(300)},
        "generos": {"tipo": VARCHAR(10), "genero_nome": NVARCHAR(100)},
        "titulo_genero": {"tipo": VARCHAR(10)},
        "provedores": {"tipo": VARCHAR(10), "provedor_nome": NVARCHAR(100)},
        "titulo_provedor": {"tipo": VARCHAR(10)},
        "cobertura": {"tipo": VARCHAR(10), "ordenacao": VARCHAR(30), "provedor_nome": NVARCHAR(100)},
    }

    # ---------- Leitura dos CSVs (antes de mexer no banco) ----------
    dfs = {}
    for tabela in TABELAS_SQL:
        caminho = PASTA_DADOS / f"{tabela}.csv"
        print(f"Lendo {caminho} ...")
        if not caminho.exists():
            raise FileNotFoundError(f"{caminho} nao existe. Rode antes: python tmdb_projeto.py coletar")
        # utf-8-sig: os CSVs do projeto sao gravados com BOM (acentos corretos)
        dfs[tabela] = pd.read_csv(caminho, sep=",", encoding="utf-8-sig", low_memory=False,
                                  parse_dates=PARSE_DATAS.get(tabela, []),
                                  dtype=INTEIROS_NULOS.get(tabela))

    # ---------- Conexao (Autenticacao do Windows) ----------
    params = quote_plus(
        f"DRIVER={{{driver}}};"
        f"SERVER={servidor};"
        f"DATABASE={banco};"
        f"Trusted_Connection=yes;"
        f"TrustServerCertificate=yes;"
    )
    engine = create_engine(f"mssql+pyodbc:///?odbc_connect={params}",
                           fast_executemany=True)   # acelera muito a insercao

    # ---------- Envio (uma transacao so) ----------
    try:
        with engine.begin() as con:
            for tabela, df in dfs.items():
                print(f"  {len(df)} linhas, {len(df.columns)} colunas. "
                      f"Enviando para a tabela '{tabela}' ...")
                df.to_sql(name=tabela, con=con, schema="dbo",
                          if_exists="replace",     # recria a tabela (sobrescreve)
                          index=False, chunksize=1000, dtype=tipos_sql.get(tabela))
                print(f"  Tabela dbo.{tabela} carregada com sucesso!")
    except Exception as erro:
        raise RuntimeError(f"Falha ao carregar no SQL Server ({type(erro).__name__}): {erro}")
    print("Carga no SQL Server concluida.")


# =====================================================================
# 6. LINHA DE COMANDO
# =====================================================================
def cmd_coletar(args):
    estimativa = 2 * len(ORDENACOES) * (1 + len(PLATAFORMAS)) * args.paginas + 6
    print(f"Estimativa: ate ~{estimativa} requisicoes (paginas por consulta: {args.paginas}).")
    coletar(args.paginas)


def cmd_carregar_sql(args):
    carregar_sql()


def cmd_atualizar(args):
    cmd_coletar(args)     # se a coleta falhar, levanta erro e a carga NAO acontece
    carregar_sql()


def main():
    p = argparse.ArgumentParser(description="Coleta de filmes/series (TMDB) e carga no SQL Server")
    sub = p.add_subparsers(dest="comando", required=True)

    sub.add_parser("testar").set_defaults(func=lambda a: testar())

    c = sub.add_parser("coletar")
    c.add_argument("--paginas", type=int, default=MAX_PAGINAS)
    c.set_defaults(func=cmd_coletar)

    sub.add_parser("carregar_sql").set_defaults(func=cmd_carregar_sql)

    a = sub.add_parser("atualizar")
    a.add_argument("--paginas", type=int, default=MAX_PAGINAS)
    a.set_defaults(func=cmd_atualizar)

    args = p.parse_args()
    try:
        args.func(args)
    except (RuntimeError, FileNotFoundError, TMDBError) as erro:
        print(f"Erro: {erro}")
        sys.exit(1)


if __name__ == "__main__":
    main()