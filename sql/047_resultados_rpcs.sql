-- 047 — CAMADA DE RPCs PARA PROTEGER O GARGALO (resultados)
-- Aditivo: cria as funções, NÃO revoga nada ainda (o revoke vem depois que o
-- front estiver 100% migrado — ver 049). Princípio:
--   • FREE  : ver o resultado de UMA prova (pódio do evento)        -> resultados_prova()
--   • PREMIUM: compilar o histórico de um cavalo/cavaleiro          -> historico_*()
--   • progenie: lista (nomes/sexo/nasc) é FREE; altura máxima é PREMIUM.

-- ── FREE: resultados de uma prova específica (pódio do torneio) ───────────
create or replace function public.resultados_prova(p_prova_id bigint)
returns setof public.resultados
language sql
stable
security definer
set search_path = public
as $$
  select * from public.resultados where prova_id = p_prova_id order by id;
$$;
grant execute on function public.resultados_prova(bigint) to anon, authenticated;

-- ── PREMIUM: histórico compilado de um CAVALO ────────────────────────────
create or replace function public.historico_cavalo(p_nome text)
returns table (
  colocacao          text,
  penalidade         text,
  tempo              text,
  cavaleiro_nome     text,
  cavalo_nome        text,
  prova_nome         text,
  prova_descricao    text,
  prova_categorias   text,
  data_prova         date,
  torneio_nome       text,
  torneio_data       date
)
language plpgsql
stable
security definer
set search_path = public
as $$
begin
  if not public.is_premium() then
    raise exception 'premium_required' using errcode = '42501';
  end if;
  return query
    select r.colocacao, r.penalidade, r.tempo, r.cavaleiro_nome, r.cavalo_nome,
           p.nome, p.descricao, p.categorias, p.data_prova,
           t.nome, t.data_inicio::date
    from public.resultados r
    join public.provas p   on p.id = r.prova_id
    left join public.torneios t on t.id = p.torneio_id
    where norm_nome(split_part(r.cavalo_nome, E'\n', 1)) = norm_nome(split_part(p_nome, E'\n', 1))
    limit 2000;
end;
$$;
grant execute on function public.historico_cavalo(text) to anon, authenticated;

-- ── PREMIUM: histórico compilado de um CAVALEIRO ─────────────────────────
create or replace function public.historico_cavaleiro(p_nome text)
returns table (
  colocacao          text,
  penalidade         text,
  tempo              text,
  cavaleiro_nome     text,
  cavalo_nome        text,
  prova_nome         text,
  prova_descricao    text,
  prova_categorias   text,
  data_prova         date,
  torneio_nome       text,
  torneio_data       date
)
language plpgsql
stable
security definer
set search_path = public
as $$
begin
  if not public.is_premium() then
    raise exception 'premium_required' using errcode = '42501';
  end if;
  return query
    select r.colocacao, r.penalidade, r.tempo, r.cavaleiro_nome, r.cavalo_nome,
           p.nome, p.descricao, p.categorias, p.data_prova,
           t.nome, t.data_inicio::date
    from public.resultados r
    join public.provas p   on p.id = r.prova_id
    left join public.torneios t on t.id = p.torneio_id
    where norm_nome(split_part(r.cavaleiro_nome, E'\n', 1)) = norm_nome(split_part(p_nome, E'\n', 1))
    limit 2000;
end;
$$;
grant execute on function public.historico_cavaleiro(text) to anon, authenticated;

-- ── progenie: nomes FREE, altura máxima/“competiu” só PREMIUM ─────────────
create or replace function public.progenie(papel text, rep text)
returns table (
  nome        text,
  sexo        text,
  nascimento  date,
  max_alt     numeric,
  competiu    boolean
)
language plpgsql
stable
security definer
set search_path = public
as $$
begin
  if public.is_premium() then
    return query
      with alt as (select cd_token, max(max_alt) as max_alt from mv_genetica group by cd_token)
      select g.nome, g.sexo, g.nascimento, a.max_alt, (a.cd_token is not null)
      from genealogia g
      left join alt a on a.cd_token = g.cd_token
      where norm_nome(case when papel = 'mae' then g.mae else g.pai end) = norm_nome(rep)
      order by a.max_alt desc nulls last, g.nome;
  else
    -- free: só os dados da ABCCH (sem revelar quem competiu nem a altura)
    return query
      select g.nome, g.sexo, g.nascimento, null::numeric, null::boolean
      from genealogia g
      where norm_nome(case when papel = 'mae' then g.mae else g.pai end) = norm_nome(rep)
      order by g.nome;
  end if;
end;
$$;
grant execute on function public.progenie(text, text) to anon, authenticated;
