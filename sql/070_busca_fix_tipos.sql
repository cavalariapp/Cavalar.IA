-- 070 — CORREÇÃO da busca por cavalo/cavaleiro (resultados.html):
--   "structure of query does not match function result type"
-- A RPC buscar_resultados declarava prova_numero INT, mas provas.numero é TEXT
-- (o scraper grava o número como string quando não é puramente numérico). O
-- Postgres recusa o retorno por incompatibilidade de tipo. Recriamos a função à
-- prova de tipos: prova_numero vira TEXT e TODAS as colunas têm cast explícito
-- pro tipo declarado (assim nunca mais quebra, independente do tipo da coluna).
-- + índices GIN trigram (aceleram o ilike '%termo%' com a tabela inflada). Aqui
-- SEM CONCURRENTLY → roda no SQL Editor numa tacada (trava as gravações por
-- alguns segundos durante a construção; o backfill espera e segue).
--
-- Rode TUDO de uma vez no Supabase → SQL Editor.

create extension if not exists pg_trgm;

create index if not exists idx_res_cavaleiro_trgm
  on public.resultados using gin (cavaleiro_nome gin_trgm_ops);
create index if not exists idx_res_cavalo_trgm
  on public.resultados using gin (cavalo_nome gin_trgm_ops);

-- mudar o TIPO de retorno (int→text) exige DROP+CREATE (não dá com replace).
drop function if exists public.buscar_resultados(text, text, text, int, int);

create function public.buscar_resultados(
  p_cavaleiro text default null,
  p_cavalo    text default null,
  p_fonte     text default null,
  p_ano       int  default null,
  p_mes       int  default null
)
returns table (
  colocacao text, cavaleiro_nome text, cavalo_nome text,
  penalidade text, tempo text, pontos text, penalidade_2 text, tempo_2 text,
  prova_id bigint, prova_nome text, prova_numero text, prova_descricao text,
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
    select r.colocacao::text, r.cavaleiro_nome::text, r.cavalo_nome::text,
           r.penalidade::text, r.tempo::text, r.pontos::text,
           r.penalidade_2::text, r.tempo_2::text,
           p.id::bigint, p.nome::text, p.numero::text, p.descricao::text,
           p.categorias::text, p.tipo_prova::text,
           t.id::bigint, t.nome::text, t.fonte::text, t.data_inicio::date
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
