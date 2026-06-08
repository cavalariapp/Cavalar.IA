-- 037 — COR e LOGO do programa (vitrine dos episódios)
-- A vitrine na página vira um "balão" da cor do programa (ex.: PodEquestre =
-- azul) com o logo encaixado (sem cortar). Admin define cor + logo; o app
-- preenche automaticamente a partir de outro episódio do mesmo programa.
alter table public.media add column if not exists cor text;     -- ex.: #1DB954, #1e66ff
alter table public.media add column if not exists imagem text;  -- URL do logo/capa
