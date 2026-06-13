-- 084 — PERFORMANCE: índices que faltavam (tabelas inflaram no backfill) + conserta o
-- historico_cavalo (a 082 envolveu o nome em canon_cavalo() e perdeu o índice → seq scan).
--
-- Os dois CREATE INDEX são CONCURRENTLY (não travam gravações). Se o SQL Editor
-- reclamar de transação, rode CADA um SOZINHO.

-- (1) provas.torneio_id — usado em TODA contagem/listagem de provas por torneio
--     (página de resultados, cura, etc.). FK não cria índice no Postgres.
create index concurrently if not exists idx_provas_torneio_id on public.provas (torneio_id);

-- (2) torneios.data_inicio — ordenação/filtro de milhares de torneios.
create index concurrently if not exists idx_torneios_data_inicio on public.torneios (data_inicio);

-- (3) historico_cavalo: volta a usar o índice idx_res_cavalonorm_nasc (coluna gerada
--     cavalo_norm). Em vez de canon_cavalo(norm_nome(...)) no WHERE (não-indexável),
--     expande o apelido em CONJUNTO: cavalo_norm = canônico OU cavalo_norm IN (apelidos
--     que mapeiam pro canônico). Mesmo resultado, agora por índice.
create or replace function public.historico_cavalo(p_nome text)
returns table (
  colocacao text, penalidade text, tempo text, cavaleiro_nome text, cavalo_nome text,
  prova_nome text, prova_descricao text, prova_categorias text, data_prova date,
  torneio_nome text, torneio_data date
)
language plpgsql stable security definer set search_path = public as $$
declare v_nasc int; v_key text := public.canon_cavalo(norm_nome(split_part(p_nome, E'\n', 1)));
begin
  if not public.is_premium() then
    raise exception 'premium_required' using errcode = '42501';
  end if;
  select min(extract(year from g.nascimento))::int into v_nasc
  from genealogia g
  where norm_nome(g.nome) = v_key
     or norm_nome(g.nome) in (select alias_norm from public.cavalo_alias where canonico_norm = v_key);

  return query
    select r.colocacao, r.penalidade, r.tempo, r.cavaleiro_nome, r.cavalo_nome,
           p.nome, p.descricao, p.categorias, p.data_prova,
           t.nome, t.data_inicio::date
    from resultados r
    join provas p   on p.id = r.prova_id
    left join torneios t on t.id = p.torneio_id
    where (r.cavalo_norm = v_key
           or r.cavalo_norm in (select alias_norm from public.cavalo_alias where canonico_norm = v_key))
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
