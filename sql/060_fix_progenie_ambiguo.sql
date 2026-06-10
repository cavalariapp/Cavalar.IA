-- 060 — corrige "column reference max_alt is ambiguous" na progenie (premium)
-- O nome da coluna de saída (max_alt) colidia com mv_genetica.max_alt. Aliasamos
-- as colunas do CTE (cd/malt) e qualificamos tudo. Free: nome/idade/genealogia/
-- progênie (sem altura). Premium: + altura máxima de cada filho + (competiu).
create or replace function public.progenie(papel text, rep text)
returns table (nome text, sexo text, nascimento date, max_alt numeric, competiu boolean)
language plpgsql
stable
security definer
set search_path = public
as $$
begin
  if public.is_premium() then
    return query
      with alt as (
        select mv.cd_token as cd, max(mv.max_alt) as malt
        from mv_genetica mv
        group by mv.cd_token
      )
      select g.nome, g.sexo, g.nascimento, a.malt, (a.cd is not null)
      from genealogia g
      left join alt a on a.cd = g.cd_token
      where norm_nome(case when papel = 'mae' then g.mae else g.pai end) = norm_nome(rep)
      order by a.malt desc nulls last, g.nome;
  else
    return query
      select g.nome, g.sexo, g.nascimento, null::numeric, null::boolean
      from genealogia g
      where norm_nome(case when papel = 'mae' then g.mae else g.pai end) = norm_nome(rep)
      order by g.nome;
  end if;
end;
$$;
grant execute on function public.progenie(text, text) to anon, authenticated;
