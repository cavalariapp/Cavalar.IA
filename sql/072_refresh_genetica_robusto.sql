-- 072 — ROBUSTEZ do refresh da genética.
-- A função refresh_genetica() faz REFRESH MATERIALIZED VIEW (recalcula a view
-- inteira). Com a tabela `resultados` muito maior pós-backfill, o refresh pode
-- passar de alguns segundos. Se for chamado por um papel com statement_timeout
-- curto (o default da role authenticated costuma ser baixo), o Postgres CANCELA o
-- refresh no meio → a genética nunca atualiza, sem erro visível. Fixamos
-- statement_timeout = 0 (sem limite) DENTRO da função, garantindo que ela SEMPRE
-- conclua, independente de quem chama. Sem mudança de comportamento — só garante
-- que termina. (REFRESH puro: não-CONCURRENTLY, pois CONCURRENTLY não roda dentro
-- de transação/RPC; o lock é breve e só durante o refresh.)

create or replace function public.refresh_genetica()
returns void
language sql
security definer
set search_path = public
set statement_timeout = 0 as $$
  refresh materialized view public.mv_genetica;
$$;

grant execute on function public.refresh_genetica() to authenticated, service_role;
