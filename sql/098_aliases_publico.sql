-- 098 — mapa de apelidos PÚBLICO (só nomes → nomes; nada sensível) p/ o app
-- FUNDIR os nomes de um match num único cavalo na busca (e em qualquer lista).
create or replace function public.aliases_publico()
returns table (alias_norm text, canonico_norm text)
language sql stable security definer set search_path = public as $$
  select alias_norm, canonico_norm from public.cavalo_alias;
$$;
grant execute on function public.aliases_publico() to anon, authenticated;
