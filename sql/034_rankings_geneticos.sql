-- 034 — MOTOR DE RANKINGS GENÉTICOS (com materialized view p/ performance)
-- Liga resultados ↔ genealogia (nome normalizado) e entrega por reprodutor:
--   R1 nº de filhos (registrados ABCCH) · R2 +4a competindo (n/%) · R3 +8a ≥1,40m (n/%)
-- Filtro por ano da prova ou todos. % ÷ total de filhos registrados. Idade atual.
-- A junção pesada (138k resultados) fica numa VIEW MATERIALIZADA; a RPC só agrega
-- a view (pequena) + a genealogia → rápido. Refresh via refresh_genetica().

-- normalização sem unaccent (translate; IMMUTABLE puro)
create or replace function public.norm_nome(t text)
returns text language sql immutable parallel safe as $$
  select nullif(upper(trim(regexp_replace(
           regexp_replace(
             translate(split_part(coalesce(t, ''), E'\n', 1),
               'áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ',
               'aaaaaeeeeiiiiooooouuuucnAAAAAEEEEIIIIOOOOOUUUUCN'),
             '\([^)]*\)', '', 'g'),
           '[^A-Za-z0-9]+', ' ', 'g'))), '')
$$;

create or replace function public.altura_m(a text, b text, c text)
returns numeric language sql immutable parallel safe as $$
  select (m[1]::int + m[2]::numeric / 100)
  from (select regexp_match(upper(concat_ws(' ', a, b, c)), '(\d{1,2})[,.](\d{2})\s*M')) s(m)
  where m is not null
$$;

create index if not exists genealogia_nomenorm_idx on public.genealogia (norm_nome(nome));

-- view materializada: 1 linha por (filho que competiu × ano), com pai/mãe
-- normalizados + ano de nascimento + maior altura saltada naquele ano.
drop materialized view if exists public.mv_genetica;
create materialized view public.mv_genetica as
select g.cd_token,
       norm_nome(g.pai) as pai_norm,
       norm_nome(g.mae) as mae_norm,
       extract(year from g.nascimento)::int as nasc_ano,
       ev.ano_prova,
       ev.max_alt
from public.genealogia g
join (
  select norm_nome(r.cavalo_nome) as filho_norm, pa.ano_prova, max(pa.alt) as max_alt
  from public.resultados r
  join (
    select p.id,
           extract(year from coalesce(p.data_prova, t.data_inicio))::int as ano_prova,
           public.altura_m(p.nome, p.descricao, p.categorias) as alt
    from public.provas p
    left join public.torneios t on t.id = p.torneio_id
  ) pa on pa.id = r.prova_id
  where r.cavalo_nome is not null
  group by 1, 2
) ev on ev.filho_norm = norm_nome(g.nome);

create index if not exists mv_genetica_pai_idx on public.mv_genetica (pai_norm);
create index if not exists mv_genetica_mae_idx on public.mv_genetica (mae_norm);

-- placeholders de "sem origem" que NÃO são reprodutores reais
create or replace function public._rep_placeholder(n text)
returns boolean language sql immutable as $$
  select n is null or n in (
    'NAO CADASTRADA','NAO CADASTRADO','DESCONHECIDO','DESCONHECIDA',
    'SEM ORIGEM','IMPORTADO','IMPORTADA','SEM REGISTRO')
$$;

create or replace function public.rankings_geneticos(papel text, ano int default null)
returns table (
  reprodutor text, total_filhos bigint,
  comp4 bigint, pct_comp4 numeric,
  alto8 bigint, pct_alto8 numeric
)
language sql stable security definer
set search_path = public
set statement_timeout = '25s' as $$
  with tot as (
    select norm_nome(case when papel = 'mae' then mae else pai end) as rep_norm,
           max(case when papel = 'mae' then mae else pai end) as nome,
           count(distinct cd_token) as total
    from genealogia
    where not _rep_placeholder(norm_nome(case when papel = 'mae' then mae else pai end))
    group by 1
  ),
  agg as (
    -- idade entre 4..30 / 8..30: o teto descarta homônimos antigos (fundadores
    -- dos anos 60-70, ex.: placeholder "TEHRAN × BIBIBEG") que casam por nome com
    -- cavalos modernos — um cavalo nascido nos anos 70 não compete hoje.
    select (case when papel = 'mae' then mae_norm else pai_norm end) as rep_norm,
           count(distinct cd_token) filter (
             where (extract(year from current_date)::int - nasc_ano) between 5 and 30) as comp4,
           count(distinct cd_token) filter (
             where (extract(year from current_date)::int - nasc_ano) between 9 and 30
               and max_alt >= 1.40) as alto8
    from mv_genetica
    where (rankings_geneticos.ano is null or ano_prova = rankings_geneticos.ano)
    group by 1
  )
  select t.nome, t.total,
         coalesce(a.comp4, 0), round(100.0 * coalesce(a.comp4, 0) / nullif(t.total, 0), 1),
         coalesce(a.alto8, 0), round(100.0 * coalesce(a.alto8, 0) / nullif(t.total, 0), 1)
  from tot t
  join agg a on a.rep_norm = t.rep_norm
  where coalesce(a.comp4, 0) > 0 or coalesce(a.alto8, 0) > 0
  order by t.total desc;
$$;

create or replace function public.refresh_genetica()
returns void language sql security definer set search_path = public as $$
  refresh materialized view public.mv_genetica;
$$;

grant execute on function public.rankings_geneticos(text, int) to anon, authenticated;
grant execute on function public.refresh_genetica() to authenticated, service_role;
