-- 068 — BUSCA por nome (cavalo/cavaleiro) na página de resultados voltou a quebrar
-- ("Erro na busca") porque a RPC buscar_resultados faz ilike '%termo%' em
-- resultados.cavaleiro_nome / cavalo_nome. Os índices de 059 são FUNCIONAIS sobre
-- norm_nome(split_part(...)) = só servem p/ match EXATO (historico_cavalo); o
-- ilike com wildcard à ESQUERDA não os usa → SEQ SCAN. Com a tabela inflando no
-- backfill, o scan estoura o statement_timeout. pg_trgm + GIN aceleram o ilike.
--
-- COMO RODAR (Supabase → SQL Editor): pode rodar com o backfill em andamento — os
-- índices são CONCURRENTLY (NÃO travam as gravações). Se o editor reclamar de
-- "CREATE INDEX CONCURRENTLY cannot run inside a transaction block", rode CADA
-- comando CONCURRENTLY SOZINHO (um de cada vez).

create extension if not exists pg_trgm;

-- ilike '%...%' acelerado por trigramas (substring, case-insensitive)
create index concurrently if not exists idx_res_cavaleiro_trgm
  on public.resultados using gin (cavaleiro_nome gin_trgm_ops);

create index concurrently if not exists idx_res_cavalo_trgm
  on public.resultados using gin (cavalo_nome gin_trgm_ops);

-- folga no timeout da RPC (belt-and-suspenders; com o índice já fica rápida).
-- Recriação idêntica à 049, só adicionando statement_timeout.
create or replace function public.buscar_resultados(
  p_cavaleiro text default null,
  p_cavalo    text default null,
  p_fonte     text default null,
  p_ano       int  default null,
  p_mes       int  default null
)
returns table (
  colocacao text, cavaleiro_nome text, cavalo_nome text,
  penalidade text, tempo text, pontos text, penalidade_2 text, tempo_2 text,
  prova_id bigint, prova_nome text, prova_numero int, prova_descricao text,
  prova_categorias text, prova_tipo text,
  torneio_id bigint, torneio_nome text, torneio_fonte text, torneio_data date
)
language plpgsql stable security definer set search_path = public
set statement_timeout = '20s' as $$
begin
  if not public.is_premium() then
    raise exception 'premium_required' using errcode = '42501';
  end if;
  return query
    select r.colocacao, r.cavaleiro_nome, r.cavalo_nome,
           r.penalidade, r.tempo, r.pontos::text, r.penalidade_2, r.tempo_2,
           p.id, p.nome, p.numero, p.descricao, p.categorias, p.tipo_prova,
           t.id, t.nome, t.fonte, t.data_inicio::date
    from resultados r
    join provas p   on p.id = r.prova_id
    join torneios t on t.id = p.torneio_id
    where (p_cavaleiro is null or r.cavaleiro_nome ilike '%' || p_cavaleiro || '%')
      and (p_cavalo    is null or r.cavalo_nome    ilike '%' || p_cavalo    || '%')
      and (p_fonte is null or t.fonte = p_fonte)
      and (p_ano   is null or extract(year  from t.data_inicio) = p_ano)
      and (p_mes   is null or extract(month from t.data_inicio) = p_mes)
    limit 500;
end;
$$;
grant execute on function public.buscar_resultados(text, text, text, int, int) to anon, authenticated;
