-- 096 — resolver_cavalo: dado um nome (variante OU canônico), devolve a entrada
-- CANÔNICA na genealogia. O app usa isso pra abrir SEMPRE a MESMA página pros dois
-- nomes de um match (ex.: clicar em "Lika JT PB" abre a página da "Lika JT").
-- Pública (dado de genealogia já é público); só nomes/registro, nada sensível.
create or replace function public.resolver_cavalo(p_nome text)
returns table (nome text, cd_token text, nascimento date, sexo text, pai text, mae text)
language plpgsql stable security definer set search_path = public as $$
declare v_canon text := public.canon_cavalo(norm_nome(split_part(p_nome, E'\n', 1)));
begin
  return query
    select g.nome, g.cd_token, g.nascimento, g.sexo, g.pai, g.mae
    from public.genealogia g
    where norm_nome(g.nome) = v_canon
    order by (g.cd_token is not null) desc, g.nascimento nulls last
    limit 1;
end; $$;
grant execute on function public.resolver_cavalo(text) to anon, authenticated;
