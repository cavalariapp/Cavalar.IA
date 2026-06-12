-- 076 — anos disponíveis na genética (alimenta o filtro de ANO da tela de rankings,
-- que estava HARDCODED em 2024-2026). Agora o filtro mostra os anos REAIS que
-- existem na mv_genetica (incl. o histórico do backfill 2013+).
-- Também serve de DIAGNÓSTICO: rode `select public.anos_genetica();` pra ver até
-- que ano a genética tem dado. Se só vier 2024-2026, o backfill histórico não está
-- casando com a genealogia (ABCCH) — me avise.

create or replace function public.anos_genetica()
returns int[]
language sql stable security definer set search_path = public as $$
  select array_agg(ano order by ano desc)
  from (select distinct ano_prova as ano
        from public.mv_genetica
        where ano_prova is not null) s;
$$;
revoke all on function public.anos_genetica() from public;
grant execute on function public.anos_genetica() to anon, authenticated;
