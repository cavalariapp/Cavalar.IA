-- 067 — VERACIDADE 2: desambiguação de HOMÔNIMOS por NASCIMENTO
-- PROBLEMA (relato do cliente: "Olympia" x4 fundidas; "Arabella" de escola com 1,55m):
--   a mv_genetica casava cada animal da genealogia (cd_token) aos resultados SÓ pelo
--   NOME (norm_nome). Quando há vários cavalos de mesmo nome (homônimos), TODOS os
--   resultados de TODOS caíam em cada cd_token → a maior altura de um homônimo mais
--   alto contaminava os demais (matriz de escola "saltando" 1,55m). A trava de idade
--   (062) reduzia o estrago, mas não resolvia homônimos de MESMA faixa etária.
--
-- RAIZ corrigida: a PRÓPRIA linha do resultado carrega o NASCIMENTO do cavalo (2ª
--   linha de resultados.cavalo_nome = genealogia "DD/MM/AAAA | Sexo | Raça | ..."),
--   extraível em 100% das linhas novas. Agora um resultado só conta para um cd_token
--   se o nascimento que a linha carrega BATER com o do animal (data exata; ou mesmo
--   ANO quando um dos lados é placeholder 01/01). Sem nascimento em algum lado, cai
--   no casamento por nome (legado N8N sem genealogia) — sem regressão.
--
-- Reconstrói a mv_genetica mantendo a trava de idade (062). progenie (060) e
-- rankings (039) leem a mv → corrigem juntos. Rode e depois: select refresh_genetica();

drop materialized view if exists public.mv_genetica;
create materialized view public.mv_genetica as
with res as (
  select norm_nome(r.cavalo_nome) as filho_norm,
         extract(year from coalesce(p.data_prova, t.data_inicio))::int as ano_prova,
         public.altura_m(p.nome, p.descricao, p.categorias) as alt,
         -- nascimento que a linha do resultado carrega (2ª linha = genealogia)
         regexp_match(split_part(r.cavalo_nome, E'\n', 2), '(\d{2})/(\d{2})/(\d{4})') as nm
  from public.resultados r
  join public.provas p on p.id = r.prova_id
  left join public.torneios t on t.id = p.torneio_id
  where r.cavalo_nome is not null
),
res2 as (
  select filho_norm, ano_prova, alt,
         -- make_date robusto: só monta se mês 1-12 e dia 1-31 (um match estranho
         -- não pode derrubar o refresh inteiro); senão nasc_res = null (cai no nome)
         case when nm is not null
               and nm[2]::int between 1 and 12
               and nm[1]::int between 1 and 31
              then make_date(nm[3]::int, nm[2]::int, nm[1]::int) end as nasc_res
  from res
)
select g.cd_token,
       norm_nome(g.pai) as pai_norm,
       norm_nome(g.mae) as mae_norm,
       extract(year from g.nascimento)::int as nasc_ano,
       res2.ano_prova,
       max(res2.alt) as max_alt
from public.genealogia g
join res2 on res2.filho_norm = norm_nome(g.nome)
  -- DESAMBIGUAÇÃO POR NASCIMENTO (anti-homônimo)
  and (
    res2.nasc_res is null or g.nascimento is null            -- algum lado sem data → nome
    or res2.nasc_res = g.nascimento                          -- data exata bate
    or (extract(year from res2.nasc_res) = extract(year from g.nascimento)
        and ( (extract(month from res2.nasc_res) = 1 and extract(day from res2.nasc_res) = 1)
           or (extract(month from g.nascimento)  = 1 and extract(day from g.nascimento)  = 1) ))
  )
where res2.alt is not null
  -- TRAVA DE IDADE (062): só conta se a altura é plausível p/ a idade na prova
  and (
    g.nascimento is null
    or res2.ano_prova is null
    or res2.alt <= public.alt_max_para_idade(res2.ano_prova - extract(year from g.nascimento)::int)
  )
group by g.cd_token, norm_nome(g.pai), norm_nome(g.mae),
         extract(year from g.nascimento)::int, res2.ano_prova;

create index if not exists mv_genetica_pai_idx on public.mv_genetica (pai_norm);
create index if not exists mv_genetica_mae_idx on public.mv_genetica (mae_norm);
create index if not exists mv_genetica_cd_idx  on public.mv_genetica (cd_token);

-- ── DIAGNÓSTICO (admin): inspeciona os homônimos de um nome ───────────
-- Mostra, por NASCIMENTO carregado nos resultados, quantas linhas e a maior altura.
-- Serve p/ CONFIRMAR casos (Arabella/Olympia): se aparecerem vários nascimentos com
-- alturas bem diferentes, era homônimo (agora separado). Admin-only (lê resultados).
create or replace function public.diag_homonimos(p_nome text)
returns table (nasc_res date, n_linhas bigint, alt_max numeric, exemplos text)
language plpgsql stable security definer set search_path = public as $$
begin
  if not public.is_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  return query
  with res as (
    select regexp_match(split_part(r.cavalo_nome, E'\n', 2), '(\d{2})/(\d{2})/(\d{4})') as nm,
           public.altura_m(p.nome, p.descricao, p.categorias) as alt,
           t.nome as torneio
    from public.resultados r
    join public.provas p on p.id = r.prova_id
    left join public.torneios t on t.id = p.torneio_id
    where norm_nome(split_part(r.cavalo_nome, E'\n', 1)) = norm_nome(split_part(p_nome, E'\n', 1))
  )
  select case when nm is not null
                and nm[2]::int between 1 and 12 and nm[1]::int between 1 and 31
              then make_date(nm[3]::int, nm[2]::int, nm[1]::int) end as nasc_res,
         count(*) as n_linhas,
         max(alt) as alt_max,
         string_agg(distinct torneio, ' | ' order by torneio) filter (where alt >= 1.30) as exemplos
  from res
  group by 1
  order by alt_max desc nulls last;
end;
$$;
revoke all on function public.diag_homonimos(text) from public, anon;
grant execute on function public.diag_homonimos(text) to authenticated;
