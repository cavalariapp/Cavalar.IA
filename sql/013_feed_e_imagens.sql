-- ═══════════════════════════════════════════════════════════════════
-- Migração 013 — Imagens de perfil + Feed pessoal/global
--
-- Adiciona avatar/capa em profiles, cria tabela feed_posts e configura
-- buckets de Storage (avatars, covers, feed-imgs) com policies.
-- ═══════════════════════════════════════════════════════════════════

-- ───────────────── PROFILES — imagens ──────────────────────────────
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS avatar_url TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS capa_url TEXT;

-- ───────────────── FEED POSTS ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS feed_posts (
  id            BIGSERIAL PRIMARY KEY,
  profile_id    UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  texto         TEXT,
  imagem_url    TEXT,
  tipo          TEXT NOT NULL DEFAULT 'recado' CHECK (tipo IN ('recado','anuncio','foto')),
  criado_em     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS feed_posts_profile_idx ON feed_posts (profile_id, criado_em DESC);
CREATE INDEX IF NOT EXISTS feed_posts_criado_em_idx ON feed_posts (criado_em DESC);

ALTER TABLE feed_posts ENABLE ROW LEVEL SECURITY;

-- Posts visíveis se o dono é público OU é você mesmo
DROP POLICY IF EXISTS "feed_posts_select" ON feed_posts;
CREATE POLICY "feed_posts_select" ON feed_posts FOR SELECT USING (
  EXISTS (
    SELECT 1 FROM profiles p
    WHERE p.id = feed_posts.profile_id
      AND (p.visibilidade = 'publico' OR auth.uid() = p.id)
  )
);

-- Só o dono escreve/edita/deleta
DROP POLICY IF EXISTS "feed_posts_write" ON feed_posts;
CREATE POLICY "feed_posts_write" ON feed_posts FOR ALL USING (auth.uid() = profile_id);

-- ───────────────── STORAGE BUCKETS ──────────────────────────────────
-- (Buckets públicos pra leitura, escrita só pelo dono)
INSERT INTO storage.buckets (id, name, public) VALUES ('avatars', 'avatars', true) ON CONFLICT (id) DO NOTHING;
INSERT INTO storage.buckets (id, name, public) VALUES ('covers',  'covers',  true) ON CONFLICT (id) DO NOTHING;
INSERT INTO storage.buckets (id, name, public) VALUES ('feed-imgs', 'feed-imgs', true) ON CONFLICT (id) DO NOTHING;

-- Storage policies: qualquer um lê
DROP POLICY IF EXISTS "Public read of profile images" ON storage.objects;
CREATE POLICY "Public read of profile images" ON storage.objects
FOR SELECT USING (bucket_id IN ('avatars','covers','feed-imgs'));

-- Storage policies: usuário só sobe arquivo se o nome começar com o uid dele
-- (convenção: avatars/<uid>/file.jpg)
DROP POLICY IF EXISTS "Users upload own avatar" ON storage.objects;
CREATE POLICY "Users upload own avatar" ON storage.objects
FOR INSERT TO authenticated WITH CHECK (
  bucket_id IN ('avatars','covers','feed-imgs')
  AND (storage.foldername(name))[1] = auth.uid()::text
);

DROP POLICY IF EXISTS "Users update own files" ON storage.objects;
CREATE POLICY "Users update own files" ON storage.objects
FOR UPDATE TO authenticated USING (
  bucket_id IN ('avatars','covers','feed-imgs')
  AND (storage.foldername(name))[1] = auth.uid()::text
);

DROP POLICY IF EXISTS "Users delete own files" ON storage.objects;
CREATE POLICY "Users delete own files" ON storage.objects
FOR DELETE TO authenticated USING (
  bucket_id IN ('avatars','covers','feed-imgs')
  AND (storage.foldername(name))[1] = auth.uid()::text
);

SELECT 'OK' AS resultado;
