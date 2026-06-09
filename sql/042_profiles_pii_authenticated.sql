-- 042 — ENDURECE PII DE PROFILES TAMBÉM PRO PAPEL 'authenticated'
-- Resíduo da 041: um usuário LOGADO ainda lia email/celular/idade de QUALQUER
-- outro membro (o front só usa a view pública, mas a tabela estava aberta a
-- todo authenticated). LGPD: PII só pra quem é dono.
--
-- Estratégia (mesma da 041 pro anon): revoga o SELECT da tabela inteira do
-- 'authenticated' e reconcede só as colunas NÃO sensíveis. O dono lê o próprio
-- perfil completo (com PII) por uma RPC SECURITY DEFINER.
--
-- INSERT/UPDATE do próprio perfil NÃO são afetados (revogamos só SELECT, e o
-- front grava com return=minimal, sem ler de volta).
-- Subconsultas das policies de feed_posts/follows usam id/visibilidade, que
-- continuam concedidas → seguem funcionando.

-- 1) RPC: dono lê o próprio perfil completo (contorna o revoke de colunas)
create or replace function public.meu_perfil()
returns public.profiles
language sql
stable
security definer
set search_path = public
as $$
  select * from public.profiles where id = auth.uid();
$$;

revoke all on function public.meu_perfil() from public, anon;
grant execute on function public.meu_perfil() to authenticated;

-- 2) Tira o SELECT amplo do authenticated e devolve só as colunas públicas
--    (as mesmas da view profiles_publicos; fora: email, celular, idade, is_admin)
revoke select on public.profiles from authenticated;

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
) on public.profiles to authenticated;
