"""Adapter ABCCH (studbook genealógico) — api.abcch.com.br.

O site abcch.com.br é um SPA que consome a API pública:
  GET /pesquisa/<termo>   → busca por SUBSTRING no nome; retorna a lista INTEIRA
                            de uma vez (sem paginação). Cada item já traz a
                            FILIAÇÃO (pai/mãe), sexo, nascimento e registro.
  GET /animais/<CdToken>  → genealogia detalhada (árvore) — não usado no Passo 1.

EXTRAÇÃO DE TODOS OS NOMES: varre /pesquisa/ por cada caractere (a–z + 0–9) e
deduplica por CdToken. Como todo nome tem ao menos uma letra, a varredura cobre
o universo (~46k animais) em ~36 requisições, já com pai/mãe — o suficiente pra
ligar resultados esportivos à genealogia. Auth vai vazia (studbook é público).
"""
import time
import requests
from urllib.parse import quote

BASE = "https://api.abcch.com.br"
H = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "application/json",
    "Authorization": "admin=,user=",
    "Origin": "https://www.abcch.com.br",
    "Referer": "https://www.abcch.com.br/",
}
CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


def buscar(termo, timeout=120):
    """GET /pesquisa/<termo> → lista de animais (substring match no nome)."""
    r = requests.get(f"{BASE}/pesquisa/{quote(termo)}", headers=H, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    return (j.get("animais") or []) if isinstance(j, dict) else []


def varrer_todos(chars=CHARS, sleep=0.4, log=print):
    """Varre /pesquisa/ por cada caractere e deduplica por CdToken.
    Retorna {CdToken: animal_dict}."""
    uni = {}
    for ch in chars:
        try:
            an = buscar(ch)
        except Exception as e:
            log(f"  '{ch}': erro {e.__class__.__name__} — pulando")
            continue
        novos = 0
        for a in an:
            tok = a.get("CdToken")
            if tok and tok not in uni:
                novos += 1
            if tok:
                uni[tok] = a
        log(f"  '{ch}': {len(an):>6} resultados | +{novos} novos | total único {len(uni)}")
        time.sleep(sleep)
    return uni


def detalhe(cd_token, timeout=60):
    """GET /animais/<CdToken> → genealogia detalhada (Passo 2, opcional)."""
    r = requests.get(f"{BASE}/animais/{quote(str(cd_token))}", headers=H, timeout=timeout)
    r.raise_for_status()
    return r.json()


def to_row(a):
    """Animal da busca → linha da tabela `genealogia`. CdGender: 'M'/'F' (macho/
    fêmea) conforme a fonte; guardamos o valor cru."""
    def s(k):
        v = a.get(k)
        return v.strip() if isinstance(v, str) and v.strip() else None
    dt = a.get("DtFoaled")
    return {
        "cd_token": a.get("CdToken"),
        "nome": s("NmAnimal"),
        "nome_completo": s("NmAnimalComplete"),
        "registro": s("NrRegistration"),
        "nascimento": (dt[:10] if isinstance(dt, str) and len(dt) >= 10 else None),
        "sexo": s("CdGender"),
        "pai": s("NmAnimalSire"),
        "mae": s("NmAnimalDam"),
        "proprietario": s("NmUserOwner"),
    }
