-- 058 — reprocessar QUADROS DE HORÁRIOS com o prompt novo (agora extrai altura,
-- categoria, característica/tabela, pista e nome da prova). Zera o estruturado
-- p/ a fila do --estruturar pegar de novo. Rode 1x; depois rode o workflow
-- --estruturar (lote de 15; re-rode até zerar a fila, ou suba CAVALARIA_DOCS).
update public.torneio_documentos
set conteudo_estruturado = null, estruturado_em = null
where tipo = 'horarios';
