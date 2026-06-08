-- 036 — PROGRAMA/CANAL da mídia (sub-abas dentro de Podcast/Videocast/Videoaula)
-- Permite organizar episódios por programa (ex.: PodEquestre, Clac Cast, Big
-- Talk for Breeders). O admin "cria" uma aba simplesmente digitando o nome do
-- programa ao adicionar/editar um item.
alter table public.media add column if not exists programa text;
create index if not exists media_tipo_programa_idx on public.media (tipo, programa, ordem);
