-- 034 — MOTOR DE RANKINGS GENÉTICOS
-- Liga resultados esportivos ↔ genealogia (por nome normalizado) e entrega, por
-- reprodutor (pai/mãe), os 3 rankings:
--   R1 nº de filhos (registrados na ABCCH)
--   R2 nº e % de filhos +4 anos (idade atual) que COMPETIRAM
--   R3 nº e % de filhos +8 anos em provas ≥ 1,40 m
-- Filtro por ano (ano da prova) ou todos (NULL). % sempre ÷ total de filhos
-- registrados. Roda no servidor (o app só recebe o ranking pronto).
--
-- Normalização SEM extensão unaccent (que no Supabase vive em `extensions` e
-- complica o índice): acentos PT removidos via translate → IMMUTABLE puro.

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

-- altura (m) da prova: 1ª medida "X,YY m" em nome+descricao+categorias.
create or replace function public.altura_m(a text, b text, c text)
returns numeric language sql immutable parallel safe as $$
  select (m[1]::int + m[2]::numeric / 100)
  from (select regexp_match(upper(concat_ws(' ', a, b, c)), '(\d{1,2})[,.](\d{2})\s*M')) s(m)
  where m is not null
$$;

create index if not exists genealogia_nomenorm_idx on public.genealogia (norm_nome(nome));

-- ranking por reprodutor. papel = 'pai' ou 'mae'; ano = filtro (NULL = todos).
create or replace function public.rankings_geneticos(papel text, ano int default null)
returns table (
  reprodutor   text,
  total_filhos bigint,
  comp4        bigint,
  pct_comp4    numeric,
  alto8        bigint,
  pct_alto8    numeric
)
language sql stable security definer set search_path = public as $$
  with ger as (
    select cd_token,
           norm_nome(nome) as filho_norm,
           norm_nome(case when papel = 'mae' then mae else pai end) as rep_norm,
           (case when papel = 'mae' then mae else pai end) as rep_disp,
           (extract(year from current_date)::int - extract(year from nascimento)::int) as idade
    from genealogia
    where norm_nome(case when papel = 'mae' then mae else pai end) is not null
  ),
  tot as (
    select rep_norm, max(rep_disp) as nome, count(distinct cd_token) as total
    from ger group by rep_norm
  ),
  ev as (
    select norm_nome(r.cavalo_nome) as filho_norm,
           extract(year from coalesce(p.data_prova, t.data_inicio))::int as ano,
           max(altura_m(p.nome, p.descricao, p.categorias)) as max_alt
    from resultados r
    join provas p on p.id = r.prova_id
    left join torneios t on t.id = p.torneio_id
    where r.cavalo_nome is not null
    group by 1, 2
  ),
  agg as (
    select g.rep_norm,
           count(distinct g.cd_token) filter (where g.idade > 4) as comp4,
           count(distinct g.cd_token) filter (where g.idade > 8 and ev.max_alt >= 1.40) as alto8
    from ger g
    join ev on ev.filho_norm = g.filho_norm
    where (rankings_geneticos.ano is null or ev.ano = rankings_geneticos.ano)
    group by g.rep_norm
  )
  select t.nome, t.total,
         coalesce(a.comp4, 0),
         round(100.0 * coalesce(a.comp4, 0) / nullif(t.total, 0), 1),
         coalesce(a.alto8, 0),
         round(100.0 * coalesce(a.alto8, 0) / nullif(t.total, 0), 1)
  from tot t
  join agg a on a.rep_norm = t.rep_norm
  where coalesce(a.comp4, 0) > 0 or coalesce(a.alto8, 0) > 0
  order by t.total desc;
$$;

grant execute on function public.rankings_geneticos(text, int) to anon, authenticated;
