-- 085 — PROGÊNIE por TOKEN (fecha o buraco da Olympia no PERFIL do reprodutor).
-- A sql/079 já separa o RANKING por token do pai/mãe, mas ao CLICAR no reprodutor o
-- app chamava progenie(papel, rep) que filtra por NOME → juntava todas as "Olympia"
-- de novo (a de 1996 aparecia com filhos de 1970/1980). Diagnóstico confirmou que os
-- filhos TÊM mae_token correto; faltava o front/RPC usá-lo.
--
-- (1) rankings_geneticos passa a DEVOLVER rep_token (o token do reprodutor daquela
--     linha, ou null quando o grupo é por nome). O front repassa pro progenie.
-- (2) progenie ganha o parâmetro opcional rep_token: quando informado, filtra os
--     filhos por mae_token/pai_token = rep_token (separação EXATA). Sem token (ex.:
--     garanhão importado fora do studbook), mantém o comportamento por nome.

-- ── (1) rankings_geneticos + rep_token ──────────────────────────────────────────
drop function if exists public.rankings_geneticos(text, int);
create or replace function public.rankings_geneticos(papel text, ano int default null)
returns table (
  reprodutor   text,
  rep_token    text,
  total_filhos bigint,
  f4           bigint,
  comp         bigint,
  pct_comp     numeric,
  f8           bigint,
  m140         bigint,
  pct140       numeric
)
language plpgsql stable security definer
set search_path = public
set statement_timeout = '25s'
as $$
declare v_ref int := coalesce(ano, extract(year from current_date)::int);
begin
  if not public.is_premium() then
    raise exception 'premium_required' using errcode = '42501';
  end if;

  return query
  with ger as (
    select cd_token,
           (case when papel = 'mae' then mae else pai end) as rep_disp,
           coalesce(nullif(case when papel = 'mae' then mae_token else pai_token end, ''),
                    norm_nome(case when papel = 'mae' then mae else pai end)) as rep_key,
           nullif(case when papel = 'mae' then mae_token else pai_token end, '') as rep_tok,
           (v_ref - extract(year from nascimento)::int) as idade
    from genealogia
    where not _rep_placeholder(norm_nome(case when papel = 'mae' then mae else pai end))
      and (nascimento is null or extract(year from nascimento)::int <= v_ref)
  ),
  tot as (
    select rep_key, max(rep_disp) as nome, max(rep_tok) as rep_token,
           count(distinct cd_token) as total,
           count(distinct cd_token) filter (where idade between 5 and 30) as f4,
           count(distinct cd_token) filter (where idade between 9 and 30) as f8
    from ger group by rep_key
  ),
  ev as (
    select coalesce(nullif(case when papel = 'mae' then mae_token else pai_token end, ''),
                    case when papel = 'mae' then mae_norm else pai_norm end) as rep_key,
           cd_token,
           (v_ref - nasc_ano) as idade,
           max(max_alt) as max_alt
    from mv_genetica
    where (rankings_geneticos.ano is null or ano_prova = rankings_geneticos.ano)
    group by 1, 2, 3
  ),
  agg as (
    select rep_key,
           count(distinct cd_token) filter (where idade between 1 and 30) as comp,
           count(distinct cd_token) filter (where idade between 5 and 30) as comp4,
           count(distinct cd_token) filter (where idade between 1 and 30 and max_alt >= 1.40) as m140,
           count(distinct cd_token) filter (where idade between 9 and 30 and max_alt >= 1.40) as alto8
    from ev group by rep_key
  )
  select t.nome, t.rep_token, t.total,
         t.f4, coalesce(a.comp, 0),
         round(100.0 * coalesce(a.comp4, 0) / nullif(t.f4, 0), 1),
         t.f8, coalesce(a.m140, 0),
         round(100.0 * coalesce(a.alto8, 0) / nullif(t.f8, 0), 1)
  from tot t
  left join agg a on a.rep_key = t.rep_key
  where t.total >= 2 or coalesce(a.comp, 0) > 0
  order by t.total desc;
end;
$$;
grant execute on function public.rankings_geneticos(text, int) to anon, authenticated;

-- ── (2) progenie com rep_token opcional ─────────────────────────────────────────
drop function if exists public.progenie(text, text);
drop function if exists public.progenie(text, text, text);
create or replace function public.progenie(papel text, rep text, rep_token text default null)
returns table (nome text, sexo text, nascimento date, max_alt numeric, competiu boolean)
language plpgsql stable security definer set search_path = public as $$
declare use_tok boolean := (rep_token is not null and rep_token <> '');
begin
  if public.is_premium() then
    return query
      with alt as (
        select mv.cd_token as cd, max(mv.max_alt) as malt
        from mv_genetica mv group by mv.cd_token
      )
      select g.nome, g.sexo, g.nascimento, a.malt, (a.cd is not null)
      from genealogia g
      left join alt a on a.cd = g.cd_token
      where (use_tok and (case when papel = 'mae' then g.mae_token else g.pai_token end) = rep_token)
         or (not use_tok and norm_nome(case when papel = 'mae' then g.mae else g.pai end) = norm_nome(rep))
      order by a.malt desc nulls last, g.nome;
  else
    return query
      select g.nome, g.sexo, g.nascimento, null::numeric, null::boolean
      from genealogia g
      where (use_tok and (case when papel = 'mae' then g.mae_token else g.pai_token end) = rep_token)
         or (not use_tok and norm_nome(case when papel = 'mae' then g.mae else g.pai end) = norm_nome(rep))
      order by g.nome;
  end if;
end;
$$;
grant execute on function public.progenie(text, text, text) to anon, authenticated;
