-- 093 — wrapper ADMIN p/ recompor a genética pelo painel (botão "Atualizar genética").
-- O refresh_genetica() teve o execute revogado de anon/authenticated (sql/090, anti-abuso).
-- Este wrapper é gateado por is_admin() e roda sem timeout (a MV pode demorar).
create or replace function public.admin_refresh_genetica()
returns void
language plpgsql security definer
set search_path = public
set statement_timeout = 0
as $$
begin
  if not public.is_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  perform public.refresh_genetica();
end; $$;

revoke all on function public.admin_refresh_genetica() from public, anon;
grant execute on function public.admin_refresh_genetica() to authenticated;
