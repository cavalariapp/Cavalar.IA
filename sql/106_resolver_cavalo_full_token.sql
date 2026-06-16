-- 106 — PADRONIZAÇÃO da ficha do animal: resolver_cavalo_full agora aceita TOKEN.
--
-- PROBLEMA: a ficha do cavalo era aberta por 3 caminhos (busca, resultados/zeros e
-- ranking genético). Só os dois primeiros chamavam resolver_cavalo_full(nome) p/ achar o
-- indivíduo CANÔNICO. O ranking genético abria por TOKEN e PULAVA a resolução → cabeçalho
-- com nome divergente + filtro anti-homônimo zerando a performance ("ora mostra, ora não").
--
-- SOLUÇÃO: um único resolvedor que serve aos TRÊS caminhos. Aceita um token OPCIONAL:
--   - COM token (ranking genético / homônimos): o indivíduo é EXATAMENTE aquele token
--     (separa as 4 "Olympia"); mesmo assim devolve a grafia CANÔNICA do grupo + tem_alias.
--   - SEM token (busca / resultados): pega a entrada mais informativa do grupo do match.
-- Em ambos devolve display_nome (canônico), pedigree, tem_gen e tem_alias.
--
-- (usa VARIÁVEIS ESCALARES em vez de um RECORD: um record não-atribuído não pode ter os
--  campos lidos quando p_token é null — era o erro "record g is not assigned yet".)

drop function if exists public.resolver_cavalo_full(text);
drop function if exists public.resolver_cavalo_full(text, text);
create or replace function public.resolver_cavalo_full(p_nome text, p_token text default null)
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
  v_anchor  text;     -- nome âncora p/ achar o grupo canônico
  v_canon   text;
  v_grp     text[];
  v_disp    text;     -- grafia canônica p/ exibir
  v_pednome text;     -- nome da entrada de genealogia (pedigree) → tem_gen
  v_token   text;
  v_nasc    date;
  v_sexo    text;
  v_pai     text;
  v_mae     text;
begin
  -- 1) ÂNCORA: se veio token, o indivíduo é EXATAMENTE aquele (desambigua homônimos).
  v_anchor := split_part(p_nome, E'\n', 1);
  if p_token is not null and btrim(p_token) <> '' then
    select g2.nome, g2.cd_token, g2.nascimento, g2.sexo, g2.pai, g2.mae
      into v_pednome, v_token, v_nasc, v_sexo, v_pai, v_mae
    from public.genealogia g2
    where g2.cd_token = p_token
    limit 1;
    if v_pednome is not null then v_anchor := v_pednome; end if;
  end if;
  v_canon := public.canon_cavalo(norm_nome(v_anchor));

  -- grupo do match = canônico ∪ apelidos do canônico
  v_grp := array(
    select v_canon
    union
    select a.alias_norm from public.cavalo_alias a where a.canonico_norm = v_canon
  );

  -- grafia canônica p/ o cabeçalho: 1) genealogia cujo norm == canônico
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

  -- pedigree: se NÃO ancoramos por token (ou o token não tinha linha na genealogia),
  -- pega a entrada mais informativa do GRUPO (pedigree/nascimento/sexo).
  if v_pednome is null then
    select g3.nome, g3.cd_token, g3.nascimento, g3.sexo, g3.pai, g3.mae
      into v_pednome, v_token, v_nasc, v_sexo, v_pai, v_mae
    from public.genealogia g3
    where norm_nome(g3.nome) = any (v_grp)
    order by (g3.cd_token is not null) desc, g3.nascimento nulls last
    limit 1;
  end if;

  -- fallback final p/ exibição
  if v_disp is null then v_disp := v_pednome; end if;
  if v_disp is null then v_disp := v_anchor; end if;

  return query select
    v_disp,
    v_canon,
    v_token,
    v_nasc,
    v_sexo,
    v_pai,
    v_mae,
    (v_pednome is not null),
    exists (select 1 from public.cavalo_alias a where a.canonico_norm = v_canon);
end; $$;
grant execute on function public.resolver_cavalo_full(text, text) to anon, authenticated;
