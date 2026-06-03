-- ═══════════════════════════════════════════════════════════════════
-- Migração 026 — Tabela ORDEM DE ENTRADA (Fase C do scraper)
--
--  O QUE É: a ordem em que cada conjunto (cavaleiro + cavalo) entra na
--  pista numa prova. Cada prova tem a SUA ordem, publicada no início do
--  dia (OrdemEntrada.aspx?ID=N na FPH). É a base do recurso futuro
--  "avise cada competidor do horário/pista/ordem e depois do resultado".
--
--  POR QUE TABELA NOVA: não existia nada equivalente no banco (o N8N só
--  gravava `resultados`). Tabela LIMPA — genealogia em coluna própria,
--  sem o formato "NOME\nGENEALOGIA" legado de `resultados`.
--
--  CHAVE / IDEMPOTÊNCIA: o scraper faz DELETE+REINSERT por prova_id
--  (ver db.py:upsert_ordem_entrada) — a ordem é o retrato da prova num
--  momento; re-raspar substitui o conjunto inteiro, fiel à fonte. O
--  índice ÚNICO (prova_id, ordem) é uma trava de integridade. ON DELETE
--  CASCADE: se a prova some, a ordem dela some junto (dado órfão não serve).
--
--  RLS: leitura PÚBLICA (o app é aberto); escrita só via service_role
--  (o scraper), que ignora RLS. Sem policy de insert/update/delete para
--  anon → o frontend não escreve aqui.
--
--  prova_id é INTEGER pra casar com provas.id (mesmo tipo de resultados.prova_id).
--  Idempotente: pode rodar várias vezes sem erro.
-- ═══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ordem_entrada (
  id                 BIGSERIAL PRIMARY KEY,
  prova_id           INTEGER NOT NULL REFERENCES provas(id) ON DELETE CASCADE,
  ordem              INTEGER,            -- posição de entrada (pode ter gaps: retirados)
  cavaleiro_nome     TEXT,
  cavalo_nome        TEXT,
  genealogia         TEXT,               -- coluna própria (tabela nova, sem legado)
  categoria          TEXT,
  pontuacao          TEXT,               -- formato BR preservado (ex.: "19")
  id_cavaleiro_fonte TEXT,               -- id estável do cavaleiro na fonte (match futuro)
  criado_em          TIMESTAMPTZ DEFAULT NOW()
);

-- lookups por prova (o app lê a ordem de UMA prova por vez)
CREATE INDEX IF NOT EXISTS ordem_entrada_prova_idx
  ON ordem_entrada (prova_id);

-- trava de integridade + idempotência do delete+reinsert (uma posição por prova)
CREATE UNIQUE INDEX IF NOT EXISTS ordem_entrada_prova_ordem_uidx
  ON ordem_entrada (prova_id, ordem);

ALTER TABLE ordem_entrada ENABLE ROW LEVEL SECURITY;

-- SELECT: público (app aberto). Escrita fica só com a service_role (bypassa RLS).
DROP POLICY IF EXISTS "ordem_entrada_select" ON ordem_entrada;
CREATE POLICY "ordem_entrada_select" ON ordem_entrada FOR SELECT
  USING (true);

SELECT 'OK' AS resultado;
