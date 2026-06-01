"""
Camada de FETCH (rede) do scraper MacroNetwork.

Dois caminhos, por necessidade comprovada na recon:

  1. requests (sem navegador) — funciona para o MÊS ATUAL (GET simples do
     /calendario/Default já vem renderizado no servidor) e para baixar PDFs.
     Roda em qualquer lugar (inclusive Python 3.14 local).

  2. Playwright (navegador real) — necessário para:
       • navegar entre MESES/ANOS: a página guarda mês/ano em SESSÃO no
         servidor (ViewState desativado, params de GET ignorados, postback
         depende do form inteiro). No navegador, __doPostBack reenvia o form
         completo com os valores dos dropdowns → o mês troca de verdade.
       • a GRADE DE PROVAS do detalhe: carrega via AJAX (PageRequestManager)
         ao clicar em #...btnListaProva → painel #...pnListaProvas.
     Roda no CI (Python 3.12). Import adiado p/ não quebrar onde não há browser.

O parsing do HTML (datas, cards, provas) fica em adapters/macronetwork.py.
Aqui só obtemos o HTML.
"""
import time
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

DDL_ANO = "ctl00_ContentPlaceHolder1_ddlAno"
DDL_MES = "ctl00_ContentPlaceHolder1_ddlMes"
# Detalhe: as abas do menu disparam __doPostBack e o conteúdo renderiza no
# UpdatePanel upCard (confirmado ao vivo — os painéis pn* só têm o rótulo).
BTN_PROVAS = "ctl00_ContentPlaceHolder1_mpMenuNovo_btnListaProva"
BTN_PROGRAMAS = "ctl00_ContentPlaceHolder1_mpMenuNovo_btnProgramas"
UPCARD = "ctl00_ContentPlaceHolder1_upCard"


# ── caminho 1: requests (sem navegador) ──────────────────────────────
def http_get(url, timeout=30):
    """GET tolerante (FPH às vezes manda Content-Length errado → stream)."""
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout, stream=True)
    r.raise_for_status()
    data = r.raw.read(decode_content=True)
    return data.decode(r.encoding or "utf-8", errors="replace")


def fetch_calendar_current(source):
    """HTML do MÊS ATUAL, página 1 (GET simples — sem navegador)."""
    return http_get(source["calendario_url"])


# ── caminho 2: Playwright (navegador) ────────────────────────────────
def _new_page(pw, headless=True):
    browser = pw.chromium.launch(headless=headless)
    ctx = browser.new_context(user_agent=UA, locale="pt-BR")
    return browser, ctx, ctx.new_page()


def fetch_calendar_month(source, year, month, headless=True, settle_ms=1500):
    """
    Lista de HTMLs (uma por página do pager) do calendário em (year, month).
    Estratégia: seleciona ddlAno/ddlMes e dispara o postback que reenvia o
    form inteiro (__doPostBack), o que faz o servidor trocar o mês de fato.
    Verifica que o mês selecionado bateu; percorre o pager.

    ⚠ Validar no CI na 1ª execução: o alvo de postback do "aplicar/buscar"
    pode variar por tenant. Tentamos lbbusca e, como fallback, o filtro ctl09.
    """
    from playwright.sync_api import sync_playwright
    from scraper.adapters.macronetwork import read_selected_month, read_pager_pages

    pages_html = []
    with sync_playwright() as pw:
        browser, ctx, page = _new_page(pw, headless)
        try:
            page.goto(source["calendario_url"], wait_until="domcontentloaded")
            page.select_option(f"#{DDL_ANO}", str(year))
            page.select_option(f"#{DDL_MES}", str(month))

            # dispara o postback que reenvia o form completo
            applied = _apply_filters(page)
            page.wait_for_timeout(settle_ms)

            html = page.content()
            got = read_selected_month(html)
            if got != month:
                # fallback: tenta o outro gatilho de postback
                _apply_filters(page, prefer="ctl09")
                page.wait_for_timeout(settle_ms)
                html = page.content()
                got = read_selected_month(html)
            pages_html.append(html)

            # paginação dentro do mês (pager: 1,2,...)
            seen = {"1"}
            for pg in [p for p in read_pager_pages(html) if p not in seen]:
                try:
                    page.click(f"input[name*='btnPageNumber'][value='{pg}']")
                    page.wait_for_timeout(settle_ms)
                    pages_html.append(page.content())
                    seen.add(pg)
                except Exception:
                    break
        finally:
            browser.close()
    return pages_html


def _apply_filters(page, prefer="lbbusca"):
    """Dispara o postback de aplicar filtros. Devolve True se chamou algo."""
    targets = {
        "lbbusca": "ctl00$lbbusca",
        "ctl09": "ctl00$ContentPlaceHolder1$ctl09",
    }
    order = [prefer] + [k for k in targets if k != prefer]
    for k in order:
        try:
            page.evaluate("(t) => __doPostBack(t, '')", targets[k])
            return True
        except Exception:
            continue
    return False


def _capture_upcard(page, menu_id, settle_ms, timeout_ms):
    """Clica (clique REAL no <a>) a aba `menu_id` e devolve o innerHTML do
    upCard após renderizar. NÃO lança: devolve o que houver (mesmo vazio/None),
    pra ser diagnóstico — torneio sem provas pode esvaziar o upCard."""
    try:
        page.click(f"#{menu_id}", timeout=timeout_ms)
    except Exception:
        return None
    try:  # espera o upCard ter conteúdo real (sair de vazio/"Carregando")
        page.wait_for_function(
            """(uid) => {
                const el = document.getElementById(uid);
                if (!el) return false;
                const t = (el.innerText || '').trim();
                return t.length > 30 && !/Carregando/i.test(t);
            }""",
            arg=UPCARD, timeout=timeout_ms,
        )
    except Exception:
        pass  # nada publicado → devolve o que tiver
    page.wait_for_timeout(settle_ms)
    return page.evaluate(
        "(uid) => { const el = document.getElementById(uid); return el ? el.innerHTML : null; }",
        UPCARD,
    )


def fetch_detail(source, id_nativo, headless=True, settle_ms=1200, timeout_ms=15000):
    """
    Captura o DETALHE de um torneio (ListaProvas?ID=N) com navegador.

    Devolve dict:
      header_html : HTML do GET (cabeçalho estático: título etc.)
      provas_html : innerHTML do upCard após clicar "Lista de Provas" (ou None)
      docs_html   : innerHTML do upCard após clicar "Programas/Adendos" (ou None)

    ⚠ VALIDAÇÃO PENDENTE NO CI: o postback (__doPostBack p/ btnListaProva/
      btnProgramas) renderiza o conteúdo DENTRO do upCard via PageRequestManager.
      CONFIRMADO ao vivo: os painéis pn* só têm o RÓTULO; o conteúdo vai pro
      upCard. NÃO confirmado: o caminho feliz da grade renderizada — o 3316 não
      tinha provas publicadas (upCard esvaziou). Rodar `--dump-detail` num
      torneio CONCLUÍDO no CI pra capturar a fixture real e travar o parser.
    """
    from playwright.sync_api import sync_playwright

    url = source["detalhe_url"].format(id=id_nativo)
    result = {"header_html": None, "provas_html": None, "docs_html": None}
    with sync_playwright() as pw:
        browser, ctx, page = _new_page(pw, headless)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(settle_ms)
            result["header_html"] = page.content()
            result["provas_html"] = _capture_upcard(page, BTN_PROVAS, settle_ms, timeout_ms)
            # recarrega p/ estado limpo: o postback troca o upCard no lugar
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(settle_ms)
            result["docs_html"] = _capture_upcard(page, BTN_PROGRAMAS, settle_ms, timeout_ms)
        finally:
            browser.close()
    return result
