-- 074 — VIRA A CHAVE da genética rápida. ⚠️ Rode SÓ DEPOIS de:
--   1) ter rodado o sql/073 (cria as colunas pré-computadas + mv_genetica_v2);
--   2) ter rodado a QUERY DE COMPARAÇÃO (no fim do 073) e ela voltar 0 LINHAS.
-- Se a comparação não for 0, NÃO rode isto — me avise as diferenças.
--
-- Redefine a mv_genetica usando as colunas pré-computadas (mesma definição da v2),
-- com os MESMOS nomes de colunas → rankings_geneticos e progenie continuam iguais,
-- sem alterar nada neles. O refresh_genetica (sql/072) passa a recompilar essa
-- versão rápida. Por fim, descarta a mv_genetica_v2 (não é mais necessária).

drop materialized view if exists public.mv_genetica;
create materialized view public.mv_genetica as
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

create index if not exists mv_genetica_pai_idx on public.mv_genetica (pai_norm);
create index if not exists mv_genetica_mae_idx on public.mv_genetica (mae_norm);
create index if not exists mv_genetica_cd_idx  on public.mv_genetica (cd_token);

drop materialized view if exists public.mv_genetica_v2;
