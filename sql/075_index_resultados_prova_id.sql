-- 075 — CRÍTICO: índice em resultados(prova_id).
-- O upsert de resultados faz DELETE ... WHERE prova_id = X antes de reinserir. Sem
-- índice em prova_id, esse DELETE é uma VARREDURA COMPLETA da tabela. Era rápido
-- com a tabela pequena (blocos 1 e 2 do backfill passaram), mas com a `resultados`
-- inflada pelo backfill virou seq scan lento → estourou o timeout → 500 no bloco 3.
-- (FK não cria índice automático no Postgres.) Com o índice, o DELETE por prova
-- fica instantâneo — conserta o backfill, o frescor, o recurar e os --proximos.
--
-- Rode no SQL Editor (o backfill está parado, então pode ser sem CONCURRENTLY —
-- roda numa tacada; trava brevemente as gravações enquanto constrói).

create index if not exists idx_res_prova_id on public.resultados (prova_id);
