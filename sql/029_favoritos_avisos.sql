-- ═══════════════════════════════════════════════════════════════════
-- Migração 029 — FAVORITAR torneio + AVISOS (base da notificação)
--
--  OBJETIVO: o usuário marca (estrela) os torneios que vai disputar/acompanhar
--  e recebe PUSH quando sai algo novo deles: programa / adendo / quadro de
--  horário / ordem de entrada. Sem spam — só os favoritos.
--
--  torneios_favoritos: (user, torneio). Cada um vê/gerencia só os seus.
--
--  avisos_torneio: o SCRAPER insere 1 linha quando detecta algo GENUINAMENTE
--  novo (db.py/main.py: doc novo via upsert_documentos, ou ordem 0→N). Um
--  Database Webhook em INSERT dispara a Edge Function push-fanout, que abre em
--  leque pros FAVORITOS daquele torneio. (Não webhook direto em ordem_entrada:
--  ela é delete+reinsert todo ciclo → spam. O scraper é quem decide o "novo".)
--
--  RLS: favoritos = por dono (auth.uid()); avisos = leitura pública, escrita só
--  service_role (scraper). Idempotente.
-- ═══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS torneios_favoritos (
  user_id    UUID    NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  torneio_id INTEGER NOT NULL REFERENCES torneios(id)   ON DELETE CASCADE,
  criado_em  TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (user_id, torneio_id)
);
CREATE INDEX IF NOT EXISTS torneios_favoritos_torneio_idx
  ON torneios_favoritos (torneio_id);   -- push-fanout busca favoritos por torneio

ALTER TABLE torneios_favoritos ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "favoritos_select_own" ON torneios_favoritos;
CREATE POLICY "favoritos_select_own" ON torneios_favoritos FOR SELECT
  USING (auth.uid() = user_id);
DROP POLICY IF EXISTS "favoritos_insert_own" ON torneios_favoritos;
CREATE POLICY "favoritos_insert_own" ON torneios_favoritos FOR INSERT
  WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS "favoritos_delete_own" ON torneios_favoritos;
CREATE POLICY "favoritos_delete_own" ON torneios_favoritos FOR DELETE
  USING (auth.uid() = user_id);


CREATE TABLE IF NOT EXISTS avisos_torneio (
  id         BIGSERIAL PRIMARY KEY,
  torneio_id INTEGER NOT NULL REFERENCES torneios(id) ON DELETE CASCADE,
  tipo       TEXT NOT NULL,        -- 'programa' | 'adendo' | 'horario' | 'ordem' | 'resultado'
  titulo     TEXT,                 -- ex.: nome do doc, ou "Ordem de entrada"
  criado_em  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS avisos_torneio_torneio_idx ON avisos_torneio (torneio_id);

ALTER TABLE avisos_torneio ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "avisos_select_public" ON avisos_torneio;
CREATE POLICY "avisos_select_public" ON avisos_torneio FOR SELECT USING (true);
-- INSERT/UPDATE/DELETE: só service_role (scraper), que ignora RLS.

SELECT 'OK' AS resultado;
