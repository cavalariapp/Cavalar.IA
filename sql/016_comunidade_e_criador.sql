-- ═══════════════════════════════════════════════════════════════════
-- Migração 016 — Comunidade pública por padrão + Área do Criador
--
--  A) Privacidade: perfil novo nasce PÚBLICO; view profiles_publicos
--     (SECURITY DEFINER) expõe campos seguros de TODOS os perfis pra
--     comunidade — SEM vazar email/celular/idade. Páginas de perfis
--     privados ficam "só para seguidores" (gate no front).
--  B) criador_animais: matrizes / garanhões / produtos da criação.
--  C) Bucket criador-imgs + storage policies (mesmo padrão da 013).
-- ═══════════════════════════════════════════════════════════════════

-- ──────────────── A) PRIVACIDADE ────────────────────────────────────
-- Novo perfil nasce público (usuário decide depois se quer privar)
ALTER TABLE profiles ALTER COLUMN visibilidade SET DEFAULT 'publico';

-- View pública: mostra TODOS os perfis na comunidade sem mexer no RLS
-- de profiles. Como a view não tem security_invoker, roda como owner
-- (postgres) e ignora o RLS da tabela base — então perfis privados
-- também aparecem na listagem. NÃO expõe email, celular nem idade.
CREATE OR REPLACE VIEW profiles_publicos AS
SELECT
  id,
  nome_completo,
  avatar_url,
  capa_url,
  bio,
  tipos,
  estado,
  pais,
  instagram,
  website,
  visibilidade,
  cavaleiro_nome,
  cavaleiro_match_confirmado,
  analise_config
FROM profiles;

GRANT SELECT ON profiles_publicos TO anon, authenticated;

-- Correção de privacidade do FEED: a view feed_posts_aggregated foi
-- criada sem security_invoker, então rodava como dono (postgres) e
-- IGNORAVA o RLS de feed_posts — vazando posts "seguidores" e de perfis
-- privados pra qualquer um. Com security_invoker, a view passa a honrar
-- a policy feed_posts_select (o que o app já assumia). Posts públicos de
-- perfis públicos continuam visíveis; o resto fica restrito corretamente.
ALTER VIEW feed_posts_aggregated SET (security_invoker = true);

-- ──────────────── B) CRIADOR_ANIMAIS ───────────────────────────────
-- Animais da criação: matriz (égua), garanhão e produto.
CREATE TABLE IF NOT EXISTS criador_animais (
  id            BIGSERIAL PRIMARY KEY,
  profile_id    UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  tipo          TEXT NOT NULL CHECK (tipo IN ('matriz','garanhao','produto')),
  nome          TEXT NOT NULL,
  raca          TEXT,
  pai           TEXT,           -- garanhão (pai)
  mae           TEXT,
  avo_materno   TEXT,           -- avô materno
  sobre         TEXT,           -- texto aberto: o animal e a linha materna
  fotos         TEXT[] NOT NULL DEFAULT '{}',
  mostrar_stats BOOLEAN NOT NULL DEFAULT false,
  criado_em     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS criador_animais_profile_idx
  ON criador_animais (profile_id, tipo);

ALTER TABLE criador_animais ENABLE ROW LEVEL SECURITY;

-- Qualquer um lê (aparece na página pública do criador)
DROP POLICY IF EXISTS "criador_animais_select" ON criador_animais;
CREATE POLICY "criador_animais_select" ON criador_animais
  FOR SELECT USING (true);

-- Só o dono insere/edita/remove
DROP POLICY IF EXISTS "criador_animais_insert" ON criador_animais;
CREATE POLICY "criador_animais_insert" ON criador_animais
  FOR INSERT WITH CHECK (auth.uid() = profile_id);

DROP POLICY IF EXISTS "criador_animais_update" ON criador_animais;
CREATE POLICY "criador_animais_update" ON criador_animais
  FOR UPDATE USING (auth.uid() = profile_id);

DROP POLICY IF EXISTS "criador_animais_delete" ON criador_animais;
CREATE POLICY "criador_animais_delete" ON criador_animais
  FOR DELETE USING (auth.uid() = profile_id);

-- ──────────────── C) BUCKET criador-imgs ───────────────────────────
INSERT INTO storage.buckets (id, name, public)
  VALUES ('criador-imgs', 'criador-imgs', true) ON CONFLICT (id) DO NOTHING;

-- Leitura pública
DROP POLICY IF EXISTS "Public read of criador images" ON storage.objects;
CREATE POLICY "Public read of criador images" ON storage.objects
  FOR SELECT USING (bucket_id = 'criador-imgs');

-- Upload/edição/remoção só pelo dono (convenção: criador-imgs/<uid>/file.jpg)
DROP POLICY IF EXISTS "Users upload own criador imgs" ON storage.objects;
CREATE POLICY "Users upload own criador imgs" ON storage.objects
  FOR INSERT TO authenticated WITH CHECK (
    bucket_id = 'criador-imgs'
    AND (storage.foldername(name))[1] = auth.uid()::text
  );

DROP POLICY IF EXISTS "Users update own criador imgs" ON storage.objects;
CREATE POLICY "Users update own criador imgs" ON storage.objects
  FOR UPDATE TO authenticated USING (
    bucket_id = 'criador-imgs'
    AND (storage.foldername(name))[1] = auth.uid()::text
  );

DROP POLICY IF EXISTS "Users delete own criador imgs" ON storage.objects;
CREATE POLICY "Users delete own criador imgs" ON storage.objects
  FOR DELETE TO authenticated USING (
    bucket_id = 'criador-imgs'
    AND (storage.foldername(name))[1] = auth.uid()::text
  );

SELECT 'OK' AS resultado;
