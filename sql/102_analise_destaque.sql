-- 102 — analise_destaque: insight REAL p/ a caixa "Análise IA" (substitui o texto
-- fictício). Usa a mv_genetica (rápida) p/ achar a MATRIZ destaque do ano: a que tem
-- mais filhos competindo e saltando ≥1,40m. (Métrica de FALTAS/consistência fica p/ a
-- fase 2 — exige stats de faltas na base genética.)
create or replace function public.analise_destaque(p_ano int default null)
returns json
language sql stable security definer set search_path = public set statement_timeout = '15s'
as $$
  with base as (
    select coalesce(nullif(mae_token, ''), mae_norm) as rep_key,
           mae_norm as rep_disp, cd_token, max_alt
    from public.mv_genetica
    where mae_norm is not null and mae_norm <> ''
      and not public._rep_placeholder(mae_norm)
      and (p_ano is null or ano_prova = p_ano)
  ),
  agg as (
    select rep_key, max(rep_disp) as nome,
           count(distinct cd_token) as filhos_comp,
           count(distinct cd_token) filter (where max_alt >= 1.40) as filhos_140
    from base group by rep_key
    having count(distinct cd_token) >= 3
    order by filhos_140 desc, filhos_comp desc
    limit 1
  )
  select case when not exists (select 1 from agg) then null else (
    select json_build_object(
      'headline', 'Matriz destaque' || coalesce(' ' || p_ano::text, '') || ': ' || initcap(lower(nome)),
      'summary', initcap(lower(nome)) || ' tem ' || filhos_comp || ' filho(s) competindo' ||
                 case when filhos_140 > 0 then ' — ' || filhos_140 || ' saltando 1,40m ou mais' else '' end ||
                 coalesce(' em ' || p_ano::text, ' (histórico)') || '. Dados reais do Cavalar.IA cruzando o studbook ABCCH com os resultados das pistas.',
      'tags', json_build_array('Genética', 'Matrizes', coalesce(p_ano::text, 'Histórico'))
    ) from agg)
  end;
$$;
grant execute on function public.analise_destaque(int) to anon, authenticated;
