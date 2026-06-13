-- 091 — REDE DE SEGURANÇA (parte 1): detector de "buracos" de resultado.
-- Lista toda prova JÁ REALIZADA, COM inscritos (ordem de entrada) e SEM resultado.
-- É a "lista do que falta" — visível a qualquer momento e usada pelo scraper
-- (--curar-buracos) pra re-raspar exatamente essas provas (auto-cura).

create or replace view public.provas_incompletas as
select p.id            as prova_id,
       p.id_origem,
       p.torneio_id,
       t.fonte,
       t.nome          as torneio,
       p.numero,
       p.descricao,
       coalesce(p.data_prova, t.data_inicio) as data_ref,
       (select count(*) from public.ordem_entrada o where o.prova_id = p.id) as n_ordem
from public.provas p
join public.torneios t on t.id = p.torneio_id
where coalesce(p.data_prova, t.data_inicio) < current_date          -- já aconteceu
  and p.id_origem is not null                                       -- dá pra re-raspar
  and exists (select 1 from public.ordem_entrada o where o.prova_id = p.id)   -- teve gente
  and not exists (select 1 from public.resultados r where r.prova_id = p.id); -- sem resultado

-- view respeita a RLS de quem chama (tabelas-base são leitura pública).
alter view public.provas_incompletas set (security_invoker = on);

-- leitura p/ painel admin (dado de prova é público); o scraper usa service_role.
grant select on public.provas_incompletas to anon, authenticated;
