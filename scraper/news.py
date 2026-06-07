"""
Coleta de NOTÍCIAS sem N8N. Fonte: Google News RSS (pt-BR), por consultas de
hipismo. Devolve linhas no schema da tabela `news` (title, excerpt, body, date,
source_url, cat). Dedup por source_url fica no writer (db.upsert_news).
"""
import html as _html
import re
import xml.etree.ElementTree as ET
import requests

UA = {"User-Agent": "Mozilla/5.0 (cavalaria-news)"}
RSS = "https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
QUERIES = [
    "hipismo", "salto hípico", "concurso completo de equitação", "adestramento equino",
    "CSN hipismo", "CBH hipismo confederação", "cavaleiro brasileiro salto",
    "amazona hipismo", "haras salto Brasil",
]


def _txt(s):
    return re.sub(r"<[^>]+>", "", _html.unescape(s or "")).strip()


def coletar_noticias(max_por_query=20):
    """[{title, excerpt, body, date, source_url, cat}] de várias buscas, dedup por link."""
    vistos, out = set(), []
    for q in QUERIES:
        try:
            xml = requests.get(RSS.format(q=requests.utils.quote(q)), headers=UA, timeout=30).text
            root = ET.fromstring(xml)
        except Exception:
            continue
        for it in list(root.iter("item"))[:max_por_query]:
            link = (it.findtext("link") or "").strip()
            title = _txt(it.findtext("title"))
            if not link or link in vistos or not title:
                continue
            vistos.add(link)
            desc = _txt(it.findtext("description"))
            out.append({
                "title": title[:300],
                "excerpt": desc[:400],
                "body": desc,
                "date": (it.findtext("pubDate") or "").strip(),
                "source_url": link,
                "cat": "hipismo",
            })
    return out
