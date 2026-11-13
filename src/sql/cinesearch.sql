USE FILMESTMDB;

--TOP 10 POR NOTA MEDIA , MINIMO DE 1000 VOTOS , POR GENERO/PLATAFORMA
CREATE OR ALTER VIEW dbo.vw_Titulos
AS
WITH GENEROS_AGREGADOS AS (
    SELECT
        TG.tmdb_id
        ,TG.tipo
        ,STRING_AGG(G.genero_nome, ', ') AS generos
    FROM titulo_genero TG
    JOIN generos G
        ON TG.genero_id = G.genero_id
       AND TG.tipo = G.tipo
    GROUP BY
        TG.tmdb_id
        ,TG.tipo
),
PROVEDORES_AGREGADOS AS (
    SELECT
        TP.tmdb_id
        ,TP.tipo
        ,STRING_AGG(P.provedor_nome, ', ') AS provedores
    FROM titulo_provedor TP
    JOIN provedores P
        ON TP.provedor_id = P.provedor_id
       AND TP.tipo = P.tipo
    GROUP BY
        TP.tmdb_id
        ,TP.tipo
),
BASE AS (
    SELECT
        T.*
        ,CAST(T.nota_media AS DECIMAL(10,3)) AS R
        ,CAST(T.qtd_votos AS DECIMAL(10,3)) AS V
        ,CAST(
            AVG(T.nota_media) OVER(
                PARTITION BY T.tipo
            )
            AS DECIMAL(10,3)
        ) AS C
    FROM titulos T
    WHERE T.qtd_votos > 1000
)
SELECT
    concat(B.tipo,'_',B.tmdb_id) as id_titulo
    ,B.titulo
    ,B.tipo
    ,B.nota_media
    ,CAST(
        (B.V / (B.V + 2250.0)) * B.R
        +
        (2250.0 / (B.V + 2250.0)) * B.C
        AS DECIMAL(10,2)
    ) AS nota_ponderada
    ,B.qtd_votos
    ,B.popularidade
    ,B.data_lancamento
    ,B.resumo
    ,G.generos
    ,P.provedores
    ,B.url_imagem as poster_url
FROM BASE B
LEFT JOIN GENEROS_AGREGADOS G
    ON B.tmdb_id = G.tmdb_id
   AND B.tipo = G.tipo
INNER JOIN PROVEDORES_AGREGADOS P
    ON B.tmdb_id = P.tmdb_id
   AND B.tipo = P.tipo;
GO

CREATE OR ALTER VIEW dbo.vw_Provedores
AS
SELECT
    concat(T.tipo,'_',T.tmdb_id) as id_titulo
    ,T.tipo
    ,T.titulo
    ,T.data_lancamento
    ,T.nota_media
    ,T.resumo
    ,P.provedor_nome
FROM TITULOS T
JOIN titulo_provedor TP
    ON T.tmdb_id = TP.tmdb_id
   AND T.tipo = TP.tipo
JOIN provedores P
    ON TP.provedor_id = P.provedor_id
   AND TP.tipo = P.tipo
WHERE T.qtd_votos > 1000;
GO


CREATE OR ALTER VIEW dbo.vw_Generos
AS
SELECT DISTINCT
    concat(T.tipo,'_',T.tmdb_id) as id_titulo
    ,T.tipo
    ,G.genero_nome
FROM titulos T
JOIN titulo_genero TG
    ON T.tmdb_id = TG.tmdb_id
   AND T.tipo = TG.tipo
JOIN generos G
    ON TG.genero_id = G.genero_id
   AND TG.tipo = G.tipo
WHERE T.qtd_votos > 1000;
GO


