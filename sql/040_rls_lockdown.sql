-- 040 — TRAVA DE SEGURANÇA (RLS) PARA O LANÇAMENTO
-- Auditoria empírica (com a anon key, que é PÚBLICA no front) encontrou:
--   A) resultados/provas/torneios/torneio_documentos/eventos_cbh aceitavam
--      ESCRITA anônima (qualquer um podia forjar/alterar resultados).
--   B) profiles vazava EMAIL e CELULAR (PII) para qualquer visitante anônimo.
-- O scraper escreve via service_role (ignora RLS), então nada disso o afeta.

-- ───────────────────────────────────────────────────────────────────────
-- A) CONTEÚDO PÚBLICO: leitura liberada, escrita só admin (scraper = service_role)
-- ───────────────────────────────────────────────────────────────────────
do $$
declare
  t text;
  pub_tables text[] := array[
    'resultados','provas','torneios','torneio_documentos','eventos_cbh'
  ];
begin
  foreach t in array pub_tables loop
    if to_regclass('public.'||t) is null then
      continue;
    end if;
    execute format('alter table public.%I enable row level security', t);

    -- limpa QUALQUER policy pré-existente (inclusive permissivas de escrita)
    execute (
      select coalesce(string_agg(
        format('drop policy if exists %I on public.%I;', policyname, t), ' '), '')
      from pg_policies where schemaname='public' and tablename=t
    );

    -- leitura pública
    execute format(
      'create policy %I on public.%I for select using (true)', t||'_public_read', t);
    -- escrita só admin (além do service_role, que ignora RLS)
    execute format(
      'create policy %I on public.%I for insert with check (public.is_admin())', t||'_admin_ins', t);
    execute format(
      'create policy %I on public.%I for update using (public.is_admin()) with check (public.is_admin())', t||'_admin_upd', t);
    execute format(
      'create policy %I on public.%I for delete using (public.is_admin())', t||'_admin_del', t);
  end loop;
end$$;

-- ───────────────────────────────────────────────────────────────────────
-- B) PROFILES: protege PII por privilégio de COLUNA (sem mexer na RLS de
--    linha, que outras policies usam em subconsultas EXISTS).
--    O visitante anônimo deixa de poder ler email/celular/idade.
--    Outros usuários continuam sendo lidos pela VIEW profiles_publicos
--    (que não expõe esses campos). O dono lê o próprio perfil como
--    'authenticated' (mantém o acesso).
-- ───────────────────────────────────────────────────────────────────────
revoke select (email, celular, idade) on public.profiles from anon;
-- (não tocamos em 'authenticated': o dono precisa ler/editar o próprio
--  email/celular/idade. Endurecer o cross-read entre membros logados exige
--  RPC SECURITY DEFINER + refactor do front — fica como passo seguinte.)

-- ───────────────────────────────────────────────────────────────────────
-- C) LIMPEZA: linha-lixo (id 589261) criada por engano durante a auditoria
--    (todos os campos nulos, prova_id nulo). Inofensiva na UI, mas removida.
-- ───────────────────────────────────────────────────────────────────────
delete from public.resultados where id = 589261 and prova_id is null;
