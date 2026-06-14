-- 100 — estatisticas_app RÁPIDA (corrige o '—' no hero da home).
-- A versão antiga fazia count(distinct upper(split_part(...))) — expressão por linha
-- sobre os resultados inchados no backfill → timeout (57014) → o hero ficava em '—'.
-- Agora:
--   • cavalos    = count(distinct cavalo_norm)  → usa a coluna GERADA indexada (073).
--   • cavaleiros = count(distinct norm_nome(split_part(cavaleiro_nome,'\n',1)))
--                  → usa o índice de expressão idx_res_cavaleiro_norm (059).
--   • resultados = estimativa instantânea (reltuples) — número grande do hero não
--                  precisa ser exato.
--   • torneios   = count(*) (tabela pequena).
-- statement_timeout generoso por garantia (os distinct rodam em poucos segundos).
create or replace function public.estatisticas_app()
returns json
language sql
stable
security definer
set search_path = public
set statement_timeout = '60s'
as $$
  select json_build_object(
    'torneios',   (select count(*) from public.torneios),
    'resultados', (select greatest(reltuples, 0)::bigint
                     from pg_class where oid = 'public.resultados'::regclass),
    'cavaleiros', (select count(distinct norm_nome(split_part(cavaleiro_nome, E'\n', 1)))
                     from public.resultados where coalesce(cavaleiro_nome,'') <> ''),
    'cavalos',    (select count(distinct cavalo_norm)
                     from public.resultados where cavalo_norm is not null)
  );
$$;
grant execute on function public.estatisticas_app() to anon, authenticated;
