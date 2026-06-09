-- 038 — DATA DE PUBLICAÇÃO do episódio (ordenar a aba "Todos" por mais recente)
-- Na aba "Todos" os episódios de todos os programas aparecem misturados, do mais
-- novo pro mais antigo. A data vem do Spotify (release_date) na importação.
alter table public.media add column if not exists data_pub date;
create index if not exists media_tipo_datapub_idx on public.media (tipo, data_pub desc);
