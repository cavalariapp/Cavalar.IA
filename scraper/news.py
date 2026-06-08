"""
Coleta de NOTÍCIAS sem N8N — reconstrução do workflow original.

Fluxo (por item de RSS):
  1. 13 fontes RSS (Google News pt/en + portais: canaldohipismo, jumpernews,
     hippomundo, wbfsh, kwpn, zangersheide, GCL, ge.globo, abcch, fph).
  2. Decodifica o link do Google News (base64) → URL real do artigo.
  3. Dedup por source_url (no writer) + dedup SEMÂNTICA por event_fingerprint.
  4. Baixa o texto do artigo + reescreve com Claude (texto editorial de hipismo,
     com memória das notícias recentes) → {titulo, resumo, conteudo, fingerprint}.
  5. Imagem (Unsplash, se UNSPLASH_ACCESS_KEY) → linhas da tabela `news`.

Sem ANTHROPIC_API_KEY: cai pro modo CRU (título+resumo do RSS, sem reescrita).
Modelos/credenciais vêm do ambiente — NUNCA hardcode.
"""
import base64
import html as _html
import json
import re
import xml.etree.ElementTree as ET
import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; cavalaria-news/1.0)"}
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-5-20250929"

FEEDS = [
    "https://news.google.com/rss/search?q=hipismo+salto&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    "https://www.canaldohipismo.com.br/feed/",
    "https://jumpernews.com/category/results/feed/",
    "https://jumpernews.com/category/news/feed/",
    "https://jumpernews.com/category/events/feed/",
    "https://news.google.com/rss/search?q=site:hippomundo.com&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:wbfsh.com&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:kwpn.org&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:zangersheide.com&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:gcglobalchampions.com&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hipismo+site:ge.globo.com&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    "https://news.google.com/rss/search?q=hipismo+site:abcch.com.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    "https://news.google.com/rss/search?q=hipismo+site:fph.com.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
]


def _txt(s):
    return re.sub(r"<[^>]+>", "", _html.unescape(s or "")).strip()


def _decode_gnews(link):
    """Link do Google News (/articles/<base64>) → URL real do artigo."""
    if not link or "news.google.com" not in link:
        return link
    try:
        enc = link.split("/articles/")[1].split("?")[0]
        dec = base64.urlsafe_b64decode(enc + "===").decode("latin1")
        m = re.search(r"https?://[^\x00-\x1F ]+", dec)
        if m:
            return re.sub(r"[^\x20-\x7E]", "", m.group(0))
    except Exception:
        pass
    return link


def coletar():
    """Lê todas as fontes RSS → [{title, link, pubDate, content}], dedup por link."""
    vistos, out = set(), []
    for feed in FEEDS:
        try:
            xml = requests.get(feed, headers=UA, timeout=30).text
            root = ET.fromstring(xml)
        except Exception:
            continue
        for it in root.iter("item"):
            link = _decode_gnews((it.findtext("link") or "").strip())
            title = _txt(it.findtext("title"))
            if not link or not title or link in vistos:
                continue
            vistos.add(link)
            out.append({
                "title": title, "link": link,
                "pubDate": (it.findtext("pubDate") or "").strip(),
                "content": _txt(it.findtext("description")),
            })
    return out


def fetch_artigo(link, limite=6000):
    """Baixa o texto do artigo (best-effort) pra dar contexto à reescrita."""
    try:
        h = requests.get(link, headers=UA, timeout=25).text
        return _txt(re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", h))[:limite]
    except Exception:
        return ""


_PROMPT = (
    "Você é um redator especialista em hipismo (foco: Salto; também Adestramento, "
    "CCE, Volteio) do portal Cavalar.IA. Público técnico — não explique termos "
    "(jump-off, oxer, fault). NUNCA invente dados (tempo, colocação, linhagem): se "
    "não está no texto, omita. NUNCA altere nomes próprios de cavalos/cavaleiros/"
    "haras/provas — reproduza exatamente como na fonte. Destaque resultado "
    "(vencedor+nacionalidade+cavalo, desempate, categoria/altura), genética quando "
    "o cavalo for citado, e contexto do circuito. Tom direto, opinião técnica "
    "fundamentada permitida, 280–480 palavras, português brasileiro (termos em "
    "inglês do hipismo OK).\n\n"
    "MEMÓRIA (notícias recentes do portal — use pra continuidade, nunca cite "
    "diretamente):\n{memoria}\n\n"
    "Também classifique pra DEDUP de eventos: se for RESULTADO de competição, "
    "monte fingerprint \"sobrenome_vencedor|prova_tipo|venue-ANO\" (tudo minúsculo, "
    "sem acento; venue = só o local, 1 palavra, sem patrocinador/classe). Senão, "
    "fingerprint=null.\n\n"
    "Responda APENAS JSON: {\"titulo\":\"\",\"resumo\":\"\",\"conteudo\":\"\",\"fingerprint\":\"...|null\"}\n"
    "Entrada inválida → {\"titulo\":\"Erro\",\"resumo\":\"\",\"conteudo\":\"\",\"fingerprint\":null}"
)


def reescrever(item, artigo, memoria, key):
    """Claude reescreve a notícia + devolve fingerprint. None se 'Erro'/inválido."""
    user = (f"Título: {item['title']}\nData: {item.get('pubDate','')}\n"
            f"Conteúdo RSS: {item.get('content','')}\n\nArtigo:\n{artigo[:5000]}")
    sistema = _PROMPT.replace("{memoria}", memoria or "(sem histórico)")
    body = {"model": MODEL, "max_tokens": 4096, "system": sistema,
            "messages": [{"role": "user", "content": user}]}
    try:
        r = requests.post(ANTHROPIC_URL, timeout=120, data=json.dumps(body), headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        r.raise_for_status()
        txt = "".join(b.get("text", "") for b in r.json().get("content", []))
        m = re.search(r"\{.*\}", txt, re.S)
        d = json.loads(m.group(0)) if m else None
    except Exception:
        return None
    if not d or (d.get("titulo") or "").strip().lower() == "erro" or not d.get("conteudo"):
        return None
    fp = d.get("fingerprint")
    return {"titulo": d["titulo"], "resumo": d.get("resumo", ""), "conteudo": d["conteudo"],
            "fingerprint": (fp if fp and str(fp).lower() != "null" else None)}


def imagem_unsplash(key):
    """URL de uma foto de salto (Unsplash). None se sem chave/erro."""
    if not key:
        return None
    try:
        r = requests.get("https://api.unsplash.com/search/photos", timeout=20, params={
            "query": "equestrian show jumping horse", "per_page": 10,
            "orientation": "landscape", "client_id": key})
        res = r.json().get("results") or []
        import random
        return random.choice(res[:8])["urls"]["regular"] if res else None
    except Exception:
        return None
