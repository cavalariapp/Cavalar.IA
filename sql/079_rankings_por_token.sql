-- 079 — DESAMBIGUAÇÃO EXATA de reprodutor por TOKEN do pai/mãe (resolve a Olanda).
-- Pré-requisito: sql/078 (colunas pai_token/mae_token) + rodar o --abcch-detalhe
-- (enche os tokens). Antes de os tokens existirem, tudo cai no agrupamento por NOME
-- (igual à 077) — então é SEGURO rodar esta migração a qualquer momento; ela "liga"
-- sozinha conforme os tokens vão sendo preenchidos.
--
-- A chave do reprodutor passa a ser o TOKEN do pai/mãe (quando conhecido), senão o
-- NOME normalizado. Duas "Olanda" de pais diferentes têm tokens diferentes → ficam
-- SEPARADAS, sem heurística de idade.

-- (1) mv_genetica passa a carregar o token do pai/mãe (da genealogia). Mesma definição
--     rápida da 074 (colunas pré-computadas), + pai_token/mae_token.
drop materialized view if exists public.mv_genetica;
create materialized view public.mv_genetica as
with res as (
  select public.canon_cavalo(r.cavalo_norm) as filho_norm,   -- resolve apelidos (sql/082)
         r.nasc_cavalo  as nasc_res,
         extract(year from coalesce(p.data_prova, t.data_inicio))::int as ano_prova,
         public.altura_m(p.nome, p.descricao, p.categorias) as alt
  from public.resultados r
  join public.provas p on p.id = r.prova_id
  left join public.torneios t on t.id = p.torneio_id
  where r.cavalo_norm is not null
)
select g.cd_token,
       norm_nome(g.pai) as pai_norm,
       norm_nome(g.mae) as mae_norm,
       g.pai_token,
       g.mae_token,
       extract(year from g.nascimento)::int as nasc_ano,
       res.ano_prova,
       max(res.alt) as max_alt
from public.genealogia g
join res on res.filho_norm = norm_nome(g.nome)
  and (
    res.nasc_res is null or g.nascimento is null
    or res.nasc_res = g.nascimento
    or (extract(year from res.nasc_res) = extract(year from g.nascimento)
        and ( (extract(month from res.nasc_res) = 1 and extract(day from res.nasc_res) = 1)
           or (extract(month from g.nascimento)  = 1 and extract(day from g.nascimento)  = 1) ))
  )
where res.alt is not null
  and (
    g.nascimento is null or res.ano_prova is null
    or res.alt <= public.alt_max_para_idade(res.ano_prova - extract(year from g.nascimento)::int)
  )
group by g.cd_token, norm_nome(g.pai), norm_nome(g.mae), g.pai_token, g.mae_token,
         extract(year from g.nascimento)::int, res.ano_prova;

create index if not exists mv_genetica_pai_idx       on public.mv_genetica (pai_norm);
create index if not exists mv_genetica_mae_idx       on public.mv_genetica (mae_norm);
create index if not exists mv_genetica_cd_idx        on public.mv_genetica (cd_token);
create index if not exists mv_genetica_paitoken_idx  on public.mv_genetica (pai_token);
create index if not exists mv_genetica_maetoken_idx  on public.mv_genetica (mae_token);

-- (2) rankings_geneticos agrupa por TOKEN do pai/mãe (fallback: nome). Mantém o
--     "por ano" (idade na época) da 077.
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
           coalesce(nullif(case when papel = 'mae' then mae_token else pai_token end, ''),
                    norm_nome(case when papel = 'mae' then mae else pai end)) as rep_key,
           (v_ref - extract(year from nascimento)::int) as idade
    from genealogia
    where not _rep_placeholder(norm_nome(case when papel = 'mae' then mae else pai end))
      and (nascimento is null or extract(year from nascimento)::int <= v_ref)
  ),
  tot as (
    select rep_key, max(rep_disp) as nome,
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
  select t.nome, t.total,
         t.f4, coalesce(a.comp, 0),
         round(100.0 * coalesce(a.comp4, 0) / nullif(t.f4, 0), 1),
         t.f8, coalesce(a.m140, 0),
         round(100.0 * coalesce(a.alto8, 0) / nullif(t.f8, 0), 1)
  from tot t
  left join agg a on a.rep_key = t.rep_key           -- LEFT: reprodutor sem prole competindo aparece
  where t.total >= 2 or coalesce(a.comp, 0) > 0       -- piso: evita cauda longa de 1 filho
  order by t.total desc;
end;
$$;
grant execute on function public.rankings_geneticos(text, int) to anon, authenticated;
