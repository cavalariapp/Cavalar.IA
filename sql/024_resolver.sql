-- ═══════════════════════════════════════════════════════════════════
-- 024 — RESOLVER de identidade  ⚠ DRAFT — NÃO RODAR AINDA ⚠
--
--  POPULA o modelo canônico (eventos + evento_fontes) a partir do dado
--  BRUTO existente (torneios + eventos_cbh). É o "023 (resolver)" citado
--  no 022. Roda SOMENTE depois de, nesta ordem:
--      022 (schema)  →  023 (seed federacoes)  →  Carol revisar tudo.
--
--  DISCIPLINA (igual ao 020/021): blocos de LEITURA primeiro; blocos que
--  ALTERAM ficam isolados e rotulados. O MERGE (PARTE E) vem COMENTADO
--  (╳ NÃO RODAR ╳) porque fundir eventos é a operação mais arriscada —
--  só liberar depois de conferir o relatório da PARTE D.
--
--  FILOSOFIA "sem perder nada": na dúvida, NÃO funde. Criar um evento a
--  mais (revisável depois) nunca perde dado; uma fusão errada, sim.
--
--  ┌─ LIMITES do que dá pra resolver com o dado de HOJE ────────────────┐
--  │ • torneios é "magro": só id, nome, fonte, data_inicio, data_fim,    │
--  │   fingerprint, evento_cbh_id. NÃO tem uf/cidade/local/disciplina    │
--  │   NEM organizador. Logo uf/disciplina/local do canônico vêm do CBH. │
--  │ • eh_dono e a detecção de FANTASMA dependem do campo "organizador"  │
--  │   da página de detalhe — que o scraper AINDA não guarda. Por isso    │
--  │   aqui eh_dono é PROVISÓRIO (=true p/ a fonte que listou). O conserto │
--  │   do N8N (#85) passa a gravar organizador → resolver-v2 recalcula.  │
--  └────────────────────────────────────────────────────────────────────┘
-- ═══════════════════════════════════════════════════════════════════


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE 0 — HELPERS (normalização + mapeamento de organizador).       ║
-- ║ [ALTERA: cria extensão + 2 funções. Inofensivo, idempotente.]       ║
-- ╚═══════════════════════════════════════════════════════════════════╝
CREATE EXTENSION IF NOT EXISTS unaccent;   -- se falhar: rode como dono do schema ou use extensions.unaccent()

-- chave normalizada: minúsculo, sem acento, só [a-z0-9]. MANTÉM dígitos
-- (ordinais "1ª/2ª" continuam distintos → não cola etapas de uma série).
CREATE OR REPLACE FUNCTION norm_txt(t text) RETURNS text
  LANGUAGE sql STABLE AS
$$ SELECT regexp_replace(lower(unaccent(coalesce(t,''))), '[^a-z0-9]', '', 'g') $$;

-- mapeia texto cru de organizador → federacoes.codigo (ou NULL se desconhecido)
CREATE OR REPLACE FUNCTION map_federacao(org text) RETURNS text
  LANGUAGE sql STABLE AS
$$
  SELECT f.codigo FROM federacoes f
  WHERE norm_txt(org) = norm_txt(f.codigo)
     OR norm_txt(org) = norm_txt(f.nome)
     OR norm_txt(org) = ANY (SELECT norm_txt(v) FROM unnest(f.variantes) v)
  ORDER BY length(f.codigo)
  LIMIT 1
$$;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE A — PREVIEW do que o resolver vai fazer. [LEITURA] Cole o      ║
-- ║ resultado antes de rodar B/C.                                       ║
-- ╚═══════════════════════════════════════════════════════════════════╝
SELECT 'torneios sem evento_id (viram eventos no Pass-1)' AS o, count(*) AS n
  FROM torneios WHERE evento_id IS NULL
UNION ALL SELECT 'eventos_cbh cobertos por torneio (FK ou fingerprint)',
  (SELECT count(*) FROM eventos_cbh c WHERE EXISTS (
     SELECT 1 FROM torneios t WHERE t.evento_cbh_id = c.id
        OR (t.fingerprint IS NOT NULL AND t.fingerprint = c.fingerprint)))
UNION ALL SELECT 'eventos_cbh ÓRFÃOS (viram evento próprio no Pass-2)',
  (SELECT count(*) FROM eventos_cbh c WHERE NOT EXISTS (
     SELECT 1 FROM torneios t WHERE t.evento_cbh_id = c.id
        OR (t.fingerprint IS NOT NULL AND t.fingerprint = c.fingerprint)))
UNION ALL SELECT 'organizadores CBH que NÃO mapeiam (faltam no seed 023)',
  (SELECT count(DISTINCT c.federacao) FROM eventos_cbh c
    WHERE c.federacao IS NOT NULL AND map_federacao(c.federacao) IS NULL)
UNION ALL SELECT 'grupos de eventos c/ MESMO nome normalizado (candidatos a fusão)',
  (SELECT count(*) FROM (
     SELECT norm_txt(nome) AS k FROM torneios GROUP BY norm_txt(nome) HAVING count(*) > 1) z);


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE B — PASS 1: 1 evento por torneio + a menção da fonte.          ║
-- ║ [ALTERA] Idempotente (só torneios com evento_id IS NULL).           ║
-- ║ Truque: gera o uuid num CTE MATERIALIZED → o MESMO id vai pra        ║
-- ║ eventos.id, evento_fontes.evento_id E torneios.evento_id.           ║
-- ╚═══════════════════════════════════════════════════════════════════╝
WITH novos AS MATERIALIZED (
  SELECT t.id AS torneio_id, gen_random_uuid() AS evento_id,
         t.nome, t.fonte, t.data_inicio, t.data_fim, t.fingerprint,
         (t.evento_cbh_id IS NOT NULL) AS tem_cbh,
         EXISTS (SELECT 1 FROM provas p WHERE p.torneio_id = t.id)             AS tem_provas,
         EXISTS (SELECT 1 FROM torneio_documentos d WHERE d.torneio_id = t.id) AS tem_docs
  FROM torneios t
  WHERE t.evento_id IS NULL
),
ins_ev AS (
  INSERT INTO eventos (id, nome_canonico, data_inicio, data_fim, tem_no_cbh, confianca, status)
  SELECT evento_id, nome, data_inicio, data_fim, tem_cbh, 1.00, 'ativo'
  FROM novos
  RETURNING id
),
ins_fonte AS (
  INSERT INTO evento_fontes
    (evento_id, fonte, id_nativo, organizador_declarado, eh_dono,
     tem_provas, tem_documentos, fingerprint, torneio_id)
  SELECT evento_id, fonte,
         'torneio:'||torneio_id,   -- chave nativa PROVISÓRIA; trocar pelo tenant+ID macronetwork qdo o N8N gravar
         NULL,                     -- organizador: torneios não guarda (vem do N8N depois)
         true,                     -- eh_dono PROVISÓRIO: a fonte que listou é presumida dona
         tem_provas, tem_docs, fingerprint, torneio_id
  FROM novos
  RETURNING evento_id
)
UPDATE torneios t SET evento_id = n.evento_id
FROM novos n WHERE t.id = n.torneio_id;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE C1 — PASS 2a: CBH que JÁ tem torneio → anexa menção + enriquece║
-- ║ o evento com disciplina/uf/local/dono (que torneios não tem).        ║
-- ║ [ALTERA] Rode DEPOIS da PARTE B. Idempotente.                       ║
-- ╚═══════════════════════════════════════════════════════════════════╝
WITH alvo AS (
  SELECT c.id AS cbh_id, c.tipo, c.estado, c.local, c.federacao,
         c.fingerprint AS fp_cbh,
         (SELECT t.evento_id FROM torneios t
            WHERE t.evento_id IS NOT NULL
              AND (t.evento_cbh_id = c.id
                   OR (t.fingerprint IS NOT NULL AND t.fingerprint = c.fingerprint))
            ORDER BY (t.evento_cbh_id = c.id) DESC   -- prioriza FK sobre fingerprint
            LIMIT 1) AS evento_id
  FROM eventos_cbh c
  WHERE c.evento_id IS NULL
),
cobertos AS (SELECT * FROM alvo WHERE evento_id IS NOT NULL),
ins_fonte AS (
  INSERT INTO evento_fontes
    (evento_id, fonte, id_nativo, organizador_declarado, eh_dono, tem_resultados, fingerprint)
  SELECT evento_id, 'CBH', 'cbh:'||cbh_id,
         federacao,   -- aqui SIM temos o organizador declarado (campo federacao do CBH)
         false,       -- CBH é agregador de calendário, NÃO publica programa/resultado → nunca é dono dos docs
         false, fp_cbh
  FROM cobertos
  ON CONFLICT (fonte, id_nativo) DO NOTHING
  RETURNING evento_id
),
enrich AS (
  UPDATE eventos e SET
    disciplina     = COALESCE(e.disciplina, c.tipo),
    uf             = COALESCE(e.uf, c.estado),
    local          = COALESCE(e.local, c.local),
    federacao_dono = COALESCE(e.federacao_dono, map_federacao(c.federacao)),
    tem_no_cbh     = true,
    atualizado_em  = now()
  FROM cobertos c WHERE e.id = c.evento_id
  RETURNING e.id
)
UPDATE eventos_cbh c SET evento_id = k.evento_id
FROM cobertos k WHERE c.id = k.cbh_id;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE C2 — PASS 2b: CBH ÓRFÃO (ninguém publicou) → evento próprio.   ║
-- ║ São os eventos do PROBLEMA #3 (no CBH mas em nenhuma federação).     ║
-- ║ [ALTERA] Rode DEPOIS da C1. Idempotente.                            ║
-- ╚═══════════════════════════════════════════════════════════════════╝
WITH novos AS MATERIALIZED (
  SELECT c.id AS cbh_id, gen_random_uuid() AS evento_id,
         c.evento, c.tipo, c.estado, c.local, c.federacao, c.fingerprint,
         c.data_inicio, c.data_fim
  FROM eventos_cbh c
  WHERE c.evento_id IS NULL
),
ins_ev AS (
  INSERT INTO eventos
    (id, nome_canonico, disciplina, uf, local, data_inicio, data_fim,
     federacao_dono, tem_no_cbh, confianca, status)
  SELECT evento_id, evento, tipo, estado, local, data_inicio, data_fim,
         map_federacao(federacao), true, 0.90, 'ativo'
  FROM novos
  RETURNING id
),
ins_fonte AS (
  INSERT INTO evento_fontes
    (evento_id, fonte, id_nativo, organizador_declarado, eh_dono, tem_resultados, fingerprint)
  SELECT evento_id, 'CBH', 'cbh:'||cbh_id, federacao, false, false, fingerprint
  FROM novos
  ON CONFLICT (fonte, id_nativo) DO NOTHING
  RETURNING evento_id
)
UPDATE eventos_cbh c SET evento_id = n.evento_id
FROM novos n WHERE c.id = n.cbh_id;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE D — CANDIDATOS A FUSÃO. [LEITURA] Confira ANTES da PARTE E.    ║
-- ║ Agrupa eventos ativos por nome normalizado. span_dias enorme = NÃO   ║
-- ║ é o mesmo evento (edições de anos diferentes) → não fundir.         ║
-- ╚═══════════════════════════════════════════════════════════════════╝
WITH e AS (
  SELECT id, nome_canonico, uf, data_inicio, data_fim, qtd_mencoes,
         norm_txt(nome_canonico) AS chave
  FROM (
    SELECT ev.*, (SELECT count(*) FROM evento_fontes ef WHERE ef.evento_id = ev.id) AS qtd_mencoes
    FROM eventos ev WHERE ev.status = 'ativo'
  ) x
)
SELECT chave,
       count(*)                                            AS eventos,
       sum(qtd_mencoes)                                    AS mencoes_totais,
       string_agg(DISTINCT uf, ',')                        AS ufs,
       min(data_inicio)                                    AS primeira,
       max(COALESCE(data_fim, data_inicio))                AS ultima,
       (max(COALESCE(data_fim,data_inicio)) - min(data_inicio)) AS span_dias,
       string_agg(DISTINCT nome_canonico, ' || ')          AS nomes,
       array_agg(id)                                       AS evento_ids
FROM e
GROUP BY chave
HAVING count(*) > 1
ORDER BY span_dias ASC NULLS LAST, eventos DESC
LIMIT 100;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE E — FUNDIR duplicatas ⚠ ALTERA / DESTRUTIVO-ish ⚠             ║
-- ║   ╳╳╳ NÃO RODAR sem antes conferir a PARTE D ╳╳╳                     ║
-- ║                                                                     ║
-- ║ Critério conservador: MESMO nome normalizado + datas SOBREPOSTAS +   ║
-- ║ (mesma UF ou UF de um dos lados ausente). Vencedor = o que tem CBH;  ║
-- ║ empate → menor id. Perdedor vira status='fundido', fundido_em=vencedor;║
-- ║ as menções e os FKs (torneios/eventos_cbh) migram pro vencedor.      ║
-- ║ NÃO apaga nada → reversível. Descomente bloco a bloco com cuidado.   ║
-- ╚═══════════════════════════════════════════════════════════════════╝
-- WITH grupo AS (
--   SELECT id, norm_txt(nome_canonico) AS chave, uf, tem_no_cbh,
--          data_inicio, COALESCE(data_fim, data_inicio) AS dfim
--   FROM eventos WHERE status = 'ativo'
-- ),
-- pares AS (   -- a = vencedor, b = perdedor (a "ganha" por CBH, depois por id menor)
--   SELECT a.id AS vencedor, b.id AS perdedor
--   FROM grupo a JOIN grupo b
--     ON a.chave = b.chave AND a.id <> b.id
--    AND a.data_inicio <= b.dfim AND b.data_inicio <= a.dfim          -- sobreposição
--    AND (a.uf IS NOT DISTINCT FROM b.uf OR a.uf IS NULL OR b.uf IS NULL)
--    AND (a.tem_no_cbh, -a.id) > (b.tem_no_cbh, -b.id)                -- desempate determinístico
-- ),
-- -- 1 vencedor final por perdedor (evita cadeia A←B←C)
-- escolha AS (
--   SELECT DISTINCT ON (perdedor) perdedor, vencedor
--   FROM pares ORDER BY perdedor, vencedor
-- ),
-- mv_fontes AS (
--   UPDATE evento_fontes ef SET evento_id = e.vencedor
--   FROM escolha e WHERE ef.evento_id = e.perdedor RETURNING 1
-- ),
-- mv_tor AS (
--   UPDATE torneios t SET evento_id = e.vencedor
--   FROM escolha e WHERE t.evento_id = e.perdedor RETURNING 1
-- ),
-- mv_cbh AS (
--   UPDATE eventos_cbh c SET evento_id = e.vencedor
--   FROM escolha e WHERE c.evento_id = e.perdedor RETURNING 1
-- )
-- UPDATE eventos e SET status = 'fundido', fundido_em = k.vencedor, atualizado_em = now()
-- FROM escolha k WHERE e.id = k.perdedor;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE F — VERIFICAÇÃO + FILAS DE REVISÃO. [LEITURA] Rode no fim.     ║
-- ╚═══════════════════════════════════════════════════════════════════╝
SELECT 'eventos ativos'                       AS metrica, count(*)::text AS valor FROM eventos WHERE status='ativo'
UNION ALL SELECT 'eventos fundidos', count(*)::text FROM eventos WHERE status='fundido'
UNION ALL SELECT 'evento_fontes (menções)', count(*)::text FROM evento_fontes
UNION ALL SELECT 'torneios SEM evento_id (deveria ser 0)', count(*)::text FROM torneios WHERE evento_id IS NULL
UNION ALL SELECT 'eventos_cbh SEM evento_id (deveria ser 0)', count(*)::text FROM eventos_cbh WHERE evento_id IS NULL
UNION ALL SELECT 'eventos SEM federacao_dono (revisar)', count(*)::text FROM eventos WHERE status='ativo' AND federacao_dono IS NULL
UNION ALL SELECT 'linhas na VIEW calendario', count(*)::text FROM calendario;

-- Organizadores do CBH que ainda NÃO mapeiam → adicionar como `variantes` no 023:
SELECT DISTINCT c.federacao AS organizador_sem_mapa, count(*) AS eventos
FROM eventos_cbh c
WHERE c.federacao IS NOT NULL AND map_federacao(c.federacao) IS NULL
GROUP BY c.federacao ORDER BY eventos DESC;
