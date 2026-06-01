-- ═══════════════════════════════════════════════════════════════════
-- 023 — SEED do REGISTRO de federações/clubes  ⚠ DRAFT — RODAR DEPOIS DO 022 ⚠
--
--  Popula a tabela `federacoes` (criada no 022). É o DICIONÁRIO que o
--  resolver (024) usa para:
--    • mapear o texto cru do organizador (ex.: eventos_cbh.federacao =
--      "FEDERACAO HIPICA DO MATO GROSSO") ao código do dono (FHIMT);
--    • saber a PLATAFORMA de cada fonte (macronetwork|wordpress|cbh) →
--      qual adaptador o N8N usa pra extrair programa/quadro/resultado.
--
--  ┌─────────────────────────────────────────────────────────────────┐
--  │ REGRA CRÍTICA: `codigo` PRECISA BATER EXATAMENTE com torneios.fonte │
--  │ senão o resolver não casa a federação. Rode a PARTE 0 primeiro,    │
--  │ confira os códigos reais e ajuste o seed antes de rodar a PARTE 1. │
--  └─────────────────────────────────────────────────────────────────┘
--
--  Idempotente: ON CONFLICT (codigo) DO UPDATE. Pode rodar quantas vezes.
--  Campos marcados "-- VERIFICAR" são CHUTE meu (UF/plataforma) — Carol
--  confirma na revisão. Onde não sei, deixei NULL de propósito.
-- ═══════════════════════════════════════════════════════════════════


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE 0 — DESCOBRIR os códigos reais. [LEITURA] Rode e me cole.     ║
-- ║ Estes são os valores EXATOS de torneios.fonte que o seed precisa    ║
-- ║ cobrir. Se aparecer um código que não está no seed abaixo → me avise.║
-- ╚═══════════════════════════════════════════════════════════════════╝
SELECT t.fonte                                   AS codigo,
       count(*)                                  AS torneios,
       min(t.data_inicio)                        AS primeiro,
       max(t.data_inicio)                        AS ultimo
FROM torneios t
GROUP BY t.fonte
ORDER BY torneios DESC;

-- E os nomes EXATOS de organizador que o CBH usa (vira `variantes`):
SELECT c.federacao                               AS organizador_cbh,
       count(*)                                  AS eventos,
       string_agg(DISTINCT c.estado, ', ')       AS ufs
FROM eventos_cbh c
WHERE c.federacao IS NOT NULL
GROUP BY c.federacao
ORDER BY eventos DESC;


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE 1 — SEED CONFIRMADO. [ALTERA: insere/atualiza federacoes]     ║
-- ║ Só federações/clubes que JÁ investigamos ao vivo nas fontes.        ║
-- ╚═══════════════════════════════════════════════════════════════════╝
INSERT INTO federacoes (codigo, nome, variantes, uf, tipo, site, plataforma, tenant_macro) VALUES
  ('CBH',   'Confederação Brasileira de Hipismo',
            ARRAY['CBH','CONFEDERACAO BRASILEIRA DE HIPISMO'],
            NULL, 'confederacao', 'https://cbh.org.br', 'cbh', NULL),

  ('FPH',   'Federação Paulista de Hipismo',
            ARRAY['FPH','FEDERACAO PAULISTA DE HIPISMO'],
            'SP', 'federacao', NULL, 'macronetwork', NULL),

  ('FEERJ', 'Federação de Esportes Equestres do Estado do Rio de Janeiro',
            ARRAY['FEERJ','FEDERACAO DE ESPORTES EQUESTRES DO ESTADO DO RIO DE JANEIRO'],
            'RJ', 'federacao', NULL, 'macronetwork', NULL),

  ('FHIMT', 'Federação Hípica de Mato Grosso',
            ARRAY['FHIMT','FEDERACAO HIPICA DO MATO GROSSO','FEDERACAO HIPICA DE MATO GROSSO'],
            'MT', 'federacao', NULL, 'macronetwork', NULL),

  ('FGEE',  'Federação Gaúcha de Esportes Equestres',
            ARRAY['FGEE','FEDERACAO GAUCHA DE ESPORTES EQUESTRES'],
            'RS', 'federacao', 'https://fgee.com.br', 'wordpress', NULL),  -- VERIFICAR plataforma (pode ter mudado)

  ('CHSA',  'Clube Hípico de Santo Amaro',
            ARRAY['CHSA','CLUBE HIPICO DE SANTO AMARO'],
            'SP', 'clube', 'https://chsa.com.br', 'wordpress', 'chsa-inscricao'),  -- site WP + backend macronetwork (tenant)

  ('SHB',   'Sociedade Hípica Brasileira',
            ARRAY['SHB','SOCIEDADE HIPICA BRASILEIRA'],
            'RJ', 'clube', 'https://shb.com.br', 'wordpress', NULL)  -- VERIFICAR backend de provas
ON CONFLICT (codigo) DO UPDATE SET
  nome         = EXCLUDED.nome,
  variantes    = EXCLUDED.variantes,
  uf           = EXCLUDED.uf,
  tipo         = EXCLUDED.tipo,
  site         = COALESCE(EXCLUDED.site, federacoes.site),
  plataforma   = COALESCE(EXCLUDED.plataforma, federacoes.plataforma),
  tenant_macro = COALESCE(EXCLUDED.tenant_macro, federacoes.tenant_macro);


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE 2 — SEED A VERIFICAR. [ALTERA] UF/plataforma são CHUTE meu.   ║
-- ║ Mantém o registro completo (todas as fontes vistas) pra o resolver   ║
-- ║ não deixar nenhuma federação "órfã". Carol corrige nome/UF na revisão.║
-- ║ Plataforma deixei 'macronetwork' como PALPITE (maioria usa), exceto   ║
-- ║ FCH que já roda Selenium (pode ser engine diferente).               ║
-- ╚═══════════════════════════════════════════════════════════════════╝
INSERT INTO federacoes (codigo, nome, uf, tipo, plataforma) VALUES
  ('FAH',     'Federação (AH) — VERIFICAR nome',                 NULL, 'federacao', 'macronetwork'),  -- VERIFICAR UF
  ('FHBR',    'Federação Hípica de Brasília — VERIFICAR',         'DF', 'federacao', 'macronetwork'),  -- VERIFICAR
  ('FPRH',    'Federação Paranaense de Hipismo — VERIFICAR',      'PR', 'federacao', 'macronetwork'),  -- VERIFICAR
  ('FE-CE',   'Federação Equestre do Ceará — VERIFICAR',          'CE', 'federacao', 'macronetwork'),  -- VERIFICAR
  ('SHPR',    'Sociedade Hípica Paranaense — VERIFICAR',          'PR', 'clube',     'macronetwork'),  -- VERIFICAR
  ('FEPA',    'Federação Equestre do Pará — VERIFICAR',           'PA', 'federacao', 'macronetwork'),  -- VERIFICAR
  ('FE-PE',   'Federação Equestre de Pernambuco — VERIFICAR',     'PE', 'federacao', 'macronetwork'),  -- VERIFICAR
  ('FSMH',    'Federação Sul-Mato-Grossense de Hipismo — VERIFICAR','MS','federacao', 'macronetwork'), -- VERIFICAR
  ('FHMG',    'Federação Hípica de Minas Gerais — VERIFICAR',     'MG', 'federacao', 'macronetwork'),  -- VERIFICAR
  ('FCH',     'Federação Catarinense de Hipismo — VERIFICAR',     'SC', 'federacao', NULL),            -- VERIFICAR (Selenium → engine?)
  ('FEHGO',   'Federação de Esportes Hípicos de Goiás — VERIFICAR','GO','federacao', 'macronetwork'),  -- VERIFICAR
  ('FHB-INS', 'Portal de inscrições FHB — VERIFICAR',             NULL, 'federacao', 'macronetwork')   -- VERIFICAR (relação com FHBR?)
ON CONFLICT (codigo) DO NOTHING;   -- não sobrescreve o que Carol já tiver corrigido


-- ╔═══════════════════════════════════════════════════════════════════╗
-- ║ PARTE 3 — CONFERIR cobertura. [LEITURA]                            ║
-- ║ Toda fonte de torneios PRECISA ter um registro em federacoes, senão  ║
-- ║ o resolver não sabe o dono. 'FALTA SEED' = adicionar na PARTE 1/2.   ║
-- ╚═══════════════════════════════════════════════════════════════════╝
SELECT t.fonte                                          AS codigo,
       count(*)                                         AS torneios,
       CASE WHEN f.codigo IS NULL THEN '⚠ FALTA SEED' ELSE 'ok' END AS no_registro,
       f.nome, f.uf, f.plataforma
FROM torneios t
LEFT JOIN federacoes f ON f.codigo = t.fonte
GROUP BY t.fonte, f.codigo, f.nome, f.uf, f.plataforma
ORDER BY no_registro, torneios DESC;
