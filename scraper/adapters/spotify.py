"""Importa TODOS os episódios de um SHOW do Spotify de uma vez (Web API).

Fluxo: token (client-credentials) → show (nome + logo) → episódios (paginado).
Cada episódio vira uma linha em `media` (tipo=podcast) já com a TAG do programa
(p/ a sub-aba), a CAPA do show e a COR dominante da capa.

Requer SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET (app gratuito em
developer.spotify.com). Credenciais só no ambiente — nunca no código.
"""
import base64
import io
import re
import requests

TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
UA = {"User-Agent": "cavalaria-spotify/1.0"}


def token(client_id, client_secret):
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    r = requests.post(TOKEN_URL, data={"grant_type": "client_credentials"},
                      headers={"Authorization": "Basic " + auth, **UA}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def show_id(url):
    m = re.search(r"show/([A-Za-z0-9]+)", url or "")
    return m.group(1) if m else (url or "").strip()


def show(sid, tok, market="BR"):
    r = requests.get(f"{API}/shows/{sid}", params={"market": market},
                     headers={"Authorization": f"Bearer {tok}", **UA}, timeout=30)
    r.raise_for_status()
    return r.json()


def episodes(sid, tok, market="BR"):
    """Todos os episódios do show (segue a paginação `next`)."""
    out = []
    url = f"{API}/shows/{sid}/episodes"
    params = {"market": market, "limit": 50}
    while url:
        r = requests.get(url, params=params,
                         headers={"Authorization": f"Bearer {tok}", **UA}, timeout=30)
        r.raise_for_status()
        j = r.json()
        out += [e for e in (j.get("items") or []) if e]
        url = j.get("next")
        params = None            # `next` já vem com os parâmetros embutidos
    return out


def episode_id(url):
    """Extrai o id de um episódio a partir da URL (open.spotify.com/episode/ID)."""
    m = re.search(r"episode/([A-Za-z0-9]+)", url or "")
    return m.group(1) if m else None


def episode(eid, tok, market="BR"):
    """Detalhe de UM episódio (pra pegar o release_date original). None se falhar."""
    r = requests.get(f"{API}/episodes/{eid}", params={"market": market},
                     headers={"Authorization": f"Bearer {tok}", **UA}, timeout=30)
    return r.json() if r.status_code == 200 else None


def release_to_date(s):
    """release_date do Spotify ('2024', '2024-05' ou '2024-05-01') → data ISO
    completa (preenche mês/dia com 01)."""
    s = (s or "").strip()
    m = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$", s)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2) or '01'}-{m.group(3) or '01'}"


def cor_dominante(img_url):
    """Cor média (hex) da capa — pro balão sair na cor da marca. None se falhar."""
    try:
        from PIL import Image
        b = requests.get(img_url, headers=UA, timeout=20).content
        im = Image.open(io.BytesIO(b)).convert("RGB").resize((1, 1))
        r, g, bl = im.getpixel((0, 0))
        return "#%02x%02x%02x" % (r, g, bl)
    except Exception:
        return None
