-- 061 — DIAGNÓSTICO (temporário): mostra as provas de um cavalo + altura extraída,
-- pra rastrear alturas erradas (ex.: potro de 4 anos com 1,40m). SECURITY DEFINER
-- (lê resultados, que está revogado pro anon). Removeremos depois do diagnóstico.
create or replace function public.diag_altura_cavalo(p_nome text)
returns table (
  cavalo text, colocacao text, prova_nome text, descricao text, categorias text,
  alt numeric, torneio text, ano int
)
language sql stable security definer set search_path = public as $$
  select r.cavalo_nome, r.colocacao, p.nome, p.descricao, p.categorias,
         public.altura_m(p.nome, p.descricao, p.categorias),
         t.nome, extract(year from t.data_inicio)::int
  from public.resultados r
  join public.provas p on p.id = r.prova_id
  left join public.torneios t on t.id = p.torneio_id
  where norm_nome(split_part(r.cavalo_nome, E'\n', 1)) = norm_nome(split_part(p_nome, E'\n', 1))
  order by 6 desc nulls last;
$$;
grant execute on function public.diag_altura_cavalo(text) to anon, authenticated;
