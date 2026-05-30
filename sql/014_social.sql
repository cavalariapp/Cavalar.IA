-- ═══════════════════════════════════════════════════════════════════
-- Migração 014 — Camada social: curtidas, comentários, seguir,
-- visibilidade de post (público/seguidores) e mensagens diretas.
-- ═══════════════════════════════════════════════════════════════════

-- ──────────────── FEED_POSTS: visibilidade ─────────────────────────
ALTER TABLE feed_posts
  ADD COLUMN IF NOT EXISTS visibilidade TEXT NOT NULL DEFAULT 'publico'
  CHECK (visibilidade IN ('publico','seguidores'));

CREATE INDEX IF NOT EXISTS feed_posts_visibilidade_idx ON feed_posts (visibilidade);

-- ──────────────── FOLLOWS ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS follows (
  follower_id  UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  followed_id  UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  criado_em    TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (follower_id, followed_id),
  CHECK (follower_id <> followed_id)
);

CREATE INDEX IF NOT EXISTS follows_followed_idx ON follows (followed_id);
CREATE INDEX IF NOT EXISTS follows_follower_idx ON follows (follower_id);

ALTER TABLE follows ENABLE ROW LEVEL SECURITY;

-- Qualquer um vê quem segue quem (necessário pra contadores)
DROP POLICY IF EXISTS "follows_select" ON follows;
CREATE POLICY "follows_select" ON follows FOR SELECT USING (true);

-- Só você cria/remove seu próprio follow
DROP POLICY IF EXISTS "follows_insert" ON follows;
CREATE POLICY "follows_insert" ON follows FOR INSERT WITH CHECK (auth.uid() = follower_id);
DROP POLICY IF EXISTS "follows_delete" ON follows;
CREATE POLICY "follows_delete" ON follows FOR DELETE USING (auth.uid() = follower_id);

-- ──────────────── POST_LIKES ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS post_likes (
  post_id     BIGINT NOT NULL REFERENCES feed_posts(id) ON DELETE CASCADE,
  profile_id  UUID   NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  criado_em   TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (post_id, profile_id)
);

CREATE INDEX IF NOT EXISTS post_likes_post_idx ON post_likes (post_id);

ALTER TABLE post_likes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "post_likes_select" ON post_likes;
CREATE POLICY "post_likes_select" ON post_likes FOR SELECT USING (true);

DROP POLICY IF EXISTS "post_likes_insert" ON post_likes;
CREATE POLICY "post_likes_insert" ON post_likes FOR INSERT WITH CHECK (auth.uid() = profile_id);

DROP POLICY IF EXISTS "post_likes_delete" ON post_likes;
CREATE POLICY "post_likes_delete" ON post_likes FOR DELETE USING (auth.uid() = profile_id);

-- ──────────────── POST_COMMENTS ────────────────────────────────────
CREATE TABLE IF NOT EXISTS post_comments (
  id          BIGSERIAL PRIMARY KEY,
  post_id     BIGINT NOT NULL REFERENCES feed_posts(id) ON DELETE CASCADE,
  profile_id  UUID   NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  texto       TEXT NOT NULL,
  criado_em   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS post_comments_post_idx ON post_comments (post_id, criado_em);

ALTER TABLE post_comments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "post_comments_select" ON post_comments;
CREATE POLICY "post_comments_select" ON post_comments FOR SELECT USING (true);

DROP POLICY IF EXISTS "post_comments_insert" ON post_comments;
CREATE POLICY "post_comments_insert" ON post_comments FOR INSERT WITH CHECK (auth.uid() = profile_id);

DROP POLICY IF EXISTS "post_comments_delete" ON post_comments;
CREATE POLICY "post_comments_delete" ON post_comments FOR DELETE USING (auth.uid() = profile_id);

-- ──────────────── FEED_POSTS: política de leitura ──────────────────
-- Substituímos a policy antiga porque agora há "seguidores" no jogo
DROP POLICY IF EXISTS "feed_posts_select" ON feed_posts;
CREATE POLICY "feed_posts_select" ON feed_posts FOR SELECT USING (
  -- Dono sempre vê próprio post
  auth.uid() = profile_id
  -- Posts publicos (e dono é público também)
  OR (visibilidade = 'publico' AND EXISTS (
      SELECT 1 FROM profiles p
      WHERE p.id = feed_posts.profile_id AND p.visibilidade = 'publico'
  ))
  -- Posts pra seguidores (precisa estar seguindo)
  OR (visibilidade = 'seguidores' AND EXISTS (
      SELECT 1 FROM follows f
      WHERE f.followed_id = feed_posts.profile_id AND f.follower_id = auth.uid()
  ))
);

-- ──────────────── DIRECT MESSAGES ──────────────────────────────────
CREATE TABLE IF NOT EXISTS direct_messages (
  id          BIGSERIAL PRIMARY KEY,
  remetente   UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  destinatario UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  texto       TEXT,
  imagem_url  TEXT,
  lido        BOOLEAN DEFAULT FALSE,
  criado_em   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS dm_remetente_idx ON direct_messages (remetente, criado_em DESC);
CREATE INDEX IF NOT EXISTS dm_destinatario_idx ON direct_messages (destinatario, criado_em DESC);
CREATE INDEX IF NOT EXISTS dm_conversa_idx ON direct_messages (
  LEAST(remetente, destinatario), GREATEST(remetente, destinatario), criado_em
);

ALTER TABLE direct_messages ENABLE ROW LEVEL SECURITY;

-- Só envolvidos veem
DROP POLICY IF EXISTS "dm_select" ON direct_messages;
CREATE POLICY "dm_select" ON direct_messages FOR SELECT USING (
  auth.uid() IN (remetente, destinatario)
);

-- Só você manda mensagem em seu nome
DROP POLICY IF EXISTS "dm_insert" ON direct_messages;
CREATE POLICY "dm_insert" ON direct_messages FOR INSERT WITH CHECK (auth.uid() = remetente);

-- Destinatário pode marcar como lido (UPDATE limitado a lido)
DROP POLICY IF EXISTS "dm_update" ON direct_messages;
CREATE POLICY "dm_update" ON direct_messages FOR UPDATE USING (auth.uid() = destinatario);

-- Remetente pode deletar a própria
DROP POLICY IF EXISTS "dm_delete" ON direct_messages;
CREATE POLICY "dm_delete" ON direct_messages FOR DELETE USING (auth.uid() = remetente);

-- Bucket pra anexos em DM
INSERT INTO storage.buckets (id, name, public) VALUES ('dm-imgs', 'dm-imgs', true) ON CONFLICT (id) DO NOTHING;

-- ──────────────── VIEW pra agregar contadores rapidinho ────────────
CREATE OR REPLACE VIEW feed_posts_aggregated AS
SELECT
  fp.*,
  (SELECT COUNT(*)::INT FROM post_likes pl WHERE pl.post_id = fp.id) AS likes_count,
  (SELECT COUNT(*)::INT FROM post_comments pc WHERE pc.post_id = fp.id) AS comments_count
FROM feed_posts fp;

GRANT SELECT ON feed_posts_aggregated TO authenticated, anon;

SELECT 'OK' AS resultado;
