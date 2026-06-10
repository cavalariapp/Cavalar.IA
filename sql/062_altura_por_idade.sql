-- 062 — VERACIDADE: trava de altura por IDADE (anti-homônimo / erro de fonte)
-- A mv_genetica casava resultados ao cavalo SÓ pelo nome → um homônimo mais velho
-- (ou erro na fonte) atribuía 1,40m a um potro de 4 anos. Agora cada resultado só
-- conta se a altura for plausível para a idade do cavalo na prova (regras de
-- Cavalos Novos da CBH, com folga). Protege rankings E progênie.

-- Altura máxima plausível por idade (anos). 8+ sem limite prático.
create or replace function public.alt_max_para_idade(idade int)
returns numeric language sql immutable as $$
  select case
    when idade is null then 1.70
    when idade <= 3 then 1.10
    when idade =  4 then 1.20
    when idade =  5 then 1.30
    when idade =  6 then 1.40
    when idade =  7 then 1.50
    else 1.70
  end;
$$;

-- Recria a mv_genetica aplicando a trava de idade por RESULTADO (antes do max).
drop materialized view if exists public.mv_genetica;
create materialized view public.mv_genetica as
with res as (
  select norm_nome(r.cavalo_nome) as filho_norm,
         extract(year from coalesce(p.data_prova, t.data_inicio))::int as ano_prova,
         public.altura_m(p.nome, p.descricao, p.categorias) as alt
  from public.resultados r
  join public.provas p on p.id = r.prova_id
  left join public.torneios t on t.id = p.torneio_id
  where r.cavalo_nome is not null
)
select g.cd_token,
       norm_nome(g.pai) as pai_norm,
       norm_nome(g.mae) as mae_norm,
       extract(year from g.nascimento)::int as nasc_ano,
       res.ano_prova,
       max(res.alt) as max_alt
from public.genealogia g
join res on res.filho_norm = norm_nome(g.nome)
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

-- remove a função de diagnóstico (lia resultados via definer — não deixar exposta)
drop function if exists public.diag_altura_cavalo(text);
