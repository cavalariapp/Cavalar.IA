-- ═══════════════════════════════════════════════════════════════════
-- Migração 028 — COMPLETA a 027 (provas POR EQUIPE — parte 2)
--
--  O QUE A 027 DEIXOU DE FORA (descoberto conferindo o resultado dela):
--    A 027 consertou as linhas em que a coluna "Equipe" trazia um CÓDIGO
--    DE CLUBE escrito só com letras (PMDF, PMMT, CDE…). Mas na MESMA prova
--    por equipe existem mais linhas com o mesmíssimo embaralho que ela
--    NÃO tocou, porque eu fui conservador demais no filtro:
--      (1) competidores que correram SEM equipe → a coluna "Equipe" veio
--          "-" (traço) ou vazia;  ........................  ~10.026 linhas
--      (2) códigos de equipe que CONTÊM número (CHSA - 5, E3I, HBF - 02),
--          que a 027 pulou por exigir "penalidade sem dígito".  ~72 linhas
--    Em todas elas o scraper antigo embaralhou igual:
--        tempo      = faltas (ex.: "0", "4", "8")     ← devia ser o TEMPO
--        penalidade = a Equipe ("-", "", "CHSA - 5")  ← devia ser a FALTA
--        pontos     = tempo de pista ("58,92")        ← devia estar vazio
--    (e a variante com STATUS: tempo="Eliminado", penalidade="-", etc.)
--
--  POR QUE É SEGURO (o sinal do bug é o VALOR, não o tipo de prova):
--    Num resultado CERTO, `tempo` guarda o tempo de pista, que SEMPRE tem
--    vírgula (ex.: "58,92") e nunca é "0". Quando `tempo` é um inteiro
--    curto (0,4,8,12…) E `pontos` está no formato NN,NN (>= 10s), só pode
--    ser linha embaralhada. Conferi também a COERÊNCIA do ranking em
--    dezenas de provas: reinterpretando essas linhas, as faltas crescem
--    junto com a colocação (0 antes de 4 antes de 8…) — bate certinho.
--
--  ZERO PERDA: "-"/vazio viram equipe NULL (sem equipe mesmo); um código
--    de verdade (CHSA - 5) vai pra coluna `equipe`. A falta e o tempo vão
--    pras colunas certas.
--
--  SEGURANÇA (igual à 027): PASSO 1 só LÊ. PASSO 2 faz BACKUP (desfaz
--    100%). Só o PASSO 4 escreve. Idempotente: o filtro `equipe IS NULL`
--    impede mexer de novo nas linhas que a 027 (ou esta) já arrumou.
--
--  FORA DE ESCOPO (ficam pra revisão manual, ~10 linhas): casos em que a
--    própria `penalidade` já é um status ("Eliminado") — contraditório
--    (status + falta + tempo na mesma linha). NÃO são tocados aqui.
-- ═══════════════════════════════════════════════════════════════════


-- ───────────────────────────────────────────────────────────────────
-- PASSO 1 — DIAGNÓSTICO (só leitura). Esperado (medido em 02/jun/2026):
--   A'≈10.098  (9.902 traço + 124 vazio + 72 código-com-número)
--   B'≈ 1.056  (status em `tempo`, sem equipe)
-- ───────────────────────────────────────────────────────────────────

-- (A') faltas em `tempo`, equipe(-/vazio/código) em `penalidade`, tempo em `pontos`.
SELECT 'A2_sem_clube' AS grupo, count(*) AS linhas
FROM resultados
WHERE equipe IS NULL
  AND tempo  ~ '^[0-9]{1,3}[[:space:]]*(\([0-9]+\+[0-9]+\))?$'   -- `tempo` é nº de faltas
  AND pontos ~ '^[0-9]{2,3},[0-9]{2}$'                           -- `pontos` é TEMPO de pista
  AND tempo_2 IS NULL AND penalidade_2 IS NULL                   -- só fase única
  AND (penalidade IS NULL OR lower(btrim(penalidade)) NOT IN     -- penalidade NÃO é status
      ('forfait','forf.','ff','eliminado','eliminada','elim.','elim',
       'desistente','desqualificado','desqualificada','des.','des',
       'abandono','desclassificado','retirado','ausente','nc','np','wd','rt','ab'));

-- (B') status em `tempo`, equipe(-/vazio/código) em `penalidade`, pontos vazio.
SELECT 'B2_sem_clube' AS grupo, count(*) AS linhas
FROM resultados
WHERE equipe IS NULL
  AND lower(btrim(tempo)) IN
      ('eliminado','eliminada','desistente','forfait','desqualificado',
       'desclassificado','abandono','retirado','ausente')
  AND (pontos IS NULL OR btrim(pontos) = '')
  AND tempo_2 IS NULL AND penalidade_2 IS NULL
  AND (penalidade IS NULL OR lower(btrim(penalidade)) NOT IN
      ('forfait','forf.','ff','eliminado','eliminada','elim.','elim',
       'desistente','desqualificado','desqualificada','des.','des',
       'abandono','desclassificado','retirado','ausente','nc','np','wd','rt','ab'));


-- ───────────────────────────────────────────────────────────────────
-- PASSO 2 — BACKUP das linhas afetadas (permite DESFAZER 100%).
--   Cria `resultados_bkp_028` com a CÓPIA original. Idempotente.
-- ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS resultados_bkp_028 AS
SELECT * FROM resultados
WHERE equipe IS NULL
  AND tempo_2 IS NULL AND penalidade_2 IS NULL
  AND (penalidade IS NULL OR lower(btrim(penalidade)) NOT IN
      ('forfait','forf.','ff','eliminado','eliminada','elim.','elim',
       'desistente','desqualificado','desqualificada','des.','des',
       'abandono','desclassificado','retirado','ausente','nc','np','wd','rt','ab'))
  AND (
        ( tempo ~ '^[0-9]{1,3}[[:space:]]*(\([0-9]+\+[0-9]+\))?$'
          AND pontos ~ '^[0-9]{2,3},[0-9]{2}$' )
     OR ( lower(btrim(tempo)) IN
            ('eliminado','eliminada','desistente','forfait','desqualificado',
             'desclassificado','abandono','retirado','ausente')
          AND (pontos IS NULL OR btrim(pontos) = '') )
      );

SELECT 'backup' AS info, count(*) AS linhas_no_backup FROM resultados_bkp_028;


-- ───────────────────────────────────────────────────────────────────
-- PASSO 3 — Garante a coluna `equipe` (já criada na 027; idempotente).
-- ───────────────────────────────────────────────────────────────────
ALTER TABLE resultados ADD COLUMN IF NOT EXISTS equipe TEXT;


-- ───────────────────────────────────────────────────────────────────
-- PASSO 4 — CORREÇÃO (ESCREVE). Confira o PASSO 1 antes de rodar.
--   `equipe`: "-"/vazio → NULL (sem equipe); código → vira o nome do time.
--   Lembre: todo valor à direita do "=" usa o conteúdo ANTIGO da linha.
-- ───────────────────────────────────────────────────────────────────

-- (A') equipe←penalidade(limpa), penalidade←faltas(tempo), tempo←tempo-de-pista(pontos), pontos←NULL
UPDATE resultados
SET equipe     = CASE WHEN penalidade IS NULL
                       OR btrim(penalidade) = ''
                       OR btrim(penalidade) ~ '^-+$' THEN NULL
                      ELSE btrim(penalidade) END,
    penalidade = tempo,
    tempo      = pontos,
    pontos     = NULL
WHERE equipe IS NULL
  AND tempo  ~ '^[0-9]{1,3}[[:space:]]*(\([0-9]+\+[0-9]+\))?$'
  AND pontos ~ '^[0-9]{2,3},[0-9]{2}$'
  AND tempo_2 IS NULL AND penalidade_2 IS NULL
  AND (penalidade IS NULL OR lower(btrim(penalidade)) NOT IN
      ('forfait','forf.','ff','eliminado','eliminada','elim.','elim',
       'desistente','desqualificado','desqualificada','des.','des',
       'abandono','desclassificado','retirado','ausente','nc','np','wd','rt','ab'));

-- (B') equipe←penalidade(limpa), penalidade←status(tempo), tempo←NULL
UPDATE resultados
SET equipe     = CASE WHEN penalidade IS NULL
                       OR btrim(penalidade) = ''
                       OR btrim(penalidade) ~ '^-+$' THEN NULL
                      ELSE btrim(penalidade) END,
    penalidade = tempo,
    tempo      = NULL
WHERE equipe IS NULL
  AND lower(btrim(tempo)) IN
      ('eliminado','eliminada','desistente','forfait','desqualificado',
       'desclassificado','abandono','retirado','ausente')
  AND (pontos IS NULL OR btrim(pontos) = '')
  AND tempo_2 IS NULL AND penalidade_2 IS NULL
  AND (penalidade IS NULL OR lower(btrim(penalidade)) NOT IN
      ('forfait','forf.','ff','eliminado','eliminada','elim.','elim',
       'desistente','desqualificado','desqualificada','des.','des',
       'abandono','desclassificado','retirado','ausente','nc','np','wd','rt','ab'));


-- ───────────────────────────────────────────────────────────────────
-- PASSO 5 — CONFERÊNCIA (só leitura). O grupo A' deve cair pra 0.
-- ───────────────────────────────────────────────────────────────────
SELECT 'restante_A2_apos_fix' AS grupo, count(*) AS linhas
FROM resultados
WHERE equipe IS NULL
  AND tempo  ~ '^[0-9]{1,3}[[:space:]]*(\([0-9]+\+[0-9]+\))?$'
  AND pontos ~ '^[0-9]{2,3},[0-9]{2}$'
  AND tempo_2 IS NULL AND penalidade_2 IS NULL
  AND (penalidade IS NULL OR lower(btrim(penalidade)) NOT IN
      ('forfait','forf.','ff','eliminado','eliminada','elim.','elim',
       'desistente','desqualificado','desqualificada','des.','des',
       'abandono','desclassificado','retirado','ausente','nc','np','wd','rt','ab'));

-- amostra: linhas SEM clube já corrigidas (equipe NULL, mas tempo agora é NN,NN e penalidade é a falta)
SELECT id, colocacao, tempo, penalidade, pontos, equipe
FROM resultados
WHERE equipe IS NULL AND tempo ~ '^[0-9]{2,3},[0-9]{2}$' AND penalidade ~ '^[0-9]{1,3}'
ORDER BY id LIMIT 10;


-- ───────────────────────────────────────────────────────────────────
-- COMO DESFAZER (enquanto `resultados_bkp_028` existir):
--   UPDATE resultados r
--     SET tempo = b.tempo, penalidade = b.penalidade, pontos = b.pontos, equipe = b.equipe
--     FROM resultados_bkp_028 b WHERE r.id = b.id;
--   -- depois, se quiser: DROP TABLE resultados_bkp_028;
-- ───────────────────────────────────────────────────────────────────

SELECT 'OK' AS resultado;
