-- ═══════════════════════════════════════════════════════════════════
-- 021 — RECUPERAR DATAS dos torneios sem data_inicio (raiz do problema)
--
--  Contexto: 179 torneios estão SEM data_inicio. Isso é a raiz de quase
--  tudo — quebra o calendário, vira "shell", e cria os ÚNICOS duplicados
--  reais (que só colidem porque os dois lados estão sem data).
--
--  Ideia: a tabela `provas` já tem `data_prova` (o scraper extraiu direto
--  do quadro). Então, para todo torneio sem data que TENHA provas com
--  data, dá pra preencher:
--        data_inicio = MIN(provas.data_prova)
--        data_fim    = MAX(provas.data_prova)
--  …direto no Postgres, SEM depender do N8N.
--
--  ┌─────────────────────────────────────────────────────────────────┐
--  │ PARTES A, B, C = SOMENTE LEITURA. Rode, confira, me cole.         │
--  │ PARTE D        = ALTERA DADOS (UPDATE). Só rode DEPOIS de conferir.│
--  │ PARTE E        = verificação pós-backfill (leitura).              │
--  └─────────────────────────────────────────────────────────────────┘
--
--  Métodos de recuperação (prioridade A > B > C):
--    A · provas.data_prova .... datas EXATAS (início e fim). É o que o
--                               backfill da PARTE D realmente grava.
--    B · só ano no nome ....... tem "2025"/"2026" no nome mas NÃO tem
--                               provas com data → dá só o ano (aproximado).
--                               NÃO é gravado aqui (precisa de critério).
--    C · irrecuperável ........ nem provas com data, nem ano no nome →
--                               precisa re-scrape no N8N na fonte.
-- ═══════════════════════════════════════════════════════════════════


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE A — RECUPERABILIDADE (totais por método). [LEITURA]          ║
-- ║ Deve somar 179 (o total de torneios sem data_inicio).             ║
-- ╚═══════════════════════════════════════════════════════════════════╝
WITH alvo AS (
  SELECT id, fonte, nome FROM torneios WHERE data_inicio IS NULL
),
prov AS (
  SELECT torneio_id,
         min(data_prova) AS pini,
         max(data_prova) AS pfim,
         count(*) FILTER (WHERE data_prova IS NOT NULL) AS provas_com_data
  FROM provas
  GROUP BY torneio_id
),
classif AS (
  SELECT a.id, a.fonte,
         CASE
           WHEN COALESCE(pr.provas_com_data,0) > 0 THEN 'A · provas.data_prova (exato)'
           WHEN a.nome ~ '20[2-3][0-9]'            THEN 'B · só ano no nome (aprox.)'
           ELSE                                         'C · irrecuperável (re-scrape N8N)'
         END AS metodo
  FROM alvo a
  LEFT JOIN prov pr ON pr.torneio_id = a.id
)
SELECT COALESCE(metodo, 'TOTAL (sem data_inicio)') AS metodo,
       count(*)                                    AS torneios
FROM classif
GROUP BY ROLLUP (metodo)
ORDER BY metodo NULLS FIRST;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE B — RECUPERABILIDADE por FONTE × MÉTODO. [LEITURA]           ║
-- ║ Mostra quais federações ganham data já (A) e quais ficam pro N8N.  ║
-- ╚═══════════════════════════════════════════════════════════════════╝
WITH alvo AS (
  SELECT id, fonte, nome FROM torneios WHERE data_inicio IS NULL
),
prov AS (
  SELECT torneio_id,
         count(*) FILTER (WHERE data_prova IS NOT NULL) AS provas_com_data
  FROM provas
  GROUP BY torneio_id
),
classif AS (
  SELECT a.fonte,
         CASE
           WHEN COALESCE(pr.provas_com_data,0) > 0 THEN 'A_provas'
           WHEN a.nome ~ '20[2-3][0-9]'            THEN 'B_ano_nome'
           ELSE                                         'C_irrecuperavel'
         END AS metodo
  FROM alvo a
  LEFT JOIN prov pr ON pr.torneio_id = a.id
)
SELECT fonte,
       count(*)                                          AS sem_data,
       count(*) FILTER (WHERE metodo = 'A_provas')       AS rec_A_provas,
       count(*) FILTER (WHERE metodo = 'B_ano_nome')     AS rec_B_ano,
       count(*) FILTER (WHERE metodo = 'C_irrecuperavel') AS irrecuperavel
FROM classif
GROUP BY fonte
ORDER BY sem_data DESC;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE C — PREVIEW EXATO do backfill (método A). [LEITURA]          ║
-- ║ Estas são as linhas que a PARTE D vai gravar. CONFIRA antes!       ║
-- ║ span_dias = MAX-MIN das provas. Span enorme (>45d) = provas de      ║
-- ║ eventos diferentes podem estar grudadas no mesmo torneio → revisar. ║
-- ╚═══════════════════════════════════════════════════════════════════╝
WITH prov AS (
  SELECT torneio_id,
         min(data_prova) AS pini,
         max(data_prova) AS pfim,
         count(*) FILTER (WHERE data_prova IS NOT NULL) AS provas_com_data
  FROM provas
  GROUP BY torneio_id
)
SELECT t.id, t.fonte,
       s.pini                       AS nova_data_inicio,
       s.pfim                       AS nova_data_fim,
       (s.pfim - s.pini)            AS span_dias,
       CASE WHEN (s.pfim - s.pini) > 45 THEN '⚠ revisar' ELSE 'ok' END AS flag,
       s.provas_com_data            AS qtd_provas,
       t.nome
FROM torneios t
JOIN prov s ON s.torneio_id = t.id
WHERE t.data_inicio IS NULL
  AND s.provas_com_data > 0
ORDER BY span_dias DESC NULLS LAST, t.fonte, t.id;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE D — BACKFILL ⚠ ALTERA DADOS ⚠                                ║
-- ║                                                                     ║
-- ║   ╳╳╳ NÃO RODAR ╳╳╳  A medição (PARTE A) provou que 0 torneios       ║
-- ║   sem data têm provas com data_prova → este UPDATE não faria nada.  ║
-- ║   Mantido aqui só como registro. Ignore.                            ║
-- ║                                                                     ║
-- ║   • Só roda DEPOIS de conferir A, B e C.                            ║
-- ║   • Idempotente: só toca torneios com data_inicio IS NULL.          ║
-- ║     Rodar 2x não faz nada na 2ª vez.                                ║
-- ║   • Grava data_inicio = MIN(data_prova), data_fim = MAX(data_prova) ║
-- ║     apenas para torneios que TÊM provas com data (método A).        ║
-- ║   • Métodos B e C NÃO são tocados aqui (continuam NULL).            ║
-- ║                                                                     ║
-- ║   Para rodar: selecione o UPDATE abaixo e clique RUN.               ║
-- ╚═══════════════════════════════════════════════════════════════════╝
-- UPDATE torneios t
-- SET data_inicio = s.pini,
--     data_fim    = COALESCE(t.data_fim, s.pfim)
-- FROM (
--   SELECT torneio_id,
--          min(data_prova) AS pini,
--          max(data_prova) AS pfim
--   FROM provas
--   WHERE data_prova IS NOT NULL
--   GROUP BY torneio_id
-- ) s
-- WHERE t.id = s.torneio_id
--   AND t.data_inicio IS NULL;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE E — VERIFICAÇÃO pós-backfill. [LEITURA]                      ║
-- ║ Rode depois da PARTE D. Esperado: 'sem data agora' caiu de 179      ║
-- ║ para (179 − recuperados pelo método A).                            ║
-- ╚═══════════════════════════════════════════════════════════════════╝
SELECT
  (SELECT count(*) FROM torneios WHERE data_inicio IS NULL) AS sem_data_agora,
  (SELECT count(*) FROM torneios)                            AS torneios_total,
  (SELECT count(*) FROM torneios WHERE data_inicio IS NOT NULL) AS com_data_agora;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE F — POR QUE deu 0? (shell × provas-sem-data × doc). [LEITURA] ║
-- ║ Classifica os 179 sem-data por que TIPO de buraco eles são:         ║
-- ║   shells_sem_prova ...... nenhuma prova → nada no banco pra usar.    ║
-- ║   provas_sem_dataprova .. TEM provas, mas todas com data_prova NULL  ║
-- ║                           (= bug do scraper: gravou prova sem data). ║
-- ║   provas_com_data ....... deveria ser 0 (confere com a PARTE A).     ║
-- ║   tem_documento ......... tem programa/adendo → a data pode estar no ║
-- ║                           texto_extraido (recuperável por parse).    ║
-- ╚═══════════════════════════════════════════════════════════════════╝
WITH alvo AS (SELECT id FROM torneios WHERE data_inicio IS NULL),
m AS (
  SELECT a.id,
    (SELECT count(*) FROM provas p WHERE p.torneio_id = a.id)                                  AS provas_total,
    (SELECT count(*) FROM provas p WHERE p.torneio_id = a.id AND p.data_prova IS NOT NULL)      AS provas_com_data,
    (SELECT count(*) FROM torneio_documentos d WHERE d.torneio_id = a.id)                       AS docs
  FROM alvo a
)
SELECT
  count(*)                                                          AS sem_data_total,
  count(*) FILTER (WHERE provas_total = 0)                          AS shells_sem_prova,
  count(*) FILTER (WHERE provas_total > 0 AND provas_com_data = 0)  AS provas_sem_dataprova,
  count(*) FILTER (WHERE provas_com_data > 0)                       AS provas_com_data,
  count(*) FILTER (WHERE docs > 0)                                  AS tem_documento
FROM m;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE G — DATA "EMPRESTÁVEL" de uma fonte que datou o mesmo evento. ║
-- ║ [LEITURA] Casa por NOME normalizado (minúsculo, sem acento/espaço/  ║
-- ║ pontuação — o ano ENTRA na chave, então desempata ano). Mede quantos ║
-- ║ dos 179 teriam uma "irmã" JÁ datada de onde herdar a data:          ║
-- ║   casa_torneio_datado .. existe OUTRO torneio (qualquer fonte) com   ║
-- ║                          mesmo nome normalizado E data preenchida.   ║
-- ║   casa_cbh_datado ...... existe evento_cbh com mesmo nome E data.    ║
-- ║ É um PISO (match exato de nome é estrito; nomes diferentes p/ mesmo  ║
-- ║ evento não entram — isso é trabalho do resolver fuzzy depois).      ║
-- ╚═══════════════════════════════════════════════════════════════════╝
WITH alvo AS (
  SELECT id, regexp_replace(lower(nome), '[^a-z0-9]', '', 'g') AS chave
  FROM torneios WHERE data_inicio IS NULL
),
tor_datado AS (
  SELECT DISTINCT regexp_replace(lower(nome), '[^a-z0-9]', '', 'g') AS chave
  FROM torneios WHERE data_inicio IS NOT NULL
),
cbh_datado AS (
  SELECT DISTINCT regexp_replace(lower(evento), '[^a-z0-9]', '', 'g') AS chave
  FROM eventos_cbh WHERE data_inicio IS NOT NULL
)
SELECT
  count(*)                                                                              AS sem_data_total,
  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM tor_datado d WHERE d.chave = a.chave))    AS casa_torneio_datado,
  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM cbh_datado c WHERE c.chave = a.chave))    AS casa_cbh_datado
FROM alvo a;
