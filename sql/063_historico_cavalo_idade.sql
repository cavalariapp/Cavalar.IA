-- 063 — aplica a trava de idade também no historico_cavalo (lista de resultados
-- da página do cavalo). Sem isso, resultados de homônimo mais velho (1,40m num
-- potro de 4 anos) ainda apareciam na aba "Resultados". Mesma regra da mv_genetica.
create or replace function public.historico_cavalo(p_nome text)
returns table (
  colocacao text, penalidade text, tempo text, cavaleiro_nome text, cavalo_nome text,
  prova_nome text, prova_descricao text, prova_categorias text, data_prova date,
  torneio_nome text, torneio_data date
)
language plpgsql stable security definer set search_path = public as $$
declare v_nasc int;
begin
  if not public.is_premium() then
    raise exception 'premium_required' using errcode = '42501';
  end if;
  -- ano de nascimento do cavalo (genealogia). Se houver homônimos, usa o menor (lenient).
  select min(extract(year from g.nascimento))::int into v_nasc
  from genealogia g
  where norm_nome(g.nome) = norm_nome(split_part(p_nome, E'\n', 1));

  return query
    select r.colocacao, r.penalidade, r.tempo, r.cavaleiro_nome, r.cavalo_nome,
           p.nome, p.descricao, p.categorias, p.data_prova,
           t.nome, t.data_inicio::date
    from resultados r
    join provas p   on p.id = r.prova_id
    left join torneios t on t.id = p.torneio_id
    where norm_nome(split_part(r.cavalo_nome, E'\n', 1)) = norm_nome(split_part(p_nome, E'\n', 1))
      and (
        v_nasc is null
        or public.altura_m(p.nome, p.descricao, p.categorias) is null
        or extract(year from coalesce(p.data_prova, t.data_inicio)) is null
        or public.altura_m(p.nome, p.descricao, p.categorias)
             <= public.alt_max_para_idade(extract(year from coalesce(p.data_prova, t.data_inicio))::int - v_nasc)
      )
    limit 2000;
end;
$$;
grant execute on function public.historico_cavalo(text) to anon, authenticated;
