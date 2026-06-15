-- 104 — "Apagar cavalo" também REMOVE DO RANKING genético.
-- O ranking de matrizes/garanhões é montado a partir do campo mãe/pai dos FILHOS —
-- não da ficha do reprodutor. Logo, apagar a entrada da égua na genealogia NÃO a tira
-- do ranking. A exclusão do ranking é feita marcando o NOME como rep_placeholder
-- (o ranking faz `where not _rep_placeholder(...)`). Agora o apagar faz as duas coisas.
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
  -- exclui o NOME do ranking (não é matriz/garanhão de verdade)
  if v_nome is not null then
    insert into public.rep_placeholder (nome_norm) values (norm_nome(v_nome))
    on conflict (nome_norm) do nothing;
  end if;
  delete from public.genealogia where cd_token = p_cd_token;
  get diagnostics v_n = row_count;
  delete from public.altura_externa where cd_token = p_cd_token;
  return json_build_object('ok', v_n > 0, 'nome', v_nome, 'removidos', v_n);
end; $$;
revoke all on function public.admin_apagar_cavalo(text) from public, anon;
grant execute on function public.admin_apagar_cavalo(text) to authenticated;
