"""
Registro das FONTES de scraping — a "lista telefônica" do scraper.

`codigo` PRECISA bater EXATAMENTE com torneios.fonte e com federacoes.codigo
(seed em sql/023_seed_federacoes.sql). É o que liga o dado scrapeado ao dono
canônico (sql/022) e permite a regra "só extrai docs/resultados da fonte dona".

Plataformas:
  macronetwork — ASP.NET WebForms (FPH, FEERJ, FHIMT...). Calendário guarda
                 mês/ano em sessão no servidor → navegação via Playwright.
  wordpress    — site WP (CHSA, SHB, FGEE). Backend de provas varia.
  cbh          — Confederação (calendário nacional, PDFs).

Só FPH está COMPLETA (recon ao vivo feita). As demais entram conforme a recon
de cada uma for concluída — por isso ficam com `ativo=False` até validadas.
"""

SOURCES = {
    "FPH": {
        "codigo": "FPH",
        "nome": "Federação Paulista de Hipismo",
        "plataforma": "macronetwork",
        "base": "https://www.fph.com.br",
        "calendario_url": "https://www.fph.com.br/calendario/Default",
        # detalhe: ListaProvas.aspx?ID=N redireciona 301 -> /calendario/ListaProvas?ID=N
        "detalhe_url": "https://www.fph.com.br/calendario/ListaProvas?ID={id}",
        # Fase C: resultados/ordem POR PROVA (id_origem). RENDERIZAM NO GET simples
        # (sem Playwright) — confirmado na recon 2026-06 (ViewState desativado).
        "resultados_url": "https://www.fph.com.br/calendario/Resultados.aspx?ID={id}",
        "ordem_url": "https://www.fph.com.br/calendario/OrdemEntrada.aspx?ID={id}",
        "ativo": True,
    },

    # ── A VALIDAR (recon pendente; mesma plataforma macronetwork) ────
    "FEERJ": {
        "codigo": "FEERJ",
        "nome": "Federação de Esportes Equestres do Estado do Rio de Janeiro",
        "plataforma": "macronetwork",
        "base": None, "calendario_url": None, "detalhe_url": None,
        "ativo": False,
    },
    "FHIMT": {
        "codigo": "FHIMT",
        "nome": "Federação Hípica de Mato Grosso",
        "plataforma": "macronetwork",
        "base": None, "calendario_url": None, "detalhe_url": None,
        "ativo": False,
    },

    # ── outras plataformas (entram nas tasks #91) ────────────────────
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
