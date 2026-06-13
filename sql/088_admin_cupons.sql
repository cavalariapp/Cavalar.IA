-- 088 — RPCs ADMIN para gerenciar cupons pelo painel (sem SQL na mão).
-- A tabela cupons tem RLS sem policy (só service_role/RPC). Estas funções são
-- SECURITY DEFINER e gateadas por is_admin() — não-admin recebe erro, nunca dados.

-- listar todos os cupons (admin)
create or replace function public.admin_listar_cupons()
returns table (
  codigo text, dias int, vitalicio boolean, max_usos int, usos int,
  ativo boolean, expira_em date, descricao text, criado_em timestamptz
)
language plpgsql stable security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  return query
    select c.codigo, c.dias, c.vitalicio, c.max_usos, c.usos,
           c.ativo, c.expira_em, c.descricao, c.criado_em
    from public.cupons c order by c.criado_em desc;
end; $$;

-- criar cupom (admin). vitalício → ignora dias.
create or replace function public.admin_criar_cupom(
  p_codigo text, p_vitalicio boolean default false, p_dias int default 30,
  p_max_usos int default null, p_descricao text default null, p_expira_em date default null
) returns json
language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  if p_codigo is null or btrim(p_codigo) = '' then
    return json_build_object('ok', false, 'erro', 'Informe o código.');
  end if;
  insert into public.cupons (codigo, dias, vitalicio, max_usos, descricao, expira_em, ativo)
  values (
    upper(btrim(p_codigo)),
    case when coalesce(p_vitalicio, false) then null else coalesce(p_dias, 30) end,
    coalesce(p_vitalicio, false),
    p_max_usos, nullif(btrim(coalesce(p_descricao, '')), ''), p_expira_em, true
  )
  on conflict (codigo) do nothing;
  if not found then return json_build_object('ok', false, 'erro', 'Já existe um cupom com esse código.'); end if;
  return json_build_object('ok', true);
end; $$;

-- ativar/desativar cupom (admin)
create or replace function public.admin_set_cupom_ativo(p_codigo text, p_ativo boolean)
returns void
language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  update public.cupons set ativo = coalesce(p_ativo, false) where codigo = upper(btrim(p_codigo));
end; $$;

revoke all on function public.admin_listar_cupons()                       from public, anon;
revoke all on function public.admin_criar_cupom(text,boolean,int,int,text,date) from public, anon;
revoke all on function public.admin_set_cupom_ativo(text,boolean)         from public, anon;
grant execute on function public.admin_listar_cupons()                       to authenticated;
grant execute on function public.admin_criar_cupom(text,boolean,int,int,text,date) to authenticated;
grant execute on function public.admin_set_cupom_ativo(text,boolean)         to authenticated;
