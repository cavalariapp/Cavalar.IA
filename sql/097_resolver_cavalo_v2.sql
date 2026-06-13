-- 097 — resolver_cavalo v2: acha a entrada de genealogia de QUALQUER nome do grupo
-- do match (canônico OU apelidos), nos DOIS sentidos.
-- BUG da v1 (096): só procurava o CANÔNICO na genealogia. Quando o admin escolhe como
-- canônico o nome que está só em RESULTADOS (ex.: "LIKA JT PB"), a genética — que está
-- sob o apelido "LIKA JT" — não era encontrada → perfil sem genética.
-- Agora monta o grupo {canônico} ∪ {apelidos do canônico} e pega a entrada de
-- genealogia de qualquer um deles (preferindo a que tem cd_token).
create or replace function public.resolver_cavalo(p_nome text)
returns table (nome text, cd_token text, nascimento date, sexo text, pai text, mae text)
language plpgsql stable security definer set search_path = public as $$
declare v_canon text := public.canon_cavalo(norm_nome(split_part(p_nome, E'\n', 1)));
begin
  return query
    select g.nome, g.cd_token, g.nascimento, g.sexo, g.pai, g.mae
    from public.genealogia g
    where norm_nome(g.nome) = v_canon
       or norm_nome(g.nome) in (select alias_norm from public.cavalo_alias where canonico_norm = v_canon)
    order by (g.cd_token is not null) desc, g.nascimento nulls last
    limit 1;
end; $$;
grant execute on function public.resolver_cavalo(text) to anon, authenticated;
