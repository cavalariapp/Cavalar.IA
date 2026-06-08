"""Adapter LiveHorse (livehorse.com.br) — sistema de resultados da FGEE
(Federação Gaúcha) e clubes do RS.

Resultado POR PROVA em PDF (texto extraível, mas CAVALO×CONCORRENTE sem
delimitador → estruturação via Claude). Fluxo:
  wp-json/wp/v2/mec-events            → lista de eventos (slug, título, link)
  <link do evento>                    → PDFs 'resultado-<evento>-pN.pdf'
  cada PDF resultado → pypdf (texto)  → Claude → {prova + linhas}

Requer ANTHROPIC_API_KEY (só a estruturação). Descoberta/PDF são determinísticos.
"""
import hashlib
import io
import json
import re
import requests

BASE = "https://livehorse.com.br"
WPJSON = BASE + "/wp-json/wp/v2/mec-events"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-5-20250929"
H = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "*/*", "Accept-Language": "pt-BR,pt;q=0.9",
}
_MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
          "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
          "outubro": 10, "novembro": 11, "dezembro": 12}


def _clean(s):
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def listar_eventos(max_paginas=4):
    """Eventos do LiveHorse via wp-json (mais recentes primeiro)."""
    out = []
    for pg in range(1, max_paginas + 1):
        r = requests.get(WPJSON, headers=H, timeout=45, params={
            "per_page": 100, "page": pg, "orderby": "date", "order": "desc",
            "_fields": "id,slug,link,title,date"})
        if r.status_code != 200:
            break
        j = r.json()
        if not isinstance(j, list) or not j:
            break
        for e in j:
            out.append({"id": e["id"], "slug": e.get("slug"), "link": e.get("link"),
                        "nome": _clean((e.get("title") or {}).get("rendered", "")),
                        "post_date": (e.get("date") or "")[:10]})
        if len(j) < 100:
            break
    return out


def resultado_pdfs(event_link):
    """URLs dos PDFs de RESULTADO na página do evento (únicos, ordenados)."""
    h = requests.get(event_link, headers=H, timeout=45).text
    pdfs = re.findall(r'href="([^"]+\.pdf[^"]*)"', h)
    res = {u for u in pdfs if "resultado" in u.split("/")[-1].lower()}
    return sorted(res)


def id_origem_de(url):
    """id_origem estável (int) por PDF — hash do nome do arquivo. Único dentro do
    torneio (resolução de prova é por torneio_id+id_origem)."""
    fn = url.split("/")[-1]
    return int(hashlib.md5(fn.encode("utf-8")).hexdigest()[:7], 16)


def extrair_texto_pdf(url, timeout=60):
    """Texto do PDF (pypdf). Tolerante a Content-Length errado (stream)."""
    from pypdf import PdfReader
    r = requests.get(url, headers=H, timeout=timeout, stream=True)
    r.raise_for_status()
    data = r.raw.read(decode_content=True)
    return "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages).strip()


def data_iso(s):
    """'05/06/2026'→'2026-06-05'; '5 de junho de 2026'→idem. None se não achar."""
    if not s:
        return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.search(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", s, re.I)
    if m and _MESES.get(m.group(2).lower()):
        return f"{int(m.group(3)):04d}-{_MESES[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return None


_PROMPT_RES = (
    "Você recebe o TEXTO de um PDF de RESULTADO de UMA prova de hipismo (salto) do "
    "sistema LiveHorse. Pode haver mais de uma sub-classificação (por categoria) na "
    "mesma prova. Extraia SOMENTE o que está no texto, em JSON válido:\n"
    '{"prova_numero": "01", "prova_nome": "", "data": "DD/MM/AAAA", "tabela": "", '
    '"resultados": [{"colocacao": 1, "cavalo_nome": "", "cavaleiro_nome": "", '
    '"entidade": "", "categoria": "", "penalidade": "0", "tempo": "70,22"}]}\n'
    "REGRAS CRÍTICAS:\n"
    "- As colunas são: ORD ID CAVALO CONCORRENTE ENT. CAT. (PTS|SEG) TEMPO CL. "
    "O CAVALO vem ANTES do CONCORRENTE (cavaleiro). Separe os dois corretamente "
    "(ambos podem ter várias palavras; ENT. é a sigla da federação, ex.: SHPA, CRM, "
    "FGEE — use-a como fronteira).\n"
    "- penalidade = a coluna de faltas/pontos (PTS ou SEG). Se eliminado/abandonou/"
    "fora, use 'ELIM'/'ABAND'/'NC'/'WD' conforme o texto.\n"
    "- tempo no formato 00,00 (vazio se não houver).\n"
    "- colocacao = número da coluna CL (sem 'º'); null se não classificou.\n"
    "- NÃO invente nada. Não altere nomes próprios. Campo ausente = '' ou null.\n"
    "Responda APENAS o JSON, sem texto antes/depois."
)


def estruturar_resultado(texto, api_key):
    """PDF de resultado (texto) → dict {prova_*, resultados:[...]} via Claude.
    None se texto vazio / resposta inválida."""
    if not texto:
        return None
    body = {"model": MODEL, "max_tokens": 8192,
            "messages": [{"role": "user",
                          "content": _PROMPT_RES + "\n\n=== TEXTO ===\n" + texto[:60000]}]}
    r = requests.post(ANTHROPIC_URL, timeout=180, data=json.dumps(body), headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01",
        "content-type": "application/json"})
    r.raise_for_status()
    txt = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    return d if isinstance(d, dict) and d.get("resultados") else None
