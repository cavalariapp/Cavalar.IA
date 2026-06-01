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
       • a GRADE DE PROVAS do detalhe: a aba "Lista de Provas" AUTO-renderiza
         no upCard ao abrir a página (sem clique) — uma SANFONA por dia do
         torneio, cada dia COLAPSADO. Cada dia é <button type=submit> + input
         hidden + spinner "Carregando"; EXPANDIR dispara um 2º postback que
         carrega as provas daquele dia (lazy-load em DOIS níveis). Trocar de
         aba (ex.: Programas/Adendo) também é __doPostBack. Tudo isso exige
         clique de navegador REAL (Playwright): na recon ao vivo (2026-06), os
         cliques sintéticos do MCP NÃO completavam o PageRequestManager — o dia
         ficava preso em "Carregando" e a aba não trocava. Só o auto-load do
         GET natural renderizava. Playwright dispara o __doPostBack de verdade.
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
# A aba "Lista de Provas" é a DEFAULT: AUTO-renderiza no upCard ao abrir.
# Recon 2026-06 (torneio FPH-próprio 3436 vs. fantasma CBH 3440):
#   • 3436 (dono FPH): sanfona de dias renderizou + tinha aba "Programas/Adendo".
#   • 3440 (dono CBH, exibido na FPH): upCard ficou só "Loading..." e SEM aba
#     Programas → AUSÊNCIA de grade/Programas = sinal forte de FANTASMA (a FPH
#     não é dona; aplica a "regra de ouro": só extrair da fonte dona).
BTN_PROVAS = "ctl00_ContentPlaceHolder1_mpMenuNovo_btnListaProva"
BTN_PROGRAMAS = "ctl00_ContentPlaceHolder1_mpMenuNovo_btnProgramas"
UPCARD = "ctl00_ContentPlaceHolder1_upCard"
# Sanfona por dia DENTRO do upCard: cada dia tem um <button type=submit> que
# dispara o 2º postback (carrega as provas daquele dia). O id do botão é
# dinâmico (não capturável via árvore de acessibilidade) — o 1º dump no CI
# revela o seletor exato. Por ora expandimos por ESTRUTURA, best-effort.
DAY_SUBMIT_SEL = f"#{UPCARD} button[type=submit]"


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


def _capture_upcard(page, menu_id, settle_ms, timeout_ms, expand_days=False):
    """Seleciona a aba `menu_id` (clique REAL no <a> __doPostBack) e devolve o
    innerHTML do upCard depois de renderizar. NÃO lança: devolve o que houver
    (mesmo vazio/None) — é diagnóstico. A aba default ("Lista de Provas") já vem
    AUTO-renderizada do GET; o clique só reforça/garante a troca.

    expand_days=True: tenta EXPANDIR cada dia da sanfona (2º postback) antes de
    capturar, pra trazer as provas. Best-effort e defensivo — se o seletor não
    bater, devolve a sanfona colapsada (o 1º dump no CI revela o seletor certo).
    """
    try:
        page.click(f"#{menu_id}", timeout=timeout_ms)
    except Exception:
        pass  # a aba default já pode estar renderizada; segue pra capturar
    try:  # espera o upCard ter conteúdo real (sair de vazio/"Carregando")
        page.wait_for_function(
            """(uid) => {
                const el = document.getElementById(uid);
                if (!el) return false;
                const t = (el.innerText || '').trim();
                return t.length > 30 && !/^\\s*Carregando/i.test(t);
            }""",
            arg=UPCARD, timeout=timeout_ms,
        )
    except Exception:
        pass  # nada publicado (provável fantasma) → devolve o que tiver
    if expand_days:
        _expand_days(page, settle_ms, timeout_ms)
    page.wait_for_timeout(settle_ms)
    return page.evaluate(
        "(uid) => { const el = document.getElementById(uid); return el ? el.innerHTML : null; }",
        UPCARD,
    )


def _expand_days(page, settle_ms, timeout_ms, max_days=15):
    """Best-effort: clica cada <button type=submit> da sanfona pra carregar as
    provas do dia (2º nível de lazy-load). Re-consulta a lista após cada clique
    (o postback troca o DOM no lugar) e NUNCA lança — no pior caso a sanfona
    fica como estava e o dump traz só os cabeçalhos de dia.

    ⚠ CI iteração-1: confirmar DAY_SUBMIT_SEL e o COMPORTAMENTO no HTML real do
      dump — sem ViewState, o servidor talvez não guarde qual dia foi expandido,
      então expandir o dia 2 pode recolher o 1. Se for o caso, capturar um dia
      por vez. NÃO inventar id/estrutura: ajustar contra o dump."""
    for i in range(max_days):
        try:
            btns = page.locator(DAY_SUBMIT_SEL)
            n = btns.count()
        except Exception:
            return
        if n == 0 or i >= n:
            break
        try:
            btns.nth(i).click(timeout=timeout_ms)
            page.wait_for_timeout(settle_ms)  # deixa o spinner "Carregando" resolver
        except Exception:
            continue


def fetch_detail(source, id_nativo, headless=True, settle_ms=1200, timeout_ms=15000):
    """
    Captura o DETALHE de um torneio (ListaProvas?ID=N) com navegador.

    Devolve dict:
      header_html : HTML do GET (cabeçalho estático: título etc.)
      provas_html : innerHTML do upCard (aba "Lista de Provas", com os dias
                    EXPANDIDOS quando possível) — ou None
      docs_html   : innerHTML do upCard após clicar "Programas/Adendos" (ou None)

    MODELO CONFIRMADO (recon 2026-06, FPH 3436 dono vs. 3440 fantasma CBH):
      • A aba "Lista de Provas" AUTO-renderiza no upCard ao abrir (sanfona por
        dia, colapsada). Só o auto-load do GET completa sozinho.
      • Expandir um dia e trocar de aba são __doPostBack que SÓ um navegador
        real (Playwright) dispara de verdade — daí rodar isto no CI.
      • Fantasma (dono != FPH): upCard fica em "Loading..." e some a aba
        Programas → não há o que extrair (regra de ouro: só a fonte dona).

    ⚠ AINDA PENDENTE DE 1 DUMP NO CI: ver o HTML real da grade expandida pra
      (a) confirmar DAY_SUBMIT_SEL e o comportamento da sanfona, e (b) travar
      adapters.macronetwork.parse_provas/parse_documentos contra a fixture.
      Use `--dump-detail 3436` (torneio FPH-próprio CONCLUÍDO) no CI.
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
            # provas: a aba default já auto-renderiza; tentamos expandir os dias
            result["provas_html"] = _capture_upcard(
                page, BTN_PROVAS, settle_ms, timeout_ms, expand_days=True)
            # recarrega p/ estado limpo: o postback troca o upCard no lugar
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(settle_ms)
            result["docs_html"] = _capture_upcard(page, BTN_PROGRAMAS, settle_ms, timeout_ms)
        finally:
            browser.close()
    return result
