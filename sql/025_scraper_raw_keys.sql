-- ═══════════════════════════════════════════════════════════════════
-- 025 — CHAVES ESTÁVEIS + ORGANIZADOR na camada BRUTA (torneios)
--        ⚠ DRAFT — revisar com a Carol antes de rodar ⚠
--
--  Para o scraper de PRODUÇÃO (código em scraper/) escrever de forma
--  IDEMPOTENTE, `torneios` precisa de uma CHAVE ESTÁVEL por fonte. Hoje só
--  existe `fingerprint` (derivado de nome+data) — que MUDA quando a data é
--  corrigida, então não serve de chave de upsert.
--
--  Adiciona:
--    • id_nativo   = ID do evento NA FONTE (ex.: MacroNetwork ListaProvas?ID=
--                    3316). Estável: não muda quando nome/data são corrigidos.
--    • organizador = ID da ENTIDADE organizadora lida do card (ex.: '37744').
--                    Sinal de FANTASMA: federação lista evento cujo organizador
--                    é outra entidade/UF (ex.: FPH listando "SINOP"/MT).
--
--  Chave de upsert: UNIQUE (fonte, id_nativo) — PARCIAL (só onde id_nativo
--  NÃO é nulo), pra NÃO colidir com os 498 torneios antigos (id_nativo NULL,
--  do snapshot congelado). Esses 498 ficam intactos; o resolver (024) faz a
--  reconciliação com o que o scraper novo trouxer.
--
--  100% aditivo: IF NOT EXISTS em tudo. Não destrói nem altera linha existente.
-- ═══════════════════════════════════════════════════════════════════

ALTER TABLE torneios ADD COLUMN IF NOT EXISTS id_nativo   text;
ALTER TABLE torneios ADD COLUMN IF NOT EXISTS organizador text;

COMMENT ON COLUMN torneios.id_nativo   IS 'ID estável do evento na fonte (ex.: MacroNetwork ListaProvas?ID=N). Chave de upsert do scraper.';
COMMENT ON COLUMN torneios.organizador IS 'ID da entidade organizadora lida do card da fonte. Sinal de fantasma quando != dono declarado.';

-- Chave de idempotência do scraper. PARCIAL: ignora as linhas antigas (NULL).
CREATE UNIQUE INDEX IF NOT EXISTS torneios_fonte_idnativo_uidx
  ON torneios (fonte, id_nativo)
  WHERE id_nativo IS NOT NULL;

-- Busca por organizador (detecção de fantasma e agrupamento por entidade).
CREATE INDEX IF NOT EXISTS torneios_organizador_idx
  ON torneios (organizador)
  WHERE organizador IS NOT NULL;

-- ── VERIFICAÇÃO (leitura) ────────────────────────────────────────────
-- Rode depois pra confirmar que as colunas existem e o índice foi criado:
--   SELECT column_name, data_type FROM information_schema.columns
--    WHERE table_name='torneios' AND column_name IN ('id_nativo','organizador');
--   SELECT indexname FROM pg_indexes WHERE tablename='torneios';
