-- ═══════════════════════════════════════════════════════════════════
-- Migração 015 — Análise de desempenho na página do usuário
--
--  • Funções de normalização de nomes (cavalo / cavaleiro) idênticas
--    à lógica JS de resultados.html (parseCavalo / nomeCavaleiro).
--  • profiles.analise_config (JSONB) — filtros que o cavaleiro escolhe expor.
--  • profile_cavalos — cavalos que criadores/proprietários vinculam ao perfil.
--  • RPCs: buscar_cavalos (autocomplete), stats_cavaleiro_filtrado,
--    stats_cavalo — todos SECURITY DEFINER (resultados são dados públicos).
-- ═══════════════════════════════════════════════════════════════════

-- ──────────────── NORMALIZAÇÃO DE NOMES ────────────────────────────
-- Nome do cavalo = 1ª linha; se linha única, corta na data dd/mm/aaaa ou no "|".
CREATE OR REPLACE FUNCTION normaliza_cavalo(s TEXT) RETURNS TEXT AS $$
DECLARE primeira TEXT;
BEGIN
  IF s IS NULL THEN RETURN NULL; END IF;
  primeira := trim(split_part(s, E'\n', 1));
  -- Multi-linha: genealogia fica na 2ª linha, nome é a 1ª linha inteira
  IF position(E'\n' IN s) > 0 THEN
    RETURN primeira;
  END IF;
  -- Linha única: corta a partir da data de nascimento
  IF primeira ~ '\s+\d{2}/\d{2}/\d{4}' THEN
    RETURN trim(regexp_replace(primeira, '\s+\d{2}/\d{2}/\d{4}.*$', ''));
  END IF;
  -- Ou corta no primeiro "|"
  IF position('|' IN primeira) > 0 THEN
    RETURN trim(split_part(primeira, '|', 1));
  END IF;
  RETURN primeira;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Nome do cavaleiro = 1ª linha, antes do "|"
CREATE OR REPLACE FUNCTION normaliza_cavaleiro(s TEXT) RETURNS TEXT AS $$
  SELECT CASE WHEN s IS NULL THEN NULL
    ELSE trim(split_part(split_part(s, E'\n', 1), '|', 1)) END;
$$ LANGUAGE sql IMMUTABLE;

-- Ano (YYYY) e mês (MM) a partir de data_inicio (texto ISO ou date) — robusto a NULL.
-- Recebe TEXT; chamadas passam data_inicio::text (no-op se já for texto).
CREATE OR REPLACE FUNCTION ano_de(d TEXT) RETURNS INT AS $$
  SELECT NULLIF(substring(d, 1, 4), '')::INT;
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION mes_de(d TEXT) RETURNS INT AS $$
  SELECT NULLIF(substring(d, 6, 2), '')::INT;
$$ LANGUAGE sql IMMUTABLE;

-- ──────────────── PROFILES: config de análise ──────────────────────
-- { visivel:bool, ano:int|null, meses:int[]|null, cavalos:text[]|null }
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS analise_config JSONB;

-- ──────────────── PROFILE_CAVALOS ──────────────────────────────────
-- Cavalos que o usuário criou ou possui (pra criadores/proprietários)
CREATE TABLE IF NOT EXISTS profile_cavalos (
  id          BIGSERIAL PRIMARY KEY,
  profile_id  UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  cavalo_nome TEXT NOT NULL,
  relacao     TEXT NOT NULL DEFAULT 'proprietario'
              CHECK (relacao IN ('criador','proprietario')),
  criado_em   TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (profile_id, cavalo_nome, relacao)
);

CREATE INDEX IF NOT EXISTS profile_cavalos_profile_idx ON profile_cavalos (profile_id);

ALTER TABLE profile_cavalos ENABLE ROW LEVEL SECURITY;

-- Qualquer um lê (aparece na página pública); só o dono escreve
DROP POLICY IF EXISTS "profile_cavalos_select" ON profile_cavalos;
CREATE POLICY "profile_cavalos_select" ON profile_cavalos FOR SELECT USING (true);

DROP POLICY IF EXISTS "profile_cavalos_insert" ON profile_cavalos;
CREATE POLICY "profile_cavalos_insert" ON profile_cavalos
  FOR INSERT WITH CHECK (auth.uid() = profile_id);

DROP POLICY IF EXISTS "profile_cavalos_delete" ON profile_cavalos;
CREATE POLICY "profile_cavalos_delete" ON profile_cavalos
  FOR DELETE USING (auth.uid() = profile_id);

-- ──────────────── RPC: autocomplete de cavalos ─────────────────────
-- Retorna nomes normalizados distintos + nº de resultados, casando o termo.
CREATE OR REPLACE FUNCTION buscar_cavalos(termo TEXT)
RETURNS TABLE(nome TEXT, total INT)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT normaliza_cavalo(cavalo_nome) AS nome, COUNT(*)::INT AS total
  FROM resultados
  WHERE cavalo_nome IS NOT NULL
    AND length(trim(coalesce(termo, ''))) >= 2
    AND normaliza_cavalo(cavalo_nome) ILIKE '%' || trim(termo) || '%'
  GROUP BY normaliza_cavalo(cavalo_nome)
  HAVING normaliza_cavalo(cavalo_nome) <> ''
  ORDER BY COUNT(*) DESC, nome ASC
  LIMIT 12;
$$;

-- ──────────────── RPC: stats do cavaleiro (filtrável) ──────────────
CREATE OR REPLACE FUNCTION stats_cavaleiro_filtrado(
  nome_input    TEXT,
  ano_input     INT     DEFAULT NULL,
  meses_input   INT[]   DEFAULT NULL,
  cavalos_input TEXT[]  DEFAULT NULL
) RETURNS TABLE(
  cavaleiro_nome      TEXT,
  total_participacoes INT,
  total_provas        INT,
  zeros               INT,
  vitorias            INT,
  top6                INT,
  cavalos             TEXT[]
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  WITH base AS (
    SELECT r.colocacao, r.penalidade, r.prova_id,
           normaliza_cavalo(r.cavalo_nome) AS cnome
    FROM resultados r
    LEFT JOIN provas p   ON p.id = r.prova_id
    LEFT JOIN torneios t ON t.id = p.torneio_id
    WHERE upper(normaliza_cavaleiro(r.cavaleiro_nome)) = upper(trim(nome_input))
      AND (ano_input  IS NULL OR ano_de(t.data_inicio::text) = ano_input)
      AND (meses_input IS NULL OR mes_de(t.data_inicio::text) = ANY(meses_input))
  ),
  filtrada AS (
    SELECT * FROM base
    WHERE cavalos_input IS NULL
       OR upper(cnome) = ANY(SELECT upper(x) FROM unnest(cavalos_input) AS x)
  )
  SELECT
    trim(nome_input),
    COUNT(*)::INT,
    COUNT(DISTINCT prova_id)::INT,
    COUNT(*) FILTER (WHERE penalidade ~ '^0([[:space:](,]|$)')::INT,
    COUNT(*) FILTER (WHERE trim(colocacao) = '1º')::INT,
    COUNT(*) FILTER (WHERE trim(colocacao) ~ '^[1-6]º$')::INT,
    (SELECT array_agg(DISTINCT cnome ORDER BY cnome)
       FROM filtrada WHERE cnome IS NOT NULL AND cnome <> '')
  FROM filtrada;
$$;

-- ──────────────── RPC: stats de um cavalo (filtrável) ──────────────
CREATE OR REPLACE FUNCTION stats_cavalo(
  cavalo_input TEXT,
  ano_input    INT    DEFAULT NULL,
  meses_input  INT[]  DEFAULT NULL
) RETURNS TABLE(
  cavalo_nome         TEXT,
  total_participacoes INT,
  total_provas        INT,
  zeros               INT,
  vitorias            INT,
  top6                INT,
  cavaleiros          TEXT[]
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  WITH base AS (
    SELECT r.colocacao, r.penalidade, r.prova_id,
           normaliza_cavaleiro(r.cavaleiro_nome) AS rnome
    FROM resultados r
    LEFT JOIN provas p   ON p.id = r.prova_id
    LEFT JOIN torneios t ON t.id = p.torneio_id
    WHERE upper(normaliza_cavalo(r.cavalo_nome)) = upper(trim(cavalo_input))
      AND (ano_input  IS NULL OR ano_de(t.data_inicio::text) = ano_input)
      AND (meses_input IS NULL OR mes_de(t.data_inicio::text) = ANY(meses_input))
  )
  SELECT
    trim(cavalo_input),
    COUNT(*)::INT,
    COUNT(DISTINCT prova_id)::INT,
    COUNT(*) FILTER (WHERE penalidade ~ '^0([[:space:](,]|$)')::INT,
    COUNT(*) FILTER (WHERE trim(colocacao) = '1º')::INT,
    COUNT(*) FILTER (WHERE trim(colocacao) ~ '^[1-6]º$')::INT,
    (SELECT array_agg(DISTINCT rnome ORDER BY rnome)
       FROM base WHERE rnome IS NOT NULL AND rnome <> '')
  FROM base;
$$;

-- ──────────────── GRANTS ────────────────────────────────────────────
GRANT EXECUTE ON FUNCTION buscar_cavalos(TEXT)                         TO anon, authenticated;
GRANT EXECUTE ON FUNCTION stats_cavaleiro_filtrado(TEXT,INT,INT[],TEXT[]) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION stats_cavalo(TEXT,INT,INT[])                 TO anon, authenticated;

SELECT 'OK' AS resultado;
