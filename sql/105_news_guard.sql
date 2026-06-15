-- 105 — TRAVA no banco contra notícia "Erro" (independe do código do app).
-- Apesar do filtro em reescrever()/upsert_news(), surgiram rows com title "Erro"
-- (algum caminho de inserção escapa). Um gatilho BEFORE INSERT bloqueia de vez:
-- linha com título começando em "erro" simplesmente NÃO é inserida (sem erro).

-- 1) limpa as existentes
delete from public.news where lower(btrim(title)) like 'erro%';

-- 2) gatilho à prova de balas
create or replace function public._news_guard() returns trigger
language plpgsql as $$
begin
  if NEW.title is null or btrim(NEW.title) = '' or lower(btrim(NEW.title)) like 'erro%' then
    return null;   -- pula a inserção dessa linha, silenciosamente
  end if;
  return NEW;
end; $$;

drop trigger if exists trg_news_guard on public.news;
create trigger trg_news_guard before insert on public.news
  for each row execute function public._news_guard();
