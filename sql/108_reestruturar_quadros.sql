-- 108 — RE-ESTRUTURAR quadros de horário mal-processados (one-time).
--
-- Docs titulados "QUADRO ATUALIZADO"/grade de horário que NÃO viraram grade (sem .dias)
-- foram processados ANTES do scraper passar a LER O PDF VISUALMENTE pelo Claude. O texto
-- desses PDFs é uma TABELA que o pypdf destrói (às vezes 0 horários extraídos) → a IA caía
-- num "resumo de adendo". Zerando o conteudo_estruturado, o próximo `--estruturar` os refaz
-- pelo novo caminho (estruturar_pdf → grade `dias`), e o app os mostra com acordeões.
--
-- Alvo: tipo adendo/outros, título de quadro, SEM a chave 'dias'. NÃO toca os 'horarios'
-- (extraídos do programa, sem url_pdf próprio) nem os quadros que já estão OK.

-- 'outros' titulado como quadro → 'adendo' (p/ entrar na fila programa/horarios/adendo).
update public.torneio_documentos
set tipo = 'adendo'
where tipo = 'outros'
  and titulo ~* 'quadro|grade\s+de\s+hor'
  and not (coalesce(conteudo_estruturado, '{}'::jsonb) ? 'dias');

-- zera o estruturado dos quadros sem grade → reprocessa no próximo --estruturar.
update public.torneio_documentos
set conteudo_estruturado = null, estruturado_em = null
where tipo in ('adendo', 'outros')
  and titulo ~* 'quadro|grade\s+de\s+hor'
  and url_pdf is not null
  and not (coalesce(conteudo_estruturado, '{}'::jsonb) ? 'dias');
