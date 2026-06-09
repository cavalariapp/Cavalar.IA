-- 049 — RPCs que substituem os últimos acessos diretos a `resultados`
-- (depois disto, a 050 revoga o SELECT direto). Tudo SECURITY DEFINER.
--   FREE   : ranking de zeros (home), sugestões de nomes (autocomplete)
--   PREMIUM: busca/compilado filtrado (resultados.html)

-- ── FREE: ranking de sequência de zeros (home) ───────────────────────────
-- Calcula no servidor e devolve SÓ o ranking (altura, nome, comprimento, ativo,
-- última data) — sem expor as linhas cruas. Replica a regra do front:
--   ativo/maior streak corrente >= melhor streak completado → "N+", senão "N".
create or replace function public.ranking_zeros(p_entity text)
returns table (altura text, nome text, comprimento int, ativo boolean, ultima_data date)
language sql
stable
security definer
set search_path = public
set statement_timeout = '20s'
as $$
  with base as (
    select p.descricao as altura,
           trim(split_part(case when p_entity = 'cavalo' then r.cavalo_nome else r.cavaleiro_nome end, E'\n', 1)) as nome,
           (r.penalidade ~ '^0([[:space:](,]|$)') as zero,
           t.data_inicio as dt, coalesce(p.numero, 0) as num, r.id as rid
    from resultados r
    join provas p   on p.id = r.prova_id
    join torneios t on t.id = p.torneio_id
    where p.descricao in ('1,00M','1,10M','1,20M','1,30M','1,35M','1,40M','1,45M','1,50M','1,55M','1,60M')
      and t.data_inicio >= make_date(extract(year from current_date)::int, 1, 1)
      and t.data_inicio <= make_date(extract(year from current_date)::int, 12, 31)
  ),
  seq as (
    select altura, nome, zero, dt,
           row_number() over (partition by altura, nome order by dt, num, rid) as rn
    from base
    where nome <> ''
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
  )
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
  -- top 25 por altura (o front exibe top 10 + faz desempate); evita o teto de 1000
  select altura, nome, comprimento, ativo, ultima_data from ranked where rk <= 25;
$$;
grant execute on function public.ranking_zeros(text) to anon, authenticated;

-- ── FREE: sugestões de NOMES (autocomplete) — só nomes, nunca resultados ──
-- Devolve o nome COMPLETO (linha 1 = nome, linha 2 = entidade/genealogia); o
-- front faz o dedup por nome normalizado. Só nomes — nunca colocação/tempo/etc.
create or replace function public.buscar_nomes_cavalos(termo text)
returns table (nome text)
language sql stable security definer set search_path = public as $$
  select distinct cavalo_nome as nome
  from resultados
  where cavalo_nome ilike '%' || termo || '%'
  order by 1 limit 80;
$$;
grant execute on function public.buscar_nomes_cavalos(text) to anon, authenticated;

create or replace function public.buscar_nomes_cavaleiros(termo text)
returns table (nome text)
language sql stable security definer set search_path = public as $$
  select distinct cavaleiro_nome as nome
  from resultados
  where cavaleiro_nome ilike '%' || termo || '%'
  order by 1 limit 80;
$$;
grant execute on function public.buscar_nomes_cavaleiros(text) to anon, authenticated;

-- ── PREMIUM: busca/compilado filtrado (resultados.html buscaGlobal) ──────
create or replace function public.buscar_resultados(
  p_cavaleiro text default null,
  p_cavalo    text default null,
  p_fonte     text default null,
  p_ano       int  default null,
  p_mes       int  default null
)
returns table (
  colocacao text, cavaleiro_nome text, cavalo_nome text,
  penalidade text, tempo text, pontos text, penalidade_2 text, tempo_2 text,
  prova_id bigint, prova_nome text, prova_numero int, prova_descricao text,
  prova_categorias text, prova_tipo text,
  torneio_id bigint, torneio_nome text, torneio_fonte text, torneio_data date
)
language plpgsql stable security definer set search_path = public as $$
begin
  if not public.is_premium() then
    raise exception 'premium_required' using errcode = '42501';
  end if;
  return query
    select r.colocacao, r.cavaleiro_nome, r.cavalo_nome,
           r.penalidade, r.tempo, r.pontos::text, r.penalidade_2, r.tempo_2,
           p.id, p.nome, p.numero, p.descricao, p.categorias, p.tipo_prova,
           t.id, t.nome, t.fonte, t.data_inicio::date
    from resultados r
    join provas p   on p.id = r.prova_id
    join torneios t on t.id = p.torneio_id
    where (p_cavaleiro is null or r.cavaleiro_nome ilike '%' || p_cavaleiro || '%')
      and (p_cavalo    is null or r.cavalo_nome    ilike '%' || p_cavalo    || '%')
      and (p_fonte is null or t.fonte = p_fonte)
      and (p_ano   is null or extract(year  from t.data_inicio) = p_ano)
      and (p_mes   is null or extract(month from t.data_inicio) = p_mes)
    limit 500;
end;
$$;
grant execute on function public.buscar_resultados(text, text, text, int, int) to anon, authenticated;
