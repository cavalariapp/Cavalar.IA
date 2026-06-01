"""
Camada de ESCRITA no Supabase (PostgREST), idempotente.

SEGURANÇA:
  • Usa a SERVICE_ROLE key (escrita, ignora RLS). Ela vem SÓ de variável de
    ambiente (SUPABASE_SERVICE_KEY) — NUNCA fica no código nem no frontend.
    Em produção: GitHub Secrets. Local: export antes de rodar com --write.
  • Sem env key configurada, o writer recusa escrever (modo dry-run força).

IDEMPOTÊNCIA:
  • torneios: upsert por (fonte, id_nativo) — a chave estável criada na
    migração 025. Rodar o scraper N vezes não duplica: atualiza no lugar.
  • fingerprint NÃO é gravado pelo scraper de propósito: o esquema legado é
    ambíguo e a dedup canônica (sql/024_resolver) usa nome normalizado +
    sobreposição de datas, não fingerprint. id_nativo é a chave nova.
"""
import os
import json
import requests

TIMEOUT = 30


class SupabaseWriter:
    def __init__(self, url=None, key=None):
        self.url = (url or os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.key = key or os.environ.get("SUPABASE_SERVICE_KEY") or ""

    @property
    def configured(self):
        return bool(self.url and self.key)

    def _headers(self, prefer):
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": prefer,
        }

    def _require(self):
        if not self.configured:
            raise RuntimeError(
                "Supabase não configurado: defina SUPABASE_URL e "
                "SUPABASE_SERVICE_KEY no ambiente (ou rode em --dry-run)."
            )

    # ── torneios (Fase A: calendário) ────────────────────────────────
    def upsert_torneios(self, rows):
        """
        Upsert idempotente em torneios por (fonte, id_nativo).
        `rows`: lista de dicts {nome, fonte, data_inicio, data_fim,
                                id_nativo, organizador}.
        Retorna a representação gravada (inclui id + id_nativo) p/ ligar provas.
        """
        self._require()
        if not rows:
            return []
        endpoint = f"{self.url}/rest/v1/torneios?on_conflict=fonte,id_nativo"
        r = requests.post(
            endpoint,
            headers=self._headers("resolution=merge-duplicates,return=representation"),
            data=json.dumps(rows),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()

    # ── provas / documentos (Fase B: detalhe via Playwright) ─────────
    #  BLOQUEIO ATUAL (#92): a grade renderiza via AJAX no upCard e ainda NÃO
    #  foi capturada de um torneio concluído (o 3316 não tinha provas). Passos
    #  pra destravar, nesta ordem:
    #    1. `--dump-detail <ID concluído>` no CI → salvar fixture do upCard.
    #    2. Implementar adapters.macronetwork.parse_provas/parse_documentos
    #       contra a fixture (com teste).
    #    3. CONFIRMAR as colunas reais (não estão nas migrações do repo; vivem
    #       só no Supabase). Conhecido até aqui: provas(torneio_id, data_prova,
    #       ...); torneio_documentos(torneio_id, tipo, url, texto_extraido,
    #       criado_em). Rodar antes de gravar:
    #         select column_name,data_type from information_schema.columns
    #          where table_name in ('provas','torneio_documentos');
    #    4. Definir a chave: provas provavelmente NÃO tem chave estável → trocar
    #       o conjunto por torneio (delete-by-torneio_id + insert).
    def replace_provas(self, torneio_id, provas_rows):
        raise NotImplementedError(
            "Fase B — capturar fixture do upCard (--dump-detail) + confirmar "
            "colunas de `provas` antes de habilitar."
        )

    def upsert_documentos(self, docs_rows):
        raise NotImplementedError(
            "Fase B — capturar fixture do upCard (--dump-detail) + confirmar "
            "colunas de `torneio_documentos` antes de habilitar."
        )


def evento_to_torneio_row(ev, fonte):
    """Converte um evento parseado (adapters) na linha de torneios."""
    return {
        "nome": ev.get("nome"),
        "fonte": fonte,
        "data_inicio": ev.get("data_inicio"),
        "data_fim": ev.get("data_fim"),
        "id_nativo": ev.get("id_nativo"),
        "organizador": ev.get("organizador_entidade"),
    }
