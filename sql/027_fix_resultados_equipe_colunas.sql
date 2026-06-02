-- ═══════════════════════════════════════════════════════════════════
-- Migração 027 — CONSERTAR resultados de PROVAS POR EQUIPE (colunas trocadas)
--
--  O PROBLEMA (medido no banco, ~5.400 linhas):
--    Em provas POR EQUIPE, a tabela da fonte tem uma coluna "Equipe". O
--    scraper antigo (nossas tentativas anteriores no N8N/Claude) gravou
--    essa Equipe na coluna ERRADA. O resultado ficou assim:
--        tempo      = "0"        ← na verdade são as FALTAS
--        penalidade = "PMDF"     ← na verdade é a EQUIPE (clube/federação)
--        pontos     = "58,92"    ← na verdade é o TEMPO de pista
--    O certo é:  tempo="58,92", penalidade="0", e a equipe guardada à parte.
--    Esse embaralho quebra a estatística de "percurso zerado" (que procura
--    penalidade começando em "0" e encontra "PMDF") e mostra dado errado.
--
--  POR QUE CORRIGIR NO BANCO (e não re-raspar):
--    O site da FPH RECICLA os números de ID — ?ID=10608 hoje aponta para
--    uma prova DIFERENTE da que guardamos. Re-raspar pelo ID antigo
--    sobrescreveria o resultado de uma prova com o de OUTRA. Já aqui os
--    valores certos JÁ ESTÃO na linha, só em colunas trocadas → dá pra
--    consertar com uma troca de colunas, sem depender da internet.
--
--  ZERO PERDA DE INFORMAÇÃO:
--    Criamos a coluna `equipe` e movemos o nome da equipe pra lá (em vez de
--    jogar fora). O app pode passar a exibir a equipe no futuro.
--
--  SEGURANÇA:
--    PASSO 1 só LÊ (mostra o que vai mudar). PASSO 2 faz BACKUP das linhas
--    afetadas (dá pra desfazer 100%). Só o PASSO 4 escreve. Rode PASSO A
--    PASSO, conferindo a saída de cada um. Idempotente (pode repetir).
--
--  RLS: `equipe` é só mais uma coluna de `resultados` (leitura já é pública;
--    escrita continua só via service_role). Não mexe em permissão.
-- ═══════════════════════════════════════════════════════════════════


-- ───────────────────────────────────────────────────────────────────
-- PASSO 1 — DIAGNÓSTICO (só leitura; NÃO muda nada). Rode e confira os números.
--   Esperado (medido em 01/jun/2026): A≈4.989, B≈374, BORDA≈58.
--   (A inclui faltas com o detalhe "(4+0)" no `tempo`, ex.: "4\n(4+0)".)
-- ───────────────────────────────────────────────────────────────────

-- (A) Linhas RECUPERÁVEIS: faltas em `tempo`, equipe em `penalidade`, tempo em `pontos`.
SELECT 'A_recuperavel' AS grupo, count(*) AS linhas
FROM resultados
WHERE penalidade IS NOT NULL
  AND penalidade !~ '[0-9]'                         -- sem dígito → não é faltas ("0","4","0\n(0+0)")
  AND btrim(penalidade) <> ''
  AND btrim(penalidade) !~ '^-+$'                   -- não é "-" / "----"
  AND lower(btrim(penalidade)) NOT IN
      ('forfait','forf.','ff','eliminado','eliminada','elim.','elim',
       'desistente','desqualificado','desqualificada','des.','des',
       'abandono','desclassificado','retirado','ausente','nc','np','wd','rt','ab')
  AND tempo  ~ '^[0-9]{1,3}[[:space:]]*(\([0-9]+\+[0-9]+\))?$'                        -- `tempo` é um nº de faltas
  AND pontos ~ '^[0-9]{2,3},[0-9]{2}$'              -- `pontos` é um TEMPO de pista (>= 10s)
  AND tempo_2 IS NULL AND penalidade_2 IS NULL;      -- só fase única (two-phase fica de fora)

-- (B) Linhas com STATUS na coluna errada: status em `tempo`, equipe em `penalidade`.
SELECT 'B_status_em_tempo' AS grupo, count(*) AS linhas
FROM resultados
WHERE penalidade IS NOT NULL
  AND penalidade !~ '[0-9]' AND btrim(penalidade) <> '' AND btrim(penalidade) !~ '^-+$'
  AND lower(btrim(penalidade)) NOT IN
      ('forfait','forf.','ff','eliminado','eliminada','elim.','elim',
       'desistente','desqualificado','desqualificada','des.','des',
       'abandono','desclassificado','retirado','ausente','nc','np','wd','rt','ab')
  AND lower(btrim(tempo)) IN
      ('eliminado','eliminada','desistente','forfait','desqualificado',
       'desclassificado','abandono','retirado','ausente')
  AND (pontos IS NULL OR btrim(pontos) = '')
  AND tempo_2 IS NULL AND penalidade_2 IS NULL;

-- (BORDA) Tudo que tem equipe na penalidade mas NÃO cai em A nem B (revisão manual).
--   Inspecione depois com calma; são poucas e inconsistentes (pontos "-----",
--   provas de duas fases, etc.). NÃO são tocadas pelos UPDATEs abaixo.
SELECT 'BORDA_revisar' AS grupo, count(*) AS linhas
FROM resultados
WHERE penalidade IS NOT NULL
  AND penalidade !~ '[0-9]' AND btrim(penalidade) <> '' AND btrim(penalidade) !~ '^-+$'
  AND lower(btrim(penalidade)) NOT IN
      ('forfait','forf.','ff','eliminado','eliminada','elim.','elim',
       'desistente','desqualificado','desqualificada','des.','des',
       'abandono','desclassificado','retirado','ausente','nc','np','wd','rt','ab')
  AND NOT (   -- não é A
        tempo ~ '^[0-9]{1,3}[[:space:]]*(\([0-9]+\+[0-9]+\))?$' AND pontos ~ '^[0-9]{2,3},[0-9]{2}$'
        AND tempo_2 IS NULL AND penalidade_2 IS NULL)
  AND NOT (   -- não é B
        lower(btrim(tempo)) IN ('eliminado','eliminada','desistente','forfait',
            'desqualificado','desclassificado','abandono','retirado','ausente')
        AND (pontos IS NULL OR btrim(pontos) = '')
        AND tempo_2 IS NULL AND penalidade_2 IS NULL);


-- ───────────────────────────────────────────────────────────────────
-- PASSO 2 — BACKUP das linhas afetadas (permite DESFAZER 100%).
--   Cria `resultados_bkp_027` com a CÓPIA original. Idempotente: se já
--   existir, mantém o backup ORIGINAL (não sobrescreve).
-- ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS resultados_bkp_027 AS
SELECT * FROM resultados
WHERE penalidade IS NOT NULL
  AND penalidade !~ '[0-9]' AND btrim(penalidade) <> '' AND btrim(penalidade) !~ '^-+$'
  AND lower(btrim(penalidade)) NOT IN
      ('forfait','forf.','ff','eliminado','eliminada','elim.','elim',
       'desistente','desqualificado','desqualificada','des.','des',
       'abandono','desclassificado','retirado','ausente','nc','np','wd','rt','ab');

SELECT 'backup' AS info, count(*) AS linhas_no_backup FROM resultados_bkp_027;


-- ───────────────────────────────────────────────────────────────────
-- PASSO 3 — Coluna `equipe` (guarda o nome da equipe; idempotente).
-- ───────────────────────────────────────────────────────────────────
ALTER TABLE resultados ADD COLUMN IF NOT EXISTS equipe TEXT;


-- ───────────────────────────────────────────────────────────────────
-- PASSO 4 — CORREÇÃO (ESCREVE). Confira PASSO 1 antes de rodar.
--   No SQL, todos os valores à direita do "=" usam o conteúdo ANTIGO da
--   linha — então `tempo = pontos, pontos = NULL` é uma troca de verdade.
-- ───────────────────────────────────────────────────────────────────

-- (A) troca tripla: equipe←penalidade, penalidade←faltas(tempo), tempo←tempo-de-pista(pontos), pontos←NULL
UPDATE resultados
SET equipe     = penalidade,
    penalidade = tempo,
    tempo      = pontos,
    pontos     = NULL
WHERE penalidade IS NOT NULL
  AND penalidade !~ '[0-9]' AND btrim(penalidade) <> '' AND btrim(penalidade) !~ '^-+$'
  AND lower(btrim(penalidade)) NOT IN
      ('forfait','forf.','ff','eliminado','eliminada','elim.','elim',
       'desistente','desqualificado','desqualificada','des.','des',
       'abandono','desclassificado','retirado','ausente','nc','np','wd','rt','ab')
  AND tempo  ~ '^[0-9]{1,3}[[:space:]]*(\([0-9]+\+[0-9]+\))?$'
  AND pontos ~ '^[0-9]{2,3},[0-9]{2}$'
  AND tempo_2 IS NULL AND penalidade_2 IS NULL;

-- (B) status estava em `tempo`: equipe←penalidade, penalidade←status(tempo), tempo←NULL
UPDATE resultados
SET equipe     = penalidade,
    penalidade = tempo,
    tempo      = NULL
WHERE penalidade IS NOT NULL
  AND penalidade !~ '[0-9]' AND btrim(penalidade) <> '' AND btrim(penalidade) !~ '^-+$'
  AND lower(btrim(penalidade)) NOT IN
      ('forfait','forf.','ff','eliminado','eliminada','elim.','elim',
       'desistente','desqualificado','desqualificada','des.','des',
       'abandono','desclassificado','retirado','ausente','nc','np','wd','rt','ab')
  AND lower(btrim(tempo)) IN
      ('eliminado','eliminada','desistente','forfait','desqualificado',
       'desclassificado','abandono','retirado','ausente')
  AND (pontos IS NULL OR btrim(pontos) = '')
  AND tempo_2 IS NULL AND penalidade_2 IS NULL;


-- ───────────────────────────────────────────────────────────────────
-- PASSO 5 — CONFERÊNCIA (só leitura). O grupo A deve cair pra 0
--   (mesmos critérios EXATOS do PASSO 4-A; se sobrar algo, não foi corrigido).
-- ───────────────────────────────────────────────────────────────────
SELECT 'restante_A_apos_fix' AS grupo, count(*) AS linhas
FROM resultados
WHERE penalidade !~ '[0-9]' AND btrim(penalidade) <> '' AND btrim(penalidade) !~ '^-+$'
  AND lower(btrim(penalidade)) NOT IN
      ('forfait','forf.','ff','eliminado','eliminada','elim.','elim',
       'desistente','desqualificado','desqualificada','des.','des',
       'abandono','desclassificado','retirado','ausente','nc','np','wd','rt','ab')
  AND tempo ~ '^[0-9]{1,3}[[:space:]]*(\([0-9]+\+[0-9]+\))?$' AND pontos ~ '^[0-9]{2,3},[0-9]{2}$'
  AND tempo_2 IS NULL AND penalidade_2 IS NULL;

-- amostra de linhas já corrigidas (equipe preenchida)
SELECT id, colocacao, tempo, penalidade, pontos, equipe
FROM resultados WHERE equipe IS NOT NULL ORDER BY id LIMIT 10;


-- ───────────────────────────────────────────────────────────────────
-- COMO DESFAZER (se precisar), enquanto `resultados_bkp_027` existir:
--   UPDATE resultados r
--     SET tempo = b.tempo, penalidade = b.penalidade, pontos = b.pontos, equipe = NULL
--     FROM resultados_bkp_027 b WHERE r.id = b.id;
--   -- depois, se quiser: DROP TABLE resultados_bkp_027;
-- ───────────────────────────────────────────────────────────────────

SELECT 'OK' AS resultado;
