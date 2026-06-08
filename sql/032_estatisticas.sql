-- 032 — ESTATÍSTICAS reais p/ o hero da home
-- Conta cavaleiros/cavalos DISTINTOS (sobre ~138k resultados) no servidor — o
-- cliente não tem como fazer count(distinct) eficiente. cavaleiro_nome/cavalo_nome
-- são "NOME\nENTIDADE"/"NOME\nGENEALOGIA"; conta só o NOME (antes do \n), em
-- maiúsculas, pra não inflar por variação de entidade/genealogia.

create or replace function public.estatisticas_app()
returns json
language sql
stable
security definer
set search_path = public
as $$
  select json_build_object(
    'torneios',   (select count(*) from torneios),
    'resultados', (select count(*) from resultados),
    'cavaleiros', (select count(distinct upper(split_part(cavaleiro_nome, E'\n', 1)))
                     from resultados where coalesce(cavaleiro_nome,'') <> ''),
    'cavalos',    (select count(distinct upper(split_part(cavalo_nome, E'\n', 1)))
                     from resultados where coalesce(cavalo_nome,'') <> '')
  );
$$;

grant execute on function public.estatisticas_app() to anon, authenticated;
