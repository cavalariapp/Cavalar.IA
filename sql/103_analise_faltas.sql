-- 103 — ANÁLISE GENÉTICA por FALTAS (consistência dos filhos).
-- Cria uma base materializada de faltas por filho/ano (mv_genetica_faltas), recalculada
-- junto com a genética (refresh_genetica), e a função analise_destaque que devolve a
-- MATRIZ destaque por: % de percursos limpos OU média de faltas por percurso.
-- Mínimo 3 filhos competindo (e ≥10 percursos no total) p/ entrar.

-- (1) base de faltas por filho × ano (mesma junção da mv_genetica, mas contando
--     PERCURSOS, LIMPOS e somando FALTAS — sem colapsar por altura).
drop materialized view if exists public.mv_genetica_faltas;
create materialized view public.mv_genetica_faltas as
with res as (
  select public.canon_cavalo(r.cavalo_norm) as filho_norm,
         r.nasc_cavalo as nasc_res,
         extract(year from coalesce(p.data_prova, t.data_inicio))::int as ano_prova,
         (r.penalidade ~ '0' and r.penalidade !~ '[1-9]') as zero,
         case when r.penalidade ~ '^[0-9]+$' then r.penalidade::int end as faltas_num
  from public.resultados r
  join public.provas p on p.id = r.prova_id
  left join public.torneios t on t.id = p.torneio_id
  where r.cavalo_norm is not null and coalesce(r.penalidade,'') <> ''
)
select g.cd_token,
       g.pai_token, g.mae_token,
       norm_nome(g.pai) as pai_norm,
       norm_nome(g.mae) as mae_norm,
       res.ano_prova,
       count(*)                               as percursos,
       count(*) filter (where res.zero)       as limpos,
       count(res.faltas_num)                  as percursos_num,
       coalesce(sum(res.faltas_num), 0)       as soma_faltas
from public.genealogia g
join res on res.filho_norm = public.canon_cavalo(norm_nome(g.nome))
  and (
    res.nasc_res is null or g.nascimento is null
    or res.nasc_res = g.nascimento
    or (extract(year from res.nasc_res) = extract(year from g.nascimento)
        and ( (extract(month from res.nasc_res)=1 and extract(day from res.nasc_res)=1)
           or (extract(month from g.nascimento)=1 and extract(day from g.nascimento)=1) ))
  )
group by g.cd_token, g.pai_token, g.mae_token, norm_nome(g.pai), norm_nome(g.mae), res.ano_prova;

create index if not exists mv_gen_faltas_mae_idx on public.mv_genetica_faltas (mae_norm);
create index if not exists mv_gen_faltas_pai_idx on public.mv_genetica_faltas (pai_norm);
create index if not exists mv_gen_faltas_ano_idx on public.mv_genetica_faltas (ano_prova);

-- (2) refresh_genetica passa a recalcular AS DUAS bases.
create or replace function public.refresh_genetica()
returns void language sql security definer set search_path = public set statement_timeout = 0 as $$
  refresh materialized view public.mv_genetica;
  refresh materialized view public.mv_genetica_faltas;
$$;
grant execute on function public.refresh_genetica() to authenticated, service_role;

-- (3) analise_destaque(ano, metrica): MATRIZ destaque.
--     metrica 'limpos' = maior % de percursos limpos; 'media' = menor média de faltas.
drop function if exists public.analise_destaque(int);
create or replace function public.analise_destaque(p_ano int default null, p_metrica text default 'limpos')
returns json
language sql stable security definer set search_path = public set statement_timeout = '15s'
as $$
  with base as (
    select coalesce(nullif(mae_token, ''), mae_norm) as rep_key, mae_norm as rep_disp,
           cd_token, percursos, limpos, percursos_num, soma_faltas
    from public.mv_genetica_faltas
    where mae_norm is not null and mae_norm <> '' and not public._rep_placeholder(mae_norm)
      and (p_ano is null or ano_prova = p_ano)
  ),
  agg as (
    select rep_key, max(rep_disp) as nome,
           count(distinct cd_token) as filhos,
           sum(percursos) as percursos, sum(limpos) as limpos,
           sum(percursos_num) as percursos_num, sum(soma_faltas) as soma_faltas
    from base group by rep_key
    having count(distinct cd_token) >= 3 and sum(percursos) >= 10
  ),
  pick as (
    select *,
           round(100.0 * limpos / nullif(percursos,0), 1)        as pct_limpos,
           round(soma_faltas::numeric / nullif(percursos_num,0), 2) as media_faltas
    from agg
    order by case when p_metrica = 'media'
                  then (soma_faltas::numeric / nullif(percursos_num,0))    -- menor é melhor
                  else (-(limpos::numeric / nullif(percursos,0))) end       -- maior % é melhor
    limit 1
  )
  select case when not exists (select 1 from pick) then null else (
    select json_build_object(
      'headline', 'Matriz destaque' || coalesce(' ' || p_ano::text, '') || ': ' || initcap(lower(nome)),
      'summary', case when p_metrica = 'media'
        then initcap(lower(nome)) || ' — filhos com média de ' || replace(media_faltas::text, '.', ',') ||
             ' falta(s) por percurso em ' || percursos || ' provas (' || filhos || ' filhos).'
        else initcap(lower(nome)) || ' — filhos com ' || replace(pct_limpos::text, '.', ',') ||
             '% de percursos limpos em ' || percursos || ' provas (' || filhos || ' filhos).' end
        || coalesce(' Temporada ' || p_ano::text || '.', ' Histórico.')
        || ' Dados reais do Cavalar.IA (ABCCH × pistas).',
      'tags', json_build_array('Genética', 'Matrizes',
              case when p_metrica='media' then 'Média de faltas' else 'Percursos limpos' end,
              coalesce(p_ano::text, 'Histórico'))
    ) from pick)
  end;
$$;
grant execute on function public.analise_destaque(int, text) to anon, authenticated;
