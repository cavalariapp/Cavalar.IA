-- 073 — ADITIVO (não muda nada existente): acelera a genética pré-computando, na
-- própria `resultados`, o que hoje a mv_genetica recalcula por linha A CADA refresh
-- (a regex do nascimento + a normalização do nome). Cria uma mv_genetica_v2 que usa
-- essas colunas → refresh MUITO mais rápido, com a MESMA lógica de casamento (logo,
-- MESMO resultado). A v2 roda EM PARALELO à atual; só viramos a chave (074) depois
-- de você rodar a query de comparação no fim e confirmar 0 diferenças.
--
-- As colunas são GERADAS pelo Postgres a partir de cavalo_nome usando as MESMAS
-- funções da mv atual (norm_nome / regex de data) → impossível divergir, e se
-- mantêm sozinhas em todo insert/update (sem mexer no scraper).
--
-- ⚠️ ADD COLUMN GENERATED reescreve a tabela uma vez (lock breve). Rode quando o
-- backfill/scraper NÃO estiver gravando pesado (ex.: agora, antes do bloco 3).

-- helper IMMUTABLE: nascimento que a linha do resultado carrega (2ª linha de
-- cavalo_nome = "DD/MM/AAAA | Sexo | ..."). Valida mês/dia. Reutilizável e testável.
create or replace function public.nasc_cavalo_de(p_cavalo_nome text)
returns date language plpgsql immutable as $$
declare m text[];
begin
  m := regexp_match(split_part(coalesce(p_cavalo_nome, ''), E'\n', 2),
                    '(\d{2})/(\d{2})/(\d{4})');
  if m is null then
    return null;
  end if;
  return make_date((m[3])::int, (m[2])::int, (m[1])::int);
exception when others then
  return null;   -- data impossível (ex.: 31/04/2020) → ignora, não quebra insert/refresh
end;
$$;

-- colunas pré-computadas (geradas/armazenadas). norm_nome e nasc_cavalo_de são
-- IMMUTABLE → válidas em coluna gerada. Idempotente (IF NOT EXISTS).
alter table public.resultados
  add column if not exists cavalo_norm text
    generated always as (public.norm_nome(split_part(cavalo_nome, E'\n', 1))) stored;

alter table public.resultados
  add column if not exists nasc_cavalo date
    generated always as (public.nasc_cavalo_de(cavalo_nome)) stored;

create index if not exists idx_res_cavalonorm_nasc
  on public.resultados (cavalo_norm, nasc_cavalo);

-- mv_genetica_v2: ESTRUTURA IDÊNTICA à mv_genetica (sql/067), só trocando o cálculo
-- inline (norm_nome(cavalo_nome) + regex do nascimento) pelas colunas pré-computadas
-- (r.cavalo_norm / r.nasc_cavalo). Mantém a trava de idade (062) e o casamento por
-- nascimento (067) — logo, mesmo conteúdo, refresh muito mais rápido.
drop materialized view if exists public.mv_genetica_v2;
create materialized view public.mv_genetica_v2 as
with res as (
  select r.cavalo_norm as filho_norm,
         r.nasc_cavalo  as nasc_res,
         extract(year from coalesce(p.data_prova, t.data_inicio))::int as ano_prova,
         public.altura_m(p.nome, p.descricao, p.categorias) as alt
  from public.resultados r
  join public.provas p on p.id = r.prova_id
  left join public.torneios t on t.id = p.torneio_id
  where r.cavalo_norm is not null
)
select g.cd_token,
       norm_nome(g.pai) as pai_norm,
       norm_nome(g.mae) as mae_norm,
       extract(year from g.nascimento)::int as nasc_ano,
       res.ano_prova,
       max(res.alt) as max_alt
from public.genealogia g
join res on res.filho_norm = norm_nome(g.nome)
  and (
    res.nasc_res is null or g.nascimento is null
    or res.nasc_res = g.nascimento
    or (extract(year from res.nasc_res) = extract(year from g.nascimento)
        and ( (extract(month from res.nasc_res) = 1 and extract(day from res.nasc_res) = 1)
           or (extract(month from g.nascimento)  = 1 and extract(day from g.nascimento)  = 1) ))
  )
where res.alt is not null
  and (
    g.nascimento is null
    or res.ano_prova is null
    or res.alt <= public.alt_max_para_idade(res.ano_prova - extract(year from g.nascimento)::int)
  )
group by g.cd_token, norm_nome(g.pai), norm_nome(g.mae),
         extract(year from g.nascimento)::int, res.ano_prova;

create index if not exists mv_genetica_v2_pai_idx on public.mv_genetica_v2 (pai_norm);
create index if not exists mv_genetica_v2_mae_idx on public.mv_genetica_v2 (mae_norm);
create index if not exists mv_genetica_v2_cd_idx  on public.mv_genetica_v2 (cd_token);

-- COMPARAÇÃO (rode DEPOIS, separado): lista onde a v2 difere da atual. 0 linhas =
-- idênticas → seguro virar a chave (074). Esperado: 0 (ou só casos raros de
-- homônimo de MESMO nascimento, que a v2 trata de forma mais limpa).
--   select coalesce(o.cd_token,n.cd_token) cd, coalesce(o.ano_prova,n.ano_prova) ano,
--          o.malt old_alt, n.malt new_alt
--   from (select cd_token,ano_prova,max(max_alt) malt from public.mv_genetica    group by 1,2) o
--   full join (select cd_token,ano_prova,max(max_alt) malt from public.mv_genetica_v2 group by 1,2) n
--     on o.cd_token=n.cd_token and o.ano_prova is not distinct from n.ano_prova
--   where o.malt is distinct from n.malt
--   limit 200;
