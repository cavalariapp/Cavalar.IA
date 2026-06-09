-- 048 — RPC FREE: todos os resultados de UM torneio (browse do pódio do evento)
-- Free pode ver o resultado de um torneio inteiro; o que é premium é COMPILAR
-- por cavalo/cavaleiro (historico_*). Esta RPC alimenta o browse de resultados.html
-- sem expor a tabela `resultados` ao acesso direto (que será revogado na 049).
create or replace function public.resultados_torneio(p_torneio_id bigint)
returns setof public.resultados
language sql
stable
security definer
set search_path = public
as $$
  select r.*
  from public.resultados r
  join public.provas p on p.id = r.prova_id
  where p.torneio_id = p_torneio_id
  order by r.prova_id, r.id;
$$;
grant execute on function public.resultados_torneio(bigint) to anon, authenticated;
