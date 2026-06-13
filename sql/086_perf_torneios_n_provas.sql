-- 086 — PERFORMANCE da página de Resultados: contagem de provas PRÉ-COMPUTADA.
-- A página abria chamando torneios?select=...,provas(count) — uma AGREGAÇÃO por
-- torneio sobre TODA a tabela provas (inchada no backfill), e ainda bloqueava o
-- render até paginar tudo. Trocamos por uma coluna n_provas mantida por trigger:
-- a query vira um SELECT de coluna simples (instantâneo) e o flag _tem_provas
-- (esconde torneio fantasma/vazio) lê a coluna direto.

-- (1) coluna
alter table public.torneios add column if not exists n_provas int not null default 0;

-- (2) backfill do valor atual
update public.torneios t
   set n_provas = coalesce((select count(*) from public.provas p where p.torneio_id = t.id), 0);

-- (3) trigger que mantém n_provas (provas só são INSERIDAS no pipeline; DELETE
--     tratado por segurança). Row-level, custo desprezível (update por PK).
create or replace function public._tg_provas_count() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if tg_op = 'INSERT' then
    update public.torneios set n_provas = n_provas + 1 where id = new.torneio_id;
  elsif tg_op = 'DELETE' then
    update public.torneios set n_provas = greatest(n_provas - 1, 0) where id = old.torneio_id;
  end if;
  return null;
end; $$;

drop trigger if exists trg_provas_count on public.provas;
create trigger trg_provas_count
  after insert or delete on public.provas
  for each row execute function public._tg_provas_count();
