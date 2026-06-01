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
import re
import json
import datetime as _dt
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

    # ── helpers HTTP (PostgREST) ─────────────────────────────────────
    def _get(self, path):
        """GET PostgREST → JSON (lista). Usado pra ler o estado atual antes do
        upsert manual (casamento por chave nativa em Python)."""
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}"}
        r = requests.get(self.url + path, headers=h, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path, rows, return_repr=False):
        prefer = "return=representation" if return_repr else "return=minimal"
        r = requests.post(self.url + path, headers=self._headers(prefer),
                          data=json.dumps(rows), timeout=TIMEOUT)
        r.raise_for_status()
        return r.json() if return_repr else None

    def _patch(self, path, patch):
        r = requests.patch(self.url + path, headers=self._headers("return=minimal"),
                           data=json.dumps(patch), timeout=TIMEOUT)
        r.raise_for_status()

    # ── torneios (Fase A: calendário) ────────────────────────────────
    def find_torneio_id(self, fonte, id_nativo):
        """Resolve torneios.id por (fonte, id_nativo) — a chave estável da
        migração 025. None se o torneio ainda não existe (a passada de
        calendário precisa rodar antes). É o FK de provas/torneio_documentos."""
        self._require()
        rows = self._get(
            f"/rest/v1/torneios?fonte=eq.{fonte}&id_nativo=eq.{id_nativo}&select=id")
        return rows[0]["id"] if rows else None

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

    # ── provas (Fase B) — UPSERT FK-SAFE por (torneio_id, id_origem) ──
    #  DESCOBERTA-CHAVE (recon 3436): cada prova tem ID NATIVO estável no href
    #  de Resultados.aspx?ID=N (= id_origem, coluna real de `provas`). Por isso
    #  NÃO apagamos+reinserimos (isso ORFANARIA resultados, cujo FK aponta pra
    #  provas.id): fazemos UPSERT manual casando id_origem, mantendo provas.id
    #  ESTÁVEL. Provas que sumiram da fonte NÃO são apagadas (preservar
    #  resultados > limpar prova obsoleta). Sem chave nativa (id_origem nulo) a
    #  prova é PULADA — sem ela não dá pra deduplicar com segurança no re-scrape.
    #  Grava só colunas CONFIRMADAS de `provas` (information_schema): nome,
    #  id_origem, numero, descricao, categorias, tipo_prova, data_prova,
    #  dia_semana. (horario/local saem do parser mas NÃO são colunas — o front
    #  popula horario client-side a partir do quadro; mandá-los daria 400.)
    _PROVA_PATCH = ("nome", "numero", "descricao", "categorias",
                    "tipo_prova", "data_prova", "dia_semana")

    def upsert_provas(self, torneio_id, provas_rows):
        """Upsert FK-safe de provas por (torneio_id, id_origem). `provas_rows`
        já vêm convertidas por prova_to_row. Devolve contagem por ação."""
        self._require()
        if not provas_rows:
            return {"inseridas": 0, "atualizadas": 0, "puladas": 0}
        existentes = self._get(
            f"/rest/v1/provas?torneio_id=eq.{torneio_id}&select=id,id_origem")
        id_por_origem = {r["id_origem"]: r["id"]
                         for r in existentes if r.get("id_origem")}
        novas, atualizadas, puladas = [], 0, 0
        for row in provas_rows:
            oid = row.get("id_origem")
            if not oid:                      # sem chave nativa: não dá pra dedup
                puladas += 1
                continue
            pid = id_por_origem.get(oid)
            if pid is not None:              # já existe → PATCH (preserva provas.id)
                patch = {k: row[k] for k in self._PROVA_PATCH if k in row}
                self._patch(f"/rest/v1/provas?id=eq.{pid}", patch)
                atualizadas += 1
            else:
                novas.append(row)
        inseridas = 0
        if novas:
            res = self._post("/rest/v1/provas", novas, return_repr=True)
            inseridas = len(res or [])
        return {"inseridas": inseridas, "atualizadas": atualizadas, "puladas": puladas}

    # ── documentos (Fase B) — UPSERT por url_pdf, PRESERVANDO o que o chatbot
    #  extraiu. torneio_documentos tem campos que NÃO são do scraper:
    #  texto_extraido / texto_extraido_em / conteudo_estruturado / estruturado_em
    #  (pipeline do chatbot). O upsert casa por url_pdf e dá PATCH só nos campos
    #  do scraper (tipo, titulo, data_publicacao, visto_em), nunca tocando os
    #  extraídos. Doc novo entra com criado_em+visto_em = agora.
    _DOC_PATCH = ("tipo", "titulo", "data_publicacao")

    def upsert_documentos(self, torneio_id, docs_rows):
        """Upsert de documentos por url_pdf, preservando texto_extraido/
        conteudo_estruturado (consumidos pelo chatbot). `docs_rows` já vêm
        convertidas por documento_to_row. Devolve contagem por ação."""
        self._require()
        if not docs_rows:
            return {"inseridos": 0, "atualizados": 0}
        existentes = self._get(
            f"/rest/v1/torneio_documentos?torneio_id=eq.{torneio_id}&select=id,url_pdf")
        id_por_url = {r["url_pdf"]: r["id"] for r in existentes if r.get("url_pdf")}
        agora = _dt.datetime.now(_dt.timezone.utc).isoformat()
        novos, atualizados = [], 0
        for row in docs_rows:
            url = row.get("url_pdf")
            if not url:
                continue
            doc_id = id_por_url.get(url)
            if doc_id is not None:           # já existe → PATCH (não toca extraídos)
                patch = {k: row[k] for k in self._DOC_PATCH if k in row}
                patch["visto_em"] = agora
                self._patch(
                    f"/rest/v1/torneio_documentos?id=eq.{doc_id}", patch)
                atualizados += 1
            else:
                novos.append({**row, "visto_em": agora, "criado_em": agora})
        inseridos = 0
        if novos:
            res = self._post("/rest/v1/torneio_documentos", novos, return_repr=True)
            inseridos = len(res or [])
        return {"inseridos": inseridos, "atualizados": atualizados}


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


# token de ALTURA (não é categoria): "1,30m", "1.30 m", "0,80M", "1,30"
_ALTURA_TOK = re.compile(r"^\d[.,]\d{2}\s*m?$", re.IGNORECASE)


def _categorias_e_descricao(prova):
    """Separa CATEGORIAS (códigos p/ os chips '/'-split do front) de DESCRIÇÃO
    (texto com altura, de onde o front extrai '1,30m' via alturaPrincipal).

    O parser entrega 'categorias' como o "resto" do nome já deduplicado
    (ex.: 'PR. 01 - 1,30M - JCT - 1,30M - JCT' → '1,30M - JCT'). Aqui:
      • descricao  = esse texto (mantém a ALTURA p/ o front extrair)
      • categorias = só os códigos não-altura, únicos, juntados por '/'
        (contrato do front em resultados.html: categorias.split('/'))
    Ex.: '1,30M - JCT' → descricao='1,30M - JCT', categorias='JCT'."""
    base = (prova.get("categorias") or "").strip()
    segs = [s.strip() for s in base.split(" - ") if s.strip()]
    descricao = " - ".join(segs) or None
    codigos, vistos = [], set()
    for s in segs:
        if _ALTURA_TOK.match(s):              # descarta altura (vai p/ descricao)
            continue
        k = s.lower()
        if k not in vistos:
            vistos.add(k)
            codigos.append(s)
    return ("/".join(codigos) or None), descricao


def prova_to_row(prova, torneio_id):
    """Converte uma prova do parser (macronetwork.parse_provas) na linha de
    `provas`. Só colunas CONFIRMADAS; id_origem é a chave do upsert FK-safe.
    numero vira int (o front ordena por ele aritmeticamente)."""
    categorias, descricao = _categorias_e_descricao(prova)
    numero = prova.get("numero")
    try:
        numero = int(numero) if numero is not None else None
    except (TypeError, ValueError):
        pass                                   # mantém o cru se não for numérico
    return {
        "torneio_id": torneio_id,
        "id_origem": prova.get("id_origem"),
        "nome": prova.get("nome"),
        "numero": numero,
        "descricao": descricao,
        "categorias": categorias,
        "tipo_prova": prova.get("tipo_prova"),
        "data_prova": prova.get("data_prova"),
        "dia_semana": prova.get("dia_semana"),
    }


def documento_to_row(doc, torneio_id):
    """Converte um doc do parser (macronetwork.parse_documentos) na linha de
    `torneio_documentos` — só os campos do SCRAPER. Os campos extraídos pelo
    chatbot (texto_extraido/conteudo_estruturado/...) não são tocados aqui;
    o writer os preserva no upsert por url_pdf."""
    return {
        "torneio_id": torneio_id,
        "tipo": doc.get("tipo"),
        "titulo": doc.get("titulo"),
        "url_pdf": doc.get("url_pdf"),
        "data_publicacao": doc.get("data_publicacao"),
    }
