-- 071 — reprocessar QUADROS DE HORÁRIO que foram estruturados como ADENDO.
-- Um quadro de horários atualizado às vezes é publicado na seção de adendos da
-- federação → entrava como tipo='adendo' e era estruturado com o schema textual de
-- adendo (sem os dias/horários). Agora o scraper detecta isso e estrutura com o
-- schema 'horarios' (dias) — e o app mostra com os acordeões como "Quadro de
-- Horários Atualizado". Esta migração LIMPA o conteudo_estruturado desses adendos
-- (título com "quadro"/"horário") pra que o próximo --estruturar (cron diário) os
-- refaça corretamente. Idempotente.

update public.torneio_documentos
set conteudo_estruturado = null,
    estruturado_em = null
where tipo = 'adendo'
  and (titulo ilike '%quadro%' or titulo ilike '%horár%' or titulo ilike '%horar%');
