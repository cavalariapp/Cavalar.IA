-- ═══════════════════════════════════════════════════════════════════
-- Migração 017 — Solicitações de seguir (aprovação em perfil privado)
--
--  • follows.status: 'aceito' (default) | 'pendente'.
--    - Perfil PÚBLICO  → seguir entra direto como 'aceito'.
--    - Perfil PRIVADO  → seguir entra como 'pendente'; o dono aprova
--      (UPDATE → 'aceito') ou recusa (DELETE).
--  • RLS reescrito: ninguém se auto-aprova num perfil privado; pendências
--    só são visíveis às duas partes; contadores contam só 'aceito'.
--  • feed_posts_select reescrito: posts de perfil privado (e posts
--    'seguidores' de qualquer perfil) só aparecem para seguidor ACEITO.
-- ═══════════════════════════════════════════════════════════════════

-- ──────────────── COLUNA status ────────────────────────────────────
-- Default 'aceito' → todos os follows já existentes continuam valendo.
ALTER TABLE follows
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'aceito'
  CHECK (status IN ('pendente','aceito'));

-- Acelera a fila de aprovação do dono ("minhas solicitações pendentes").
CREATE INDEX IF NOT EXISTS follows_pendentes_idx
  ON follows (followed_id) WHERE status = 'pendente';

-- ──────────────── RLS: follows ─────────────────────────────────────
-- SELECT: follows aceitos são públicos (contadores); pendências só as
-- duas partes enxergam.
DROP POLICY IF EXISTS "follows_select" ON follows;
CREATE POLICY "follows_select" ON follows FOR SELECT USING (
  status = 'aceito' OR auth.uid() IN (follower_id, followed_id)
);

-- INSERT: você só cria o SEU follow. Só pode nascer 'aceito' se o alvo
-- for público; caso contrário é obrigatoriamente 'pendente' (o cliente
-- não consegue se auto-aprovar num perfil privado).
DROP POLICY IF EXISTS "follows_insert" ON follows;
CREATE POLICY "follows_insert" ON follows FOR INSERT WITH CHECK (
  auth.uid() = follower_id
  AND (
    status = 'pendente'
    OR EXISTS (
      SELECT 1 FROM profiles p
      WHERE p.id = followed_id AND p.visibilidade = 'publico'
    )
  )
);

-- UPDATE: só o DONO do perfil seguido mexe no status (= aprovar).
DROP POLICY IF EXISTS "follows_update" ON follows;
CREATE POLICY "follows_update" ON follows FOR UPDATE
  USING (auth.uid() = followed_id)
  WITH CHECK (auth.uid() = followed_id);

-- DELETE: o seguidor desfaz/cancela; o dono recusa/remove seguidor.
DROP POLICY IF EXISTS "follows_delete" ON follows;
CREATE POLICY "follows_delete" ON follows FOR DELETE USING (
  auth.uid() IN (follower_id, followed_id)
);

-- ──────────────── feed_posts_select reescrito ──────────────────────
-- Regras:
--  1) Dono sempre vê os próprios posts.
--  2) Post 'publico' de perfil 'publico' → todo mundo.
--  3) Seguidor ACEITO vê QUALQUER post de quem segue — cobre os posts
--     'seguidores' (perfil público) E todos os posts de perfil privado.
DROP POLICY IF EXISTS "feed_posts_select" ON feed_posts;
CREATE POLICY "feed_posts_select" ON feed_posts FOR SELECT USING (
  auth.uid() = profile_id
  OR (visibilidade = 'publico' AND EXISTS (
      SELECT 1 FROM profiles p
      WHERE p.id = feed_posts.profile_id AND p.visibilidade = 'publico'
  ))
  OR EXISTS (
      SELECT 1 FROM follows f
      WHERE f.followed_id = feed_posts.profile_id
        AND f.follower_id = auth.uid()
        AND f.status = 'aceito'
  )
);

SELECT 'OK' AS resultado;
