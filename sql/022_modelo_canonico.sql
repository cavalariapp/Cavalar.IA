-- ═══════════════════════════════════════════════════════════════════
-- 022 — MODELO CANÔNICO DE EVENTOS  ⚠ DRAFT / PROPOSTA — NÃO RODAR AINDA ⚠
--
--  Esta é a fundação da "solução definitiva": uma camada de VERDADE acima
--  dos dados brutos (torneios/eventos_cbh), desacoplada do scraper.
--
--  NÃO destrói nada: torneios/provas/resultados/torneio_documentos
--  continuam como a camada BRUTA (uma "menção" por fonte). Em cima delas:
--    • eventos        = 1 linha por evento FÍSICO (a verdade do calendário)
--    • evento_fontes  = cada MENÇÃO do evento numa fonte (proveniência)
--    • federacoes     = registro p/ mapear o "organizador declarado" → dono
--    • VIEW calendario= o que o app e o chatbot leem (fim do drift de dedup)
--
--  REGRA DE OURO: documentos/resultados de um evento só valem da fonte com
--  evento_fontes.eh_dono = true (o organizador real). Resolve o problema
--  "federação mostra evento de outra mas não publica programa/resultado".
--
--  Antes de rodar: revisar com a Carol. ORDEM dos próximos passos:
--    023_seed_federacoes = preenche o dicionário `federacoes`.
--    024_resolver        = POPULA eventos/evento_fontes a partir de
--                          torneios + eventos_cbh (com preview e fusão gated).
-- ═══════════════════════════════════════════════════════════════════

-- 1) REGISTRO de federações e clubes ────────────────────────────────
--    Lookup para mapear o texto cru do campo "organizador" (ex.:
--    "FEDERACAO HIPICA DO MATO GROSSO") ao código do dono (FHIMT) e saber
--    a plataforma/tenant de onde extrair.
CREATE TABLE IF NOT EXISTS federacoes (
  codigo        text PRIMARY KEY,            -- 'FPH','FEERJ','FHIMT','CHSA','SHB','CBH'...
  nome          text NOT NULL,               -- 'Federação Paulista de Hipismo'
  variantes     text[] NOT NULL DEFAULT '{}',-- nomes alternativos vistos no campo organizador
  uf            text,                         -- 'SP','RJ','MT'... (NULL p/ CBH nacional)
  tipo          text,                         -- 'federacao' | 'clube' | 'confederacao'
  site          text,
  plataforma    text,                         -- 'macronetwork' | 'wordpress' | 'cbh' | 'outro'
  tenant_macro  text,                         -- subdomínio macronetwork, ex.: 'chsa-inscricao'
  ativo         boolean NOT NULL DEFAULT true
);

-- 2) EVENTO canônico (a verdade — 1 linha por evento físico) ─────────
CREATE TABLE IF NOT EXISTS eventos (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nome_canonico   text NOT NULL,
  disciplina      text,                       -- salto|adestramento|cce|enduro|volteio|atrelagem
  uf              text,
  cidade          text,
  local           text,                       -- venue (clube/sede)
  data_inicio     date,
  data_fim        date,
  federacao_dono  text REFERENCES federacoes(codigo),  -- organizador responsável
  tem_no_cbh      boolean NOT NULL DEFAULT false,
  confianca       numeric(3,2),               -- 0..1: confiança da resolução de identidade
  status          text NOT NULL DEFAULT 'ativo', -- ativo|cancelado|fundido
  fundido_em      uuid REFERENCES eventos(id),   -- se virou duplicata de outro evento
  criado_em       timestamptz NOT NULL DEFAULT now(),
  atualizado_em   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS eventos_periodo_idx   ON eventos (data_inicio, data_fim);
CREATE INDEX IF NOT EXISTS eventos_uf_disc_idx   ON eventos (uf, disciplina);

-- 3) PROVENIÊNCIA: cada MENÇÃO de um evento numa fonte (append-only) ──
--    A re-listagem do FPH e o registro do FHIMT do MESMO evento viram
--    DUAS linhas aqui apontando pro MESMO eventos.id.
CREATE TABLE IF NOT EXISTS evento_fontes (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  evento_id             uuid NOT NULL REFERENCES eventos(id) ON DELETE CASCADE,
  fonte                 text NOT NULL,        -- quem LISTOU (ex.: 'FPH')
  id_nativo             text,                 -- chave ESTÁVEL: tenant+ID macronetwork, permalink WP, ou linha CBH
  url                   text,
  organizador_declarado text,                 -- texto cru do campo organizador
  eh_dono               boolean NOT NULL DEFAULT false,  -- esta fonte é a organizadora/dona?
  tem_provas            boolean NOT NULL DEFAULT false,
  tem_resultados        boolean NOT NULL DEFAULT false,
  tem_documentos        boolean NOT NULL DEFAULT false,
  fingerprint           text,
  torneio_id            bigint REFERENCES torneios(id),  -- liga ao dado BRUTO já existente
  primeiro_visto        timestamptz NOT NULL DEFAULT now(),
  ultimo_visto          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (fonte, id_nativo)                    -- idempotência por chave nativa estável
);
CREATE INDEX IF NOT EXISTS evento_fontes_evento_idx ON evento_fontes (evento_id);
CREATE INDEX IF NOT EXISTS evento_fontes_dono_idx   ON evento_fontes (evento_id) WHERE eh_dono;

-- 4) Ligar o dado BRUTO existente ao canônico (migração suave) ───────
ALTER TABLE torneios     ADD COLUMN IF NOT EXISTS evento_id uuid REFERENCES eventos(id);
ALTER TABLE eventos_cbh  ADD COLUMN IF NOT EXISTS evento_id uuid REFERENCES eventos(id);

-- 5) VIEW única que o app e o chatbot passam a ler (fim do drift) ────
--    1 linha por evento ativo, já com a fonte DONA dos docs/resultados.
CREATE OR REPLACE VIEW calendario AS
SELECT
  e.id, e.nome_canonico AS nome, e.disciplina, e.uf, e.cidade, e.local,
  e.data_inicio, e.data_fim, e.federacao_dono, e.tem_no_cbh, e.confianca,
  (SELECT ef.fonte
     FROM evento_fontes ef
    WHERE ef.evento_id = e.id AND ef.eh_dono
    ORDER BY ef.tem_resultados DESC, ef.tem_documentos DESC, ef.ultimo_visto DESC
    LIMIT 1)                              AS fonte_dona,
  (SELECT count(*) FROM evento_fontes ef WHERE ef.evento_id = e.id) AS qtd_mencoes
FROM eventos e
WHERE e.status = 'ativo';

-- (RLS: liberar SELECT público em calendario/eventos/evento_fontes como já
--  é feito em torneios — definir no 022b após aprovação do schema.)
