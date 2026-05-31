-- ═══════════════════════════════════════════════════════════════════
-- Migração 019 — Inscrições de Web Push (notificação com o app fechado)
--
--  • Guarda a PushSubscription de cada dispositivo do usuário (endpoint +
--    chaves p256dh/auth) pra Edge Function 'push-fanout' enviar Web Push
--    quando chega DM nova ou solicitação de seguir.
--  • RLS: cada usuário só enxerga/gerencia as PRÓPRIAS inscrições.
--    A Edge Function lê tudo via service_role (server-side) — nunca o
--    frontend.
--  • endpoint é UNIQUE → o cliente faz upsert(onConflict:'endpoint') e o
--    mesmo aparelho nunca duplica.
--  • Idempotente: pode rodar várias vezes sem erro.
-- ═══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS push_subscriptions (
  id            BIGSERIAL PRIMARY KEY,
  user_id       UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  endpoint      TEXT NOT NULL UNIQUE,
  p256dh        TEXT NOT NULL,
  auth          TEXT NOT NULL,
  user_agent    TEXT,
  criado_em     TIMESTAMPTZ DEFAULT NOW(),
  atualizado_em TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS push_subs_user_idx ON push_subscriptions (user_id);

ALTER TABLE push_subscriptions ENABLE ROW LEVEL SECURITY;

-- SELECT: só as suas inscrições.
DROP POLICY IF EXISTS "push_select" ON push_subscriptions;
CREATE POLICY "push_select" ON push_subscriptions FOR SELECT
  USING (auth.uid() = user_id);

-- INSERT: você só cadastra o SEU dispositivo.
DROP POLICY IF EXISTS "push_insert" ON push_subscriptions;
CREATE POLICY "push_insert" ON push_subscriptions FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- UPDATE: só mexe nas suas (renova chaves do mesmo endpoint).
DROP POLICY IF EXISTS "push_update" ON push_subscriptions;
CREATE POLICY "push_update" ON push_subscriptions FOR UPDATE
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- DELETE: você remove as suas (logout / desinstalou).
DROP POLICY IF EXISTS "push_delete" ON push_subscriptions;
CREATE POLICY "push_delete" ON push_subscriptions FOR DELETE
  USING (auth.uid() = user_id);

SELECT 'OK' AS resultado;
