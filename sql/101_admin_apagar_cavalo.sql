-- 101 — admin_apagar_cavalo: remove uma entrada da GENEALOGIA (égua/garanhão que
-- não deveria estar nos dados). Gateado por is_admin(). Remove também a altura
-- externa do mesmo cd_token. O ranking genético reflete após o próximo refresh.
-- (Resultados de pista NÃO são apagados — genealogia é a base de criação.)
create or replace function public.admin_apagar_cavalo(p_cd_token text)
returns json
language plpgsql security definer set search_path = public as $$
declare v_nome text; v_n int;
begin
  if not public.is_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  if p_cd_token is null or btrim(p_cd_token) = '' then
    return json_build_object('ok', false, 'erro', 'cd_token obrigatório');
  end if;
  select nome into v_nome from public.genealogia where cd_token = p_cd_token;
  delete from public.genealogia where cd_token = p_cd_token;
  get diagnostics v_n = row_count;
  delete from public.altura_externa where cd_token = p_cd_token;
  return json_build_object('ok', v_n > 0, 'nome', v_nome, 'removidos', v_n);
end; $$;
revoke all on function public.admin_apagar_cavalo(text) from public, anon;
grant execute on function public.admin_apagar_cavalo(text) to authenticated;
