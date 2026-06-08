-- 035 — PROGÊNIE de um reprodutor com a ALTURA MÁXIMA competida por cada filho
-- Usada no perfil do cavalo (reprodutor): lista os filhos e, ao lado de cada um,
-- a maior altura que ele saltou no nosso sistema. Rápida: a altura vem da
-- materialized view mv_genetica (já agregada), não dos 138k resultados.

create or replace function public.progenie(papel text, rep text)
returns table (
  nome        text,
  sexo        text,
  nascimento  date,
  max_alt     numeric,   -- maior altura competida (NULL se não competiu/sem dado)
  competiu    boolean
)
language sql stable security definer set search_path = public as $$
  with alt as (
    select cd_token, max(max_alt) as max_alt
    from mv_genetica
    group by cd_token
  )
  select g.nome, g.sexo, g.nascimento, a.max_alt, (a.cd_token is not null)
  from genealogia g
  left join alt a on a.cd_token = g.cd_token
  where norm_nome(case when papel = 'mae' then g.mae else g.pai end) = norm_nome(rep)
  order by a.max_alt desc nulls last, g.nome;
$$;

grant execute on function public.progenie(text, text) to anon, authenticated;
