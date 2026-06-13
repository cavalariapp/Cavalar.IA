-- 094 — ALTURA EXTERNA (Fase 1): crédito de performance de cavalos BH EXPORTADOS.
-- Cavalos Brasileiro de Hipismo que foram competir no exterior não têm resultado
-- brasileiro → suas matrizes não eram creditadas. Aqui guardamos a "melhor altura"
-- (de carreira) desses animais — preenchida na MÃO (admin) ou pelo FEI (Fase 2) — e
-- integramos ao ranking genético: a égua/garanhão ganha crédito por esse filho.

-- (1) tabela: 1 linha por animal (cd_token ABCCH), com a melhor altura de carreira.
create table if not exists public.altura_externa (
  cd_token       text primary key,           -- token ABCCH do animal (genealogia.cd_token)
  nome           text,                        -- nome (referência/exibição)
  melhor_altura  numeric not null,            -- em METROS (ex.: 1.60)
  fonte          text not null default 'manual',  -- 'manual' | 'fei'
  origem_url     text,
  atualizado_em  timestamptz not null default now()
);
alter table public.altura_externa enable row level security;  -- sem policy → só RPC/service_role

-- (2) RPCs ADMIN (gateadas por is_admin)
-- busca na GENEALOGIA (inclui exportados sem resultado BR), com pai/mãe p/ desambiguar.
create or replace function public.admin_buscar_genealogia(termo text)
returns table (cd_token text, nome text, nascimento date, sexo text, pai text, mae text, tem_altura boolean)
language plpgsql stable security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  return query
    select g.cd_token, g.nome, g.nascimento, g.sexo, g.pai, g.mae,
           exists (select 1 from public.altura_externa a where a.cd_token = g.cd_token)
    from public.genealogia g
    where g.cd_token is not null and g.nome ilike '%' || termo || '%'
    order by g.nome limit 40;
end; $$;

create or replace function public.admin_set_altura_externa(
  p_cd_token text, p_nome text, p_altura numeric, p_fonte text default 'manual', p_url text default null
) returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  if p_cd_token is null or p_altura is null then
    raise exception 'cd_token e altura são obrigatórios';
  end if;
  insert into public.altura_externa (cd_token, nome, melhor_altura, fonte, origem_url, atualizado_em)
  values (p_cd_token, p_nome, p_altura, coalesce(p_fonte,'manual'), p_url, now())
  on conflict (cd_token) do update
    set nome = excluded.nome, melhor_altura = excluded.melhor_altura,
        fonte = excluded.fonte, origem_url = excluded.origem_url, atualizado_em = now();
end; $$;

create or replace function public.admin_listar_altura_externa()
returns table (cd_token text, nome text, melhor_altura numeric, fonte text, atualizado_em timestamptz)
language plpgsql stable security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  return query select a.cd_token, a.nome, a.melhor_altura, a.fonte, a.atualizado_em
               from public.altura_externa a order by a.atualizado_em desc;
end; $$;

create or replace function public.admin_remover_altura_externa(p_cd_token text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  delete from public.altura_externa where cd_token = p_cd_token;
end; $$;

revoke all on function public.admin_buscar_genealogia(text)                         from public, anon;
revoke all on function public.admin_set_altura_externa(text,text,numeric,text,text)  from public, anon;
revoke all on function public.admin_listar_altura_externa()                          from public, anon;
revoke all on function public.admin_remover_altura_externa(text)                     from public, anon;
grant execute on function public.admin_buscar_genealogia(text)                         to authenticated;
grant execute on function public.admin_set_altura_externa(text,text,numeric,text,text) to authenticated;
grant execute on function public.admin_listar_altura_externa()                         to authenticated;
grant execute on function public.admin_remover_altura_externa(text)                    to authenticated;

-- (3) INTEGRAÇÃO no ranking: o filho exportado passa a contar em comp/m140 com a
--     altura externa. Só entra no agregado quando ano = 'Todos' (a altura externa é
--     de carreira, sem ano específico → não infla rankings de um ano passado).
drop function if exists public.rankings_geneticos(text, int);
create or replace function public.rankings_geneticos(papel text, ano int default null)
returns table (
  reprodutor text, rep_token text, total_filhos bigint, f4 bigint, comp bigint,
  pct_comp numeric, f8 bigint, m140 bigint, pct140 numeric
)
language plpgsql stable security definer set search_path = public set statement_timeout = '25s' as $$
declare v_ref int := coalesce(ano, extract(year from current_date)::int);
begin
  if not public.is_premium() then raise exception 'premium_required' using errcode = '42501'; end if;
  return query
  with ger as (
    select cd_token,
           (case when papel='mae' then mae else pai end) as rep_disp,
           coalesce(nullif(case when papel='mae' then mae_token else pai_token end,''),
                    norm_nome(case when papel='mae' then mae else pai end)) as rep_key,
           nullif(case when papel='mae' then mae_token else pai_token end,'') as rep_tok,
           (v_ref - extract(year from nascimento)::int) as idade
    from genealogia
    where not _rep_placeholder(norm_nome(case when papel='mae' then mae else pai end))
      and (nascimento is null or extract(year from nascimento)::int <= v_ref)
  ),
  tot as (
    select rep_key, max(rep_disp) as nome, max(rep_tok) as rep_token,
           count(distinct cd_token) as total,
           count(distinct cd_token) filter (where idade between 5 and 30) as f4,
           count(distinct cd_token) filter (where idade between 9 and 30) as f8
    from ger group by rep_key
  ),
  ev as (
    select rep_key, cd_token, idade, max(max_alt) as max_alt from (
      -- resultados brasileiros (mv_genetica)
      select coalesce(nullif(case when papel='mae' then mae_token else pai_token end,''),
                      case when papel='mae' then mae_norm else pai_norm end) as rep_key,
             cd_token, (v_ref - nasc_ano) as idade, max_alt
      from mv_genetica
      where (rankings_geneticos.ano is null or ano_prova = rankings_geneticos.ano)
      union all
      -- altura externa (filhos exportados) — só no agregado "Todos" (sem ano)
      select coalesce(nullif(case when papel='mae' then g.mae_token else g.pai_token end,''),
                      norm_nome(case when papel='mae' then g.mae else g.pai end)) as rep_key,
             g.cd_token, (v_ref - extract(year from g.nascimento)::int) as idade,
             ae.melhor_altura as max_alt
      from public.altura_externa ae
      join public.genealogia g on g.cd_token = ae.cd_token
      where rankings_geneticos.ano is null
    ) u
    group by 1,2,3
  ),
  agg as (
    select rep_key,
           count(distinct cd_token) filter (where idade between 1 and 30) as comp,
           count(distinct cd_token) filter (where idade between 5 and 30) as comp4,
           count(distinct cd_token) filter (where idade between 1 and 30 and max_alt>=1.40) as m140,
           count(distinct cd_token) filter (where idade between 9 and 30 and max_alt>=1.40) as alto8
    from ev group by rep_key
  )
  select t.nome, t.rep_token, t.total, t.f4, coalesce(a.comp,0),
         round(100.0*coalesce(a.comp4,0)/nullif(t.f4,0),1),
         t.f8, coalesce(a.m140,0), round(100.0*coalesce(a.alto8,0)/nullif(t.f8,0),1)
  from tot t left join agg a on a.rep_key=t.rep_key
  where t.total>=2 or coalesce(a.comp,0)>0
  order by t.total desc;
end; $$;
grant execute on function public.rankings_geneticos(text, int) to anon, authenticated;

-- (4) progênie (perfil do reprodutor) também mostra a altura externa do filho.
drop function if exists public.progenie(text, text);
drop function if exists public.progenie(text, text, text);
create or replace function public.progenie(papel text, rep text, rep_token text default null)
returns table (nome text, sexo text, nascimento date, max_alt numeric, competiu boolean)
language plpgsql stable security definer set search_path = public as $$
declare use_tok boolean := (rep_token is not null and rep_token <> '');
begin
  if public.is_premium() then
    return query
      with alt as (
        select cd, max(malt) as malt from (
          select mv.cd_token as cd, mv.max_alt as malt from mv_genetica mv
          union all
          select ae.cd_token, ae.melhor_altura from public.altura_externa ae
        ) x group by cd
      )
      select g.nome, g.sexo, g.nascimento, a.malt, (a.cd is not null)
      from genealogia g left join alt a on a.cd = g.cd_token
      where (use_tok and (case when papel='mae' then g.mae_token else g.pai_token end)=rep_token)
         or (not use_tok and norm_nome(case when papel='mae' then g.mae else g.pai end)=norm_nome(rep))
      order by a.malt desc nulls last, g.nome;
  else
    return query
      select g.nome, g.sexo, g.nascimento, null::numeric, null::boolean
      from genealogia g
      where (use_tok and (case when papel='mae' then g.mae_token else g.pai_token end)=rep_token)
         or (not use_tok and norm_nome(case when papel='mae' then g.mae else g.pai end)=norm_nome(rep))
      order by g.nome;
  end if;
end; $$;
grant execute on function public.progenie(text, text, text) to anon, authenticated;
