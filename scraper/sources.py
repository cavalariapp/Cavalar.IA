"""
Registro das FONTES de scraping — a "lista telefônica" do scraper.

`codigo` PRECISA bater EXATAMENTE com torneios.fonte e com federacoes.codigo
(seed em sql/023_seed_federacoes.sql). É o que liga o dado scrapeado ao dono
canônico (sql/022) e permite a regra "só extrai docs/resultados da fonte dona".

Plataformas:
  macronetwork — ASP.NET WebForms. MESMO backend p/ várias federações; cada uma
                 tem domínio próprio e as MESMAS rotas:
                   /calendario/Default                (calendário do mês)
                   /calendario/ListaProvas?ID=N       (detalhe: provas + docs)
                   /calendario/Resultados.aspx?ID=N    (resultados por prova)
                   /calendario/OrdemEntrada.aspx?ID=N  (ordem de entrada)
                 Calendário do MÊS ATUAL + Resultados/Ordem renderizam no GET
                 simples (requests); navegar entre meses e a grade de provas do
                 detalhe exigem Playwright (ver fetch.py).
  wordpress    — site WP (CHSA, SHB, FGEE). Backend de provas varia.
  cbh          — Confederação (calendário nacional, PDFs).

Domínios confirmados ao vivo (recon 2026-06 + backfill de resultados todas as
federações). FHIMT fica como stub (domínio ainda não confirmado).
"""


def _macro(codigo, nome, dominio, ativo=True):
    """Config de uma federação MacroNetwork a partir do domínio (rotas idênticas
    em todas — só muda o host)."""
    base = f"https://{dominio}"
    return {
        "codigo": codigo, "nome": nome, "plataforma": "macronetwork",
        "base": base,
        "calendario_url": f"{base}/calendario/Default",
        "detalhe_url": f"{base}/calendario/ListaProvas?ID={{id}}",
        "resultados_url": f"{base}/calendario/Resultados.aspx?ID={{id}}",
        "ordem_url": f"{base}/calendario/OrdemEntrada.aspx?ID={{id}}",
        "ativo": ativo,
    }


SOURCES = {
    # ── MacroNetwork (domínio confirmado, recon + backfill 2026-06) ──────
    "FPH":   _macro("FPH",   "Federação Paulista de Hipismo",                               "www.fph.com.br"),
    "FEERJ": _macro("FEERJ", "Federação de Esportes Equestres do Estado do Rio de Janeiro", "feerj.org"),
    "SHPR":  _macro("SHPR",  "Sociedade Hípica Paranaense",                                 "www.shpr.com.br"),
    "FE-CE": _macro("FE-CE", "Federação Equestre do Ceará",                                 "federacaoequestrece.com.br"),
    "FSMH":  _macro("FSMH",  "Federação Sul-Mato-Grossense de Hipismo",                     "www.fsmh.com.br"),
    "FEHGO": _macro("FEHGO", "Federação de Esportes Hípicos de Goiás",                      "fehgo.com.br"),
    "FHBR":  _macro("FHBR",  "Federação Hípica de Brasília",                                "www.fhbr.com.br"),

    # domínio ainda não confirmado → stub inativo
    "FHIMT": {
        "codigo": "FHIMT", "nome": "Federação Hípica de Mato Grosso",
        "plataforma": "macronetwork",
        "base": None, "calendario_url": None, "detalhe_url": None,
        "resultados_url": None, "ordem_url": None, "ativo": False,
    },

    # ── outras plataformas (tasks #91) ───────────────────────────────────
    "CHSA": {
        "codigo": "CHSA", "nome": "Clube Hípico de Santo Amaro",
        "plataforma": "wordpress", "tenant_macro": "chsa-inscricao",
        "base": "https://chsa.com.br", "calendario_url": None, "detalhe_url": None,
        "ativo": False,
    },
    "CBH": {
        "codigo": "CBH", "nome": "Confederação Brasileira de Hipismo",
        "plataforma": "cbh",
        "base": "https://cbh.org.br", "calendario_url": None, "detalhe_url": None,
        "ativo": False,
    },
}


def ativos():
    """Fontes prontas pra scrape (recon validada)."""
    return [s for s in SOURCES.values() if s.get("ativo")]


def get(codigo):
    return SOURCES.get(codigo)
