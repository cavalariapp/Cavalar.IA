-- 055 — banners de patrocínio: tempo de exposição (segundos) no carrossel.
-- `posicao` (já existe) = em qual página do app o banner aparece.
-- `nome` passa a ser só rótulo interno (admin); o banner em si é imagem pura.
alter table public.patrocinadores add column if not exists tempo_exposicao int not null default 6;
