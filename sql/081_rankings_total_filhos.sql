-- 081 — total_filhos = TODOS os filhos (não só os que competiram) + reprodutor
-- aparece mesmo SEM prole competindo.
-- PROBLEMA: a query fazia INNER JOIN entre "todos os filhos" (genealogia) e "filhos
-- que competiram" (mv_genetica) → uma matriz prolífica sem prole competindo (ex.:
-- Olinda Jmen, 46 filhos) sumia do ranking inteiro, mesmo no sort "Filhos". Agora:
--   • LEFT JOIN → o reprodutor aparece mesmo com 0 competindo (comp/m140 = 0);
--   • total_filhos = TODOS os filhos nascidos até o ano (parâmetro p/ comparar com
--     "competindo" e "≥1,40m"); f4/f8 seguem por faixa de idade no ano.
-- Piso: total>=2 OU algum competindo → evita a cauda longa de 1 filho. Mantém o
-- "por ano" (077). Versão por NOME (roda na mv atual); a 079 traz a mesma correção
-- já agrupando por TOKEN (separa Olanda) quando você rodar o abcch_detalhe.

drop function if exists public.rankings_geneticos(text, int);
create or replace function public.rankings_geneticos(papel text, ano int default null)
returns table (
  reprodutor   text,
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
           norm_nome(case when papel = 'mae' then mae else pai end) as rep_norm,
           (case when papel = 'mae' then mae else pai end) as rep_disp,
           (v_ref - extract(year from nascimento)::int) as idade
    from genealogia
    where not _rep_placeholder(norm_nome(case when papel = 'mae' then mae else pai end))
      and (nascimento is null or extract(year from nascimento)::int <= v_ref)
  ),
  tot as (
    select rep_norm, max(rep_disp) as nome,
           count(distinct cd_token) as total,    -- TODOS os filhos (nascidos até Y)
           count(distinct cd_token) filter (where idade between 5 and 30) as f4,
           count(distinct cd_token) filter (where idade between 9 and 30) as f8
    from ger group by rep_norm
  ),
  ev as (
    select (case when papel = 'mae' then mae_norm else pai_norm end) as rep_norm,
           cd_token,
           (v_ref - nasc_ano) as idade,
           max(max_alt) as max_alt
    from mv_genetica
    where (rankings_geneticos.ano is null or ano_prova = rankings_geneticos.ano)
    group by 1, 2, 3
  ),
  agg as (
    select rep_norm,
           count(distinct cd_token) filter (where idade between 1 and 30) as comp,
           count(distinct cd_token) filter (where idade between 5 and 30) as comp4,
           count(distinct cd_token) filter (where idade between 1 and 30 and max_alt >= 1.40) as m140,
           count(distinct cd_token) filter (where idade between 9 and 30 and max_alt >= 1.40) as alto8
    from ev group by rep_norm
  )
  select t.nome, t.total,
         t.f4, coalesce(a.comp, 0),
         round(100.0 * coalesce(a.comp4, 0) / nullif(t.f4, 0), 1),
         t.f8, coalesce(a.m140, 0),
         round(100.0 * coalesce(a.alto8, 0) / nullif(t.f8, 0), 1)
  from tot t
  left join agg a on a.rep_norm = t.rep_norm        -- LEFT: matriz sem prole competindo aparece
  where t.total >= 2 or coalesce(a.comp, 0) > 0      -- piso: evita cauda longa de 1 filho
  order by t.total desc;
end;
$$;
grant execute on function public.rankings_geneticos(text, int) to anon, authenticated;
