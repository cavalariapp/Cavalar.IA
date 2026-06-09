-- 041 — CORRIGE de fato o vazamento de PII em profiles
-- O REVOKE por COLUNA da migration 040 não surtiu efeito: existe um GRANT SELECT
-- na TABELA inteira para 'anon', que cobre todas as colunas (o revoke de coluna
-- é ignorado nesse caso). O correto é revogar o SELECT da tabela e reconceder
-- apenas as colunas NÃO sensíveis.
--
-- Mantemos 'id' e 'visibilidade' no anon porque as policies de feed_posts/follows
-- fazem subconsulta em profiles (EXISTS ... WHERE visibilidade='publico') e o
-- visitante anônimo lê o feed — sem essas colunas o feed quebraria.
-- Fora: email, celular, idade (PII) e is_admin (não expor quem é admin).
-- As colunas concedidas = exatamente as da view profiles_publicos.

revoke select on public.profiles from anon;

grant select (
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
) on public.profiles to anon;
