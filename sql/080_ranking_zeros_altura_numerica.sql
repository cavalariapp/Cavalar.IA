-- 080 — SEQUÊNCIA DE ZEROS robusta ao formato do `descricao`.
-- O ranking_zeros filtrava por p.descricao IN ('1,00M',…,'1,55M','1,60M') — string
-- EXATA, maiúscula. Mas o backfill/recura grava o descricao em OUTRO formato (ex.:
-- '1,55m - ST - U25'), então as provas altas (realinhadas/novas) pararam de casar e
-- SUMIRAM do ranking (1,50/1,55/1,60 zeradas). Agora casamos pela ALTURA NUMÉRICA
-- (public.altura_m) e devolvemos a altura NORMALIZADA 'X,XXM' (que o front já agrupa).
-- Vale pra qualquer altura/formato, presente e futuro.

create or replace function public.ranking_zeros(p_entity text)
returns table (altura text, nome text, comprimento int, ativo boolean, ultima_data date)
language sql stable security definer set search_path = public
set statement_timeout = '25s'
as $$
  with raw as (
    select public.altura_m(p.nome, p.descricao, p.categorias) as altm,
           trim(split_part(case when p_entity = 'cavalo' then r.cavalo_nome else r.cavaleiro_nome end, E'\n', 1)) as nome,
           -- ZERO = percurso limpo de VERDADE: tem um '0' E nenhum dígito 1-9 em
           -- lugar nenhum da penalidade. Pega 'X (PS+PT)' (faltas obstáculo+tempo) e
           -- 'duas fases' (total das 2 fases). Ex.: '0 (4+0)', '4 (0+4)', '1 (0+1)' →
           -- NÃO é zero. 'Eliminado'/'Desistente' (sem dígito) → não é zero.
           -- (Desempate/2ª volta já usam a 1ª volta: o desempate vai em penalidade_2.)
           (r.penalidade ~ '0' and r.penalidade !~ '[1-9]') as zero,
           t.data_inicio as dt, coalesce(p.numero, 0) as num, r.id as rid
    from resultados r
    join provas p   on p.id = r.prova_id
    join torneios t on t.id = p.torneio_id
    where t.data_inicio >= make_date(extract(year from current_date)::int, 1, 1)
      and t.data_inicio <= make_date(extract(year from current_date)::int, 12, 31)
  ),
  base as (   -- normaliza a altura p/ 'X,XXM' e mantém só as faixas do ranking
    select (trunc(altm)::int::text || ',' ||
            lpad((round((altm - trunc(altm)) * 100))::int::text, 2, '0') || 'M') as altura,
           nome, zero, dt, num, rid
    from raw
    where altm in (1.00,1.10,1.20,1.30,1.35,1.40,1.45,1.50,1.55,1.60)
      and nome <> ''
  ),
  seq as (
    select altura, nome, zero, dt,
           row_number() over (partition by altura, nome order by dt, num, rid) as rn
    from base
  ),
  isl as (
    select altura, nome, zero, dt, rn,
           rn - row_number() over (partition by altura, nome, zero order by rn) as grp
    from seq
  ),
  runs as (   -- ilhas de zeros consecutivos
    select altura, nome, grp, count(*)::int as run_len, max(rn) as max_rn, max(dt) as run_dt
    from isl where zero group by altura, nome, grp
  ),
  ent as (    -- última posição e última data por entidade
    select altura, nome, max(rn) as last_rn, max(dt) as ult from seq group by altura, nome
  ),
  agg as (
    select e.altura, e.nome, e.ult,
           coalesce(max(case when r.max_rn = e.last_rn then r.run_len end), 0) as corrente,
           coalesce(max(case when r.max_rn <> e.last_rn then r.run_len end), 0) as best_completed
    from ent e left join runs r on r.altura = e.altura and r.nome = e.nome
    group by e.altura, e.nome, e.ult
  ),
  fin as (
    select altura, nome,
           case when corrente > 0 and corrente >= best_completed then corrente else best_completed end as comprimento,
           (corrente > 0 and corrente >= best_completed) as ativo,
           ult as ultima_data
    from agg
  ),
  ranked as (
    select altura, nome, comprimento, ativo, ultima_data,
           row_number() over (partition by altura
             order by comprimento desc, ativo desc, ultima_data desc nulls last) as rk
    from fin where comprimento > 0
  )
  select altura, nome, comprimento, ativo, ultima_data from ranked where rk <= 25;
$$;
grant execute on function public.ranking_zeros(text) to anon, authenticated;
