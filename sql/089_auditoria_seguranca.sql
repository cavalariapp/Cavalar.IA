-- 089 — AUDITORIA DE SEGURANÇA (somente LEITURA; não altera nada).
-- Rode CADA bloco no SQL Editor e me cole o resultado. É a verificação
-- autoritativa: com a anon key (pública no front), qualquer um pode bater na API
-- REST direto, então TODA tabela precisa de RLS. Aqui vemos o estado real do banco.

-- ════════════════════════════════════════════════════════════════════════════
-- BLOCO 1 — TABELAS: RLS ligado? quantas policies? o que 'anon' (visitante NÃO
-- logado) consegue fazer? Linhas com 🔴 são vazamento/abuso em potencial.
-- ════════════════════════════════════════════════════════════════════════════
select
  c.relname                                              as tabela,
  c.relrowsecurity                                       as rls_ligado,
  (select count(*) from pg_policy p where p.polrelid=c.oid) as policies,
  has_table_privilege('anon', c.oid, 'SELECT')          as anon_le,
  has_table_privilege('anon', c.oid, 'INSERT')          as anon_insere,
  has_table_privilege('anon', c.oid, 'UPDATE')          as anon_edita,
  has_table_privilege('anon', c.oid, 'DELETE')          as anon_apaga,
  has_table_privilege('authenticated', c.oid, 'SELECT') as logado_le,
  case
    when not c.relrowsecurity and has_table_privilege('anon', c.oid,'SELECT')
      then '🔴 LÊ SEM RLS (qualquer um lê tudo)'
    when not c.relrowsecurity and (has_table_privilege('anon',c.oid,'INSERT')
      or has_table_privilege('anon',c.oid,'UPDATE') or has_table_privilege('anon',c.oid,'DELETE'))
      then '🔴 ESCREVE SEM RLS (qualquer um altera)'
    when not c.relrowsecurity
      then '🟡 sem RLS (mas anon sem privilégio)'
    else '🟢 RLS ligado'
  end                                                    as veredito
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'r'
order by c.relrowsecurity asc, anon_le desc, c.relname;

-- ════════════════════════════════════════════════════════════════════════════
-- BLOCO 2 — VIEWS: uma view SEM security_invoker roda como o DONO (postgres) e
-- IGNORA a RLS das tabelas que ela lê → pode vazar. Toda view que toca dado
-- sensível deve ter security_invoker = on (ou expor só colunas públicas).
-- ════════════════════════════════════════════════════════════════════════════
select
  c.relname as view,
  coalesce(
    (c.reloptions::text ilike '%security_invoker=on%') or
    (c.reloptions::text ilike '%security_invoker=true%'), false) as security_invoker,
  case when coalesce(
        (c.reloptions::text ilike '%security_invoker=on%') or
        (c.reloptions::text ilike '%security_invoker=true%'), false)
       then '🟢 respeita RLS'
       else '🟡 roda como dono — confira se NÃO expõe PII/colunas sensíveis' end as veredito
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'v'
order by security_invoker asc, c.relname;

-- ════════════════════════════════════════════════════════════════════════════
-- BLOCO 3 — FUNÇÕES SECURITY DEFINER expostas a anon/authenticated. São
-- propositais (contornam RLS), mas cada uma DEVE ter trava interna
-- (is_admin()/is_premium()/auth.uid()). Confira se nenhuma sensível está solta.
-- ════════════════════════════════════════════════════════════════════════════
select
  p.proname                                            as funcao,
  pg_get_function_identity_arguments(p.oid)            as args,
  has_function_privilege('anon', p.oid, 'EXECUTE')     as anon_pode,
  has_function_privilege('authenticated', p.oid, 'EXECUTE') as logado_pode
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.prosecdef
order by anon_pode desc, p.proname;

-- ════════════════════════════════════════════════════════════════════════════
-- BLOCO 4 — Confirma que a PII de profiles NÃO está exposta. O esperado é
-- 'false' para email/celular/idade/is_admin em anon E authenticated.
-- ════════════════════════════════════════════════════════════════════════════
select 'anon' as papel, col,
       has_column_privilege('anon', 'public.profiles', col, 'SELECT') as pode_ler
from (values ('email'),('celular'),('idade'),('is_admin')) v(col)
union all
select 'authenticated', col,
       has_column_privilege('authenticated', 'public.profiles', col, 'SELECT')
from (values ('email'),('celular'),('idade'),('is_admin')) v(col)
order by papel, col;
