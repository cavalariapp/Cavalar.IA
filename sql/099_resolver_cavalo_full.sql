-- 099 — resolver_cavalo_full: resolução ÚNICA e completa do indivíduo CANÔNICO.
-- (v2: corrige "column reference canonico_norm is ambiguous" — a coluna de SAÍDA
--  canonico_norm colidia com cavalo_alias.canonico_norm no WHERE. Agora as referências
--  à tabela são QUALIFICADAS com alias, e o OUT param virou o_canonico.)
--
-- Para QUALQUER nome do grupo de um match devolve:
--   - display_nome: a grafia CANÔNICA p/ o cabeçalho;
--   - pedigree (cd_token, nascimento, sexo, pai, mae) do indivíduo;
--   - tem_gen: existe genealogia no grupo (→ mostra a aba/genética);
--   - tem_alias: o grupo tem apelido (→ o front desliga o filtro anti-homônimo).
drop function if exists public.resolver_cavalo_full(text);
create or replace function public.resolver_cavalo_full(p_nome text)
returns table (
  display_nome text,
  o_canonico   text,
  cd_token     text,
  nascimento   date,
  sexo         text,
  pai          text,
  mae          text,
  tem_gen      boolean,
  tem_alias    boolean
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
    select a.alias_norm from public.cavalo_alias a where a.canonico_norm = v_canon
  );

  -- grafia canônica p/ exibir: 1) genealogia cujo norm == canônico
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

  -- 3) último fallback p/ exibição
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
    exists (select 1 from public.cavalo_alias a where a.canonico_norm = v_canon);
end; $$;
grant execute on function public.resolver_cavalo_full(text) to anon, authenticated;
