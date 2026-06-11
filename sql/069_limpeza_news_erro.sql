-- 069 — limpa notícias "Erro" / vazias já gravadas (de versões antigas do coletor
-- que não tinham a guarda reforçada). A partir de agora o scraper rejeita essas na
-- origem (news.py: título começando com "erro" OU corpo < 120 chars).
-- Rode no SQL Editor do Supabase.

delete from public.news
where lower(coalesce(title, '')) like 'erro%'
   or coalesce(btrim(title), '') = ''
   or length(coalesce(btrim(body), '')) < 120;
