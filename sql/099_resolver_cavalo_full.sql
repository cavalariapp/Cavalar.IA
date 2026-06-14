-- 099 — resolver_cavalo_full: resolução ÚNICA e completa do indivíduo CANÔNICO.
--
-- PROBLEMA (busca mostrava só performance OU só genética; nome às vezes não-canônico):
--   * resolver_cavalo (097) devolve a entrada de GENEALOGIA do grupo do match, mas o
--     campo `nome` é o nome da genealogia (que pode ser o APELIDO, não o canônico) —
--     então o cabeçalho exibia o apelido em vez do canônico (ex.: "QUALMATIE DU BRIEL"
--     em vez de "QUALMATIE DU BREIL").
--   * o nome canônico definido no match (canonico_norm) às vezes só existe em
--     RESULTADOS (ex.: "LIKA JT PB"), nunca na genealogia → não havia de onde tirar a
--     grafia "bonita" do canônico.
--
-- SOLUÇÃO: uma RPC que, para QUALQUER nome do grupo, devolve:
--   - display_nome: a grafia CANÔNICA para exibir (preferindo a 1ª linha de um
--     resultado cujo norm == canonico_norm; se não houver em resultados, usa a
--     genealogia do canônico; se nem isso, usa a entrada de genealogia do grupo).
--   - os campos de genealogia (cd_token, nascimento, sexo, pai, mae) do indivíduo
--     (entrada com cd_token / nascimento mais informativa do grupo).
--   - tem_gen: se existe QUALQUER entrada de genealogia no grupo (→ mostra aba/genética).
--
-- Assim BUSCA e RANKING chamam a MESMA resolução e abrem a MESMA página completa.
create or replace function public.resolver_cavalo_full(p_nome text)
returns table (
  display_nome text,
  canonico_norm text,
  cd_token text,
  nascimento date,
  sexo text,
  pai text,
  mae text,
  tem_gen boolean,
  tem_alias boolean
)
language plpgsql stable security definer set search_path = public as $$
declare
  v_canon text := public.canon_cavalo(norm_nome(split_part(p_nome, E'\n', 1)));
  v_grp   text[];
  v_disp  text;
  g       record;
begin
  -- grupo do match = canônico ∪ apelidos do canônico
  v_grp := array(
    select v_canon
    union
    select alias_norm from public.cavalo_alias where canonico_norm = v_canon
  );

  -- grafia canônica p/ exibir:
  -- 1) genealogia cujo norm == canônico (a entrada oficial)
  select g2.nome into v_disp
  from public.genealogia g2
  where norm_nome(g2.nome) = v_canon
  order by (g2.cd_token is not null) desc, g2.nascimento nulls last
  limit 1;

  -- 2) senão, 1ª linha de um resultado cujo norm == canônico
  if v_disp is null then
    select split_part(r.cavalo_nome, E'\n', 1) into v_disp
    from public.resultados r
    where norm_nome(split_part(r.cavalo_nome, E'\n', 1)) = v_canon
    limit 1;
  end if;

  -- entrada de genealogia mais informativa do GRUPO (pedigree/nascimento/sexo)
  select g3.nome, g3.cd_token, g3.nascimento, g3.sexo, g3.pai, g3.mae into g
  from public.genealogia g3
  where norm_nome(g3.nome) = any (v_grp)
  order by (g3.cd_token is not null) desc, g3.nascimento nulls last
  limit 1;

  -- 3) último fallback p/ exibição: a própria entrada de genealogia do grupo,
  --    e por fim o nome pedido.
  if v_disp is null then v_disp := g.nome; end if;
  if v_disp is null then v_disp := split_part(p_nome, E'\n', 1); end if;

  return query select
    v_disp,
    v_canon,
    g.cd_token,
    g.nascimento,
    g.sexo,
    g.pai,
    g.mae,
    (g.nome is not null),
    exists (select 1 from public.cavalo_alias where canonico_norm = v_canon);
end; $$;
grant execute on function public.resolver_cavalo_full(text) to anon, authenticated;
