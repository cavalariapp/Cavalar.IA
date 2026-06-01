-- ═══════════════════════════════════════════════════════════════════
-- Diagnóstico 020 — AUDITORIA do calendário/scraping (SOMENTE LEITURA)
--
--  • NÃO altera NADA no banco. Só SELECTs. Pode rodar à vontade.
--  • Objetivo: medir o TAMANHO REAL dos problemas antes de construir o
--    modelo canônico de eventos + resolver de duplicatas.
--  • Mede: volume por tabela, completude dos torneios, cobertura do CBH,
--    candidatos a duplicata (cross-fonte e intra-fonte), shells/fantasmas,
--    órfãos do calendário e saúde dos fingerprints.
--
--  COMO RODAR (Supabase → SQL Editor):
--    1) Rode a PARTE 1 (painel de métricas) e me cole a tabela.
--    2) Rode a PARTE 2 (saúde por fonte) e me cole a tabela.
--    3) PARTE 3 (amostras) é opcional — rode UM bloco por vez,
--       selecionando o bloco e clicando RUN, se quisermos ver exemplos.
--
--  Definição de "sobreposição de datas" usada em todo o arquivo:
--    [a_ini,a_fim] e [b_ini,b_fim] se sobrepõem  ⇔
--      a_ini <= b_fim  E  b_ini <= a_fim
--    (data_fim ausente → usa data_inicio; ambas ausentes → ignorado)
-- ═══════════════════════════════════════════════════════════════════


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE 1 — PAINEL DE MÉTRICAS (rode tudo de uma vez, cole o result) ║
-- ╚═══════════════════════════════════════════════════════════════════╝
SELECT 1  AS ord, 'A · Volume'              AS secao, 'Torneios (total)'                              AS metrica, (SELECT count(*) FROM torneios)                                                                                                  AS valor
UNION ALL SELECT 2,  'A · Volume',          'eventos_cbh (total)',                          (SELECT count(*) FROM eventos_cbh)
UNION ALL SELECT 3,  'A · Volume',          'Provas (total)',                               (SELECT count(*) FROM provas)
UNION ALL SELECT 4,  'A · Volume',          'Resultados (total)',                           (SELECT count(*) FROM resultados)
UNION ALL SELECT 5,  'A · Volume',          'Documentos (total)',                           (SELECT count(*) FROM torneio_documentos)

UNION ALL SELECT 6,  'B · Torneios',        'Com provas (têm resultados)',                  (SELECT count(*) FROM torneios t WHERE EXISTS (SELECT 1 FROM provas p WHERE p.torneio_id = t.id))
UNION ALL SELECT 7,  'B · Torneios',        'SEM provas',                                   (SELECT count(*) FROM torneios t WHERE NOT EXISTS (SELECT 1 FROM provas p WHERE p.torneio_id = t.id))
UNION ALL SELECT 8,  'B · Torneios',        'Com >=1 documento',                            (SELECT count(*) FROM torneios t WHERE EXISTS (SELECT 1 FROM torneio_documentos d WHERE d.torneio_id = t.id))
UNION ALL SELECT 9,  'B · Torneios',        'SEM documento',                                (SELECT count(*) FROM torneios t WHERE NOT EXISTS (SELECT 1 FROM torneio_documentos d WHERE d.torneio_id = t.id))
UNION ALL SELECT 10, 'B · Torneios',        'Linkados ao CBH (evento_cbh_id)',              (SELECT count(*) FROM torneios WHERE evento_cbh_id IS NOT NULL)
UNION ALL SELECT 11, 'B · Torneios',        'SEM link CBH',                                 (SELECT count(*) FROM torneios WHERE evento_cbh_id IS NULL)
UNION ALL SELECT 12, 'B · Torneios',        'SHELLS suspeitos (sem provas E sem link CBH)', (SELECT count(*) FROM torneios t WHERE evento_cbh_id IS NULL AND NOT EXISTS (SELECT 1 FROM provas p WHERE p.torneio_id = t.id))
UNION ALL SELECT 13, 'B · Torneios',        'Fingerprint NULO',                             (SELECT count(*) FROM torneios WHERE fingerprint IS NULL)

UNION ALL SELECT 14, 'C · CBH cobertura',   'Cobertos por FK (torneio aponta p/ ele)',      (SELECT count(*) FROM eventos_cbh c WHERE EXISTS (SELECT 1 FROM torneios t WHERE t.evento_cbh_id = c.id))
UNION ALL SELECT 15, 'C · CBH cobertura',   'Cobertos por fingerprint',                     (SELECT count(*) FROM eventos_cbh c WHERE c.fingerprint IS NOT NULL AND EXISTS (SELECT 1 FROM torneios t WHERE t.fingerprint = c.fingerprint))
UNION ALL SELECT 16, 'C · CBH cobertura',   'ORFAOS (sem FK e sem fingerprint batendo)',    (SELECT count(*) FROM eventos_cbh c WHERE NOT EXISTS (SELECT 1 FROM torneios t WHERE t.evento_cbh_id = c.id) AND NOT EXISTS (SELECT 1 FROM torneios t WHERE t.fingerprint IS NOT NULL AND t.fingerprint = c.fingerprint))
UNION ALL SELECT 17, 'C · CBH cobertura',   'Fingerprint NULO',                             (SELECT count(*) FROM eventos_cbh WHERE fingerprint IS NULL)

UNION ALL SELECT 18, 'D · Duplicatas',      'Pares CROSS-fonte com datas sobrepostas',      (SELECT count(*) FROM torneios a JOIN torneios b ON a.id < b.id AND a.fonte IS DISTINCT FROM b.fonte AND COALESCE(a.data_inicio,a.data_fim) <= COALESCE(b.data_fim,b.data_inicio) AND COALESCE(b.data_inicio,b.data_fim) <= COALESCE(a.data_fim,a.data_inicio))
UNION ALL SELECT 19, 'D · Duplicatas',      'Pares MESMA-fonte com datas sobrepostas',      (SELECT count(*) FROM torneios a JOIN torneios b ON a.id < b.id AND a.fonte = b.fonte AND COALESCE(a.data_inicio,a.data_fim) <= COALESCE(b.data_fim,b.data_inicio) AND COALESCE(b.data_inicio,b.data_fim) <= COALESCE(a.data_fim,a.data_inicio))
UNION ALL SELECT 20, 'D · Duplicatas',      'Grupos de fingerprint repetido (>1 torneio)',  (SELECT count(*) FROM (SELECT fingerprint FROM torneios WHERE fingerprint IS NOT NULL GROUP BY fingerprint HAVING count(*) > 1) x)
UNION ALL SELECT 21, 'D · Duplicatas',      'CBH sem FK mas com torneio sobrepondo data',   (SELECT count(*) FROM eventos_cbh c WHERE NOT EXISTS (SELECT 1 FROM torneios t WHERE t.evento_cbh_id = c.id) AND EXISTS (SELECT 1 FROM torneios t WHERE COALESCE(t.data_inicio,t.data_fim) <= COALESCE(c.data_fim,c.data_inicio) AND COALESCE(c.data_inicio,c.data_fim) <= COALESCE(t.data_fim,t.data_inicio) AND (t.fingerprint IS NULL OR t.fingerprint IS DISTINCT FROM c.fingerprint)))

UNION ALL SELECT 22, 'E · Datas faltando',  'Torneios sem data_inicio',                     (SELECT count(*) FROM torneios   WHERE data_inicio IS NULL)
UNION ALL SELECT 23, 'E · Datas faltando',  'eventos_cbh sem data_inicio',                  (SELECT count(*) FROM eventos_cbh WHERE data_inicio IS NULL)
ORDER BY ord;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE 2 — SAÚDE POR FONTE (federação)                             ║
-- ║ Mostra: volume, quantos têm provas/docs, link CBH, janela de datas ║
-- ║ coberta e o documento mais recente (= a fonte está "viva"?).       ║
-- ╚═══════════════════════════════════════════════════════════════════╝
SELECT
  t.fonte,
  count(*)                                                AS torneios,
  count(*) FILTER (WHERE pr.cnt > 0)                      AS com_provas,
  count(*) FILTER (WHERE dc.cnt > 0)                      AS com_docs,
  count(*) FILTER (WHERE t.evento_cbh_id IS NOT NULL)     AS linkados_cbh,
  count(*) FILTER (WHERE t.fingerprint IS NULL)           AS sem_fingerprint,
  min(t.data_inicio)                                      AS primeiro_evento,
  max(t.data_inicio)                                      AS ultimo_evento,
  max(dc.ultimo_doc)                                      AS doc_mais_recente
FROM torneios t
LEFT JOIN LATERAL (SELECT count(*) AS cnt FROM provas p WHERE p.torneio_id = t.id) pr ON true
LEFT JOIN LATERAL (SELECT count(*) AS cnt, max(criado_em) AS ultimo_doc FROM torneio_documentos d WHERE d.torneio_id = t.id) dc ON true
GROUP BY t.fonte
ORDER BY torneios DESC;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE 3 — AMOSTRAS (opcional). Rode UM bloco por vez (selecione e   ║
-- ║ clique RUN). São listas pra eyeball, não métricas.                 ║
-- ╚═══════════════════════════════════════════════════════════════════╝

-- S1 · Candidatos a DUPLICATA cross-fonte (inclui falsos positivos — é a
--      "pilha" que o resolver vai filtrar por local+nome). 50 mais recentes.
SELECT a.data_inicio AS data, a.fonte AS fonte_a, a.nome AS torneio_a,
       b.fonte AS fonte_b, b.nome AS torneio_b
FROM torneios a
JOIN torneios b
  ON a.id < b.id
 AND a.fonte IS DISTINCT FROM b.fonte
 AND COALESCE(a.data_inicio,a.data_fim) <= COALESCE(b.data_fim,b.data_inicio)
 AND COALESCE(b.data_inicio,b.data_fim) <= COALESCE(a.data_fim,a.data_inicio)
ORDER BY a.data_inicio DESC NULLS LAST
LIMIT 50;

-- S2 · SHELLS / fantasmas: torneio sem provas E sem link CBH (suspeito de
--      "federação re-listando evento de outra"). Olhar a coluna fonte.
SELECT t.fonte, t.data_inicio, t.data_fim, t.nome
FROM torneios t
WHERE t.evento_cbh_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM provas p WHERE p.torneio_id = t.id)
ORDER BY t.data_inicio DESC NULLS LAST
LIMIT 50;

-- S3 · ÓRFÃOS do CBH: no calendário oficial mas nenhuma federação publicou
--      (nem FK nem fingerprint). Completude do calendário.
SELECT c.data_inicio, c.federacao, c.estado, c.evento, c.local
FROM eventos_cbh c
WHERE NOT EXISTS (SELECT 1 FROM torneios t WHERE t.evento_cbh_id = c.id)
  AND NOT EXISTS (SELECT 1 FROM torneios t WHERE t.fingerprint IS NOT NULL AND t.fingerprint = c.fingerprint)
ORDER BY c.data_inicio DESC NULLS LAST
LIMIT 50;

-- S4 · LINK FALTANTE: evento CBH sem FK, mas existe torneio sobrepondo data
--      e com fingerprint diferente (= o resolver deveria ter casado e não casou).
SELECT c.data_inicio AS data_cbh, c.federacao, c.evento AS evento_cbh,
       t.fonte, t.nome AS torneio_sobreposto, t.data_inicio AS data_torneio
FROM eventos_cbh c
JOIN torneios t
  ON COALESCE(t.data_inicio,t.data_fim) <= COALESCE(c.data_fim,c.data_inicio)
 AND COALESCE(c.data_inicio,c.data_fim) <= COALESCE(t.data_fim,t.data_inicio)
WHERE NOT EXISTS (SELECT 1 FROM torneios t2 WHERE t2.evento_cbh_id = c.id)
  AND (t.fingerprint IS NULL OR t.fingerprint IS DISTINCT FROM c.fingerprint)
ORDER BY c.data_inicio DESC NULLS LAST
LIMIT 50;

-- S5 · FINGERPRINTS repetidos: mesmo fingerprint em >1 torneio (duplicata já
--      "colada" pelo scraper, ou colisão de hash). Ver fontes e nomes.
SELECT t.fingerprint, count(*) AS qtd,
       string_agg(DISTINCT t.fonte, ', ') AS fontes,
       string_agg(t.nome, '  ||  ' ORDER BY t.nome) AS nomes
FROM torneios t
WHERE t.fingerprint IS NOT NULL
GROUP BY t.fingerprint
HAVING count(*) > 1
ORDER BY qtd DESC
LIMIT 50;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE 4 — DUPLICATAS REAIS (chave normalizada, ignora fingerprint)  ║
-- ║ Por que: o fingerprint REMOVE o ordinal ("1ª","2ª") e cola etapas   ║
-- ║ distintas de uma série. Aqui a chave MANTÉM os dígitos e remove só   ║
-- ║ caixa/acento/pontuação/espaço → mesma fonte + mesma chave = MESMO    ║
-- ║ evento raspado 2x (duplicata de verdade). Séries (1ª,2ª,3ª) NÃO      ║
-- ║ colapsam, porque o dígito do ordinal entra na chave.                ║
-- ╚═══════════════════════════════════════════════════════════════════╝
SELECT fonte,
       count(*)                        AS qtd,
       min(nome)                       AS exemplo,
       array_agg(id ORDER BY id)       AS ids,
       array_agg(DISTINCT data_inicio) AS datas
FROM (
  SELECT id, fonte, nome, data_inicio,
         regexp_replace(lower(nome), '[^a-z0-9]', '', 'g') AS chave
  FROM torneios
) x
GROUP BY fonte, chave
HAVING count(*) > 1
ORDER BY qtd DESC, fonte
LIMIT 100;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE 5 — TRIAGEM DOS 194 SHELLS (sem provas E sem link CBH)         ║
-- ║ Separa: futuro (normal, ainda sem resultado) × passado (falha de     ║
-- ║ resultado OU fantasma) × sem data (não dá pra classificar).         ║
-- ╚═══════════════════════════════════════════════════════════════════╝
SELECT
  CASE
    WHEN t.data_inicio IS NULL                                   THEN '3 · sem data (não classificável)'
    WHEN COALESCE(t.data_fim, t.data_inicio) < CURRENT_DATE      THEN '2 · passado SEM resultado (falha/fantasma)'
    ELSE                                                              '1 · futuro/em andamento (normal)'
  END           AS situacao,
  count(*)      AS shells,
  string_agg(DISTINCT t.fonte, ', ' ORDER BY t.fonte) AS fontes
FROM torneios t
WHERE t.evento_cbh_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM provas p WHERE p.torneio_id = t.id)
GROUP BY situacao
ORDER BY situacao;
