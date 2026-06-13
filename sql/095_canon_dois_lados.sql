-- 095 — APELIDO em AMBOS os lados do casamento genético (libera a "direção").
-- Antes: mv_genetica casava canon(nome_resultado) = norm(nome_genealogia). Só o lado
-- dos RESULTADOS passava pelo apelido → o "canônico" PRECISAVA ser o nome da genética,
-- senão a matriz perdia o crédito do filho. Agora aplicamos canon() TAMBÉM no lado da
-- genealogia → os dois nomes se encontram no mesmo canônico INDEPENDENTE da direção
-- escolhida no "Match de Cavalos". (Resultados e perfil já eram agnósticos à direção.)
--
-- Pré-requisito: canon_cavalo (082). Recria a mv com a MESMA definição da 079, mudando
-- só a linha do JOIN. Rode + depois: select public.refresh_genetica();

drop materialized view if exists public.mv_genetica;
create materialized view public.mv_genetica as
with res as (
  select public.canon_cavalo(r.cavalo_norm) as filho_norm,
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
       g.pai_token,
       g.mae_token,
       extract(year from g.nascimento)::int as nasc_ano,
       res.ano_prova,
       max(res.alt) as max_alt
from public.genealogia g
join res on res.filho_norm = public.canon_cavalo(norm_nome(g.nome))   -- ← canon nos DOIS lados
  and (
    res.nasc_res is null or g.nascimento is null
    or res.nasc_res = g.nascimento
    or (extract(year from res.nasc_res) = extract(year from g.nascimento)
        and ( (extract(month from res.nasc_res) = 1 and extract(day from res.nasc_res) = 1)
           or (extract(month from g.nascimento)  = 1 and extract(day from g.nascimento)  = 1) ))
  )
where res.alt is not null
  and (
    g.nascimento is null or res.ano_prova is null
    or res.alt <= public.alt_max_para_idade(res.ano_prova - extract(year from g.nascimento)::int)
  )
group by g.cd_token, norm_nome(g.pai), norm_nome(g.mae), g.pai_token, g.mae_token,
         extract(year from g.nascimento)::int, res.ano_prova;

create index if not exists mv_genetica_pai_idx       on public.mv_genetica (pai_norm);
create index if not exists mv_genetica_mae_idx       on public.mv_genetica (mae_norm);
create index if not exists mv_genetica_cd_idx        on public.mv_genetica (cd_token);
create index if not exists mv_genetica_paitoken_idx  on public.mv_genetica (pai_token);
create index if not exists mv_genetica_maetoken_idx  on public.mv_genetica (mae_token);

select public.refresh_genetica();
