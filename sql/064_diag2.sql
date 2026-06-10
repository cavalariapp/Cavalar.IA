-- 064 — diagnóstico (temporário) p/ achar a raiz do mapeamento errado de resultados.
-- (1) resultados de um cavalo: nome EXATO + prova + altura.
create or replace function public.diag_cavalo(p_nome text)
returns table (cavalo_exato text, prova_id bigint, prova_nome text, alt numeric, torneio text)
language sql stable security definer set search_path = public as $$
  select r.cavalo_nome, r.prova_id, p.nome,
         public.altura_m(p.nome, p.descricao, p.categorias), t.nome
  from public.resultados r
  join public.provas p on p.id = r.prova_id
  left join public.torneios t on t.id = p.torneio_id
  where norm_nome(split_part(r.cavalo_nome, E'\n', 1)) = norm_nome(split_part(p_nome, E'\n', 1));
$$;
grant execute on function public.diag_cavalo(text) to anon, authenticated;

-- (2) por torneio: cada prova + quantos resultados tem (mostra se as CN4 ficaram vazias).
create or replace function public.diag_torneio(p_tid bigint)
returns table (prova_id bigint, prova_nome text, alt numeric, n_resultados bigint)
language sql stable security definer set search_path = public as $$
  select p.id, p.nome, public.altura_m(p.nome, p.descricao, p.categorias),
         (select count(*) from public.resultados r where r.prova_id = p.id)
  from public.provas p
  where p.torneio_id = p_tid
  order by p.nome;
$$;
grant execute on function public.diag_torneio(bigint) to anon, authenticated;
