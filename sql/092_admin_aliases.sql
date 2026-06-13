-- 092 — RPCs ADMIN p/ a tela "Match de Cavalos" (apelidos).
-- set_cavalo_alias(alias, canonico) JÁ existe (sql/082). Aqui só listar + remover.
-- Tudo gateado por is_admin() (a tabela cavalo_alias tem RLS sem policy).

create or replace function public.admin_listar_aliases()
returns table (alias_norm text, canonico_norm text, criado_em timestamptz)
language plpgsql stable security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  return query
    select a.alias_norm, a.canonico_norm, a.criado_em
    from public.cavalo_alias a order by a.criado_em desc;
end; $$;

create or replace function public.admin_remover_alias(p_alias text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  delete from public.cavalo_alias where alias_norm = norm_nome(p_alias);
end; $$;

revoke all on function public.admin_listar_aliases()      from public, anon;
revoke all on function public.admin_remover_alias(text)   from public, anon;
grant execute on function public.admin_listar_aliases()    to authenticated;
grant execute on function public.admin_remover_alias(text) to authenticated;
