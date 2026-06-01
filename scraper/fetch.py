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
# Sanfona por dia DENTRO do upCard (CONFIRMADO no dump CI 3436, run 26755256049):
# cada dia é
#   <p class="lstProva accordion grid_accordion" onclick="toggleCard(this)">
#     <span class="gridCol">quinta-feira - 28/mai/2026</span>
#     <input type="hidden" id="...hfdData" value="quinta-feira, 28 de maio de 2026">
#     <img class="load hide" alt="Carregando">                    ← spinner
#     <input type="submit" class="hide btnDiaProva" ...>          ← postback (OCULTO)
#     <svg class="bi-chevron-down ...">
#   </p>
# O <input type=submit> é display:none → o gatilho REAL é o <p> (onclick=toggleCard,
# que mostra o spinner e dispara o 2º postback). Por isso o seletor é o HEADER <p>,
# NÃO um button[type=submit] (que não existe — era a causa de a sanfona nunca abrir).
DAY_HEADER_SEL = f"#{UPCARD} p.grid_accordion"


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


def _safe(thunk, default=None):
    """Roda `thunk()` e devolve o resultado; em QUALQUER exceção devolve
    `default`. Usado pra isolar cada captura (uma falha não derruba as outras
    nem aborta o dump inteiro — lição da run 26754246983: o clique em Programas
    destruía o contexto e levava junto as provas JÁ capturadas)."""
    try:
        return thunk()
    except Exception:
        return default


def _read_upcard_html(page, timeout_ms):
    """Lê upCard.innerHTML TOLERANDO navegação. O __doPostBack de algumas abas é
    postback FULL (recarrega a página) e destrói o contexto JS no meio do
    page.evaluate ("Execution context was destroyed"). Tentamos algumas vezes,
    estabilizando o load entre tentativas; por fim devolvemos None (o chamador
    ainda tem o page.content() inteiro como fallback de diagnóstico)."""
    for _ in range(3):
        h = _safe(lambda: page.evaluate(
            "(uid) => { const el = document.getElementById(uid); return el ? el.innerHTML : null; }",
            UPCARD,
        ), default="__RETRY__")
        if h != "__RETRY__":
            return h
        _safe(lambda: page.wait_for_load_state("domcontentloaded", timeout=timeout_ms))
        _safe(lambda: page.wait_for_timeout(500))
    return None


def _capture_upcard(page, menu_id, settle_ms, timeout_ms):
    """Seleciona a aba `menu_id` (clique REAL no <a> __doPostBack) e devolve o
    innerHTML do upCard depois de renderizar. NÃO lança: devolve o que houver
    (mesmo vazio/None) — é diagnóstico. Usado p/ a aba Programas, cujo postback é
    FULL (o upCard fica vazio); o conteúdo real vem do page.content() inteiro
    (docs_page_html). Mantido por diagnóstico — pode ter conteúdo em outro tenant."""
    _safe(lambda: page.click(f"#{menu_id}", timeout=timeout_ms))
    _safe(lambda: page.wait_for_function(
        """(uid) => {
            const el = document.getElementById(uid);
            if (!el) return false;
            const t = (el.innerText || '').trim();
            return t.length > 30 && !/^\\s*Carregando/i.test(t);
        }""",
        arg=UPCARD, timeout=timeout_ms,
    ))
    _safe(lambda: page.wait_for_timeout(settle_ms))
    return _read_upcard_html(page, timeout_ms)


def _extract_upcard(page_html):
    """Extrai SÓ o subtree do upCard de um page.content() inteiro — compacta o
    snapshot por dia (não precisamos do chrome de ~130KB da página em cada dump).
    Import bs4 adiado (já é dep de adapters/macronetwork → existe no CI e no venv).
    Devolve None se não achar/der erro → o chamador cai pro page inteiro."""
    if not page_html:
        return None
    try:
        from bs4 import BeautifulSoup
        el = BeautifulSoup(page_html, "html.parser").find(id=UPCARD)
        return str(el) if el else None
    except Exception:
        return None


def _capture_provas(page, settle_ms, timeout_ms, max_days=31):
    """Captura a grade de PROVAS: um SNAPSHOT do upCard por dia EXPANDIDO.

    A aba "Lista de Provas" auto-renderiza uma SANFONA por dia (colapsada). Cada
    dia é <p class="grid_accordion" onclick="toggleCard(this)"> + <input type=submit
    class="hide btnDiaProva"> OCULTO. Clicar o <p> dispara um postback FULL que
    recarrega a página com as provas DAQUELE dia inline (lazy-load 2º nível).
    PROVA (run 26766321647): ler upCard.innerHTML no meio dá "Execution context was
    destroyed" → todos os reads voltaram None e provas_days ficou VAZIO. A captura
    ROBUSTA é page.content() DEPOIS que a navegação assenta (content() auto-espera o
    documento, não corre com a navegação). Extraímos o upCard de cada page p/ compactar.

    ViewState off → cada postback traz só o dia clicado expandido; acumulamos um
    snapshot do upCard por dia → a UNIÃO tem TODAS as provas. NUNCA lança.

    Devolve (final_page_html, [upcard_html_por_dia], [diag_por_dia]).
    """
    diag = []
    _safe(lambda: page.click(f"#{BTN_PROVAS}", timeout=timeout_ms))
    _safe(lambda: page.wait_for_selector(DAY_HEADER_SEL, timeout=timeout_ms))
    n = _safe(lambda: page.locator(DAY_HEADER_SEL).count(), default=0) or 0
    settle = max(settle_ms, 3500)  # folga generosa: o postback do dia é FULL
    snaps = []
    for i in range(min(n, max_days)):
        url_before = _safe(lambda: page.url)
        # rótulo do dia (diagnóstico) ANTES do clique
        label = _safe(lambda i=i: page.locator(DAY_HEADER_SEL).nth(i)
                      .locator("span.gridCol").inner_text(timeout=timeout_ms))
        # clica o HEADER do dia i (re-consulta SEMPRE: a navegação FULL recria os <p>)
        _safe(lambda i=i: page.locator(DAY_HEADER_SEL).nth(i).click(timeout=timeout_ms))
        # assenta a navegação FULL: load + rede ociosa + folga fixa generosa
        _safe(lambda: page.wait_for_load_state("load", timeout=timeout_ms))
        _safe(lambda: page.wait_for_load_state("networkidle", timeout=timeout_ms))
        _safe(lambda: page.wait_for_timeout(settle))
        page_html = _safe(lambda: page.content())
        upc = _extract_upcard(page_html)
        url_after = _safe(lambda: page.url)
        diag.append({"i": i, "label": label, "url_before": url_before,
                     "url_after": url_after, "page_len": len(page_html or ""),
                     "upcard_len": len(upc or "")})
        snap = upc or page_html
        if snap and snap not in snaps:
            snaps.append(snap)
    final = _safe(lambda: page.content())
    return final, snaps, diag


def fetch_detail(source, id_nativo, headless=True, settle_ms=1200, timeout_ms=15000):
    """
    Captura o DETALHE de um torneio (ListaProvas?ID=N) com navegador.

    Devolve dict:
      header_html      : HTML do GET (cabeçalho estático: título etc.)
      provas_html      : innerHTML do upCard ao FINAL (último dia expandido) — ou None
      provas_days      : LISTA de snapshots do upCard (extraídos do page.content()),
                         um por dia expandido (a união contém TODAS as provas mesmo
                         se o servidor recolhe os outros dias — ver _capture_provas)
      provas_diag      : LISTA de diagnóstico por dia (i, label, url antes/depois,
                         page_len, upcard_len) — pra ver no CI se a expansão pegou
      provas_page_html : HTML da PÁGINA INTEIRA após capturar provas (diagnóstico)
      docs_html        : innerHTML do upCard após clicar "Programas/Adendos" (em
                         geral VAZIO — o postback é FULL; ver docs_page_html)
      docs_page_html   : HTML da PÁGINA INTEIRA após a aba de docs — é AQUI que os
                         PDFs (Programa/Adendos) realmente moram (parse_documentos)

    MODELO CONFIRMADO (recon 2026-06 + dump CI 3436, run 26755256049):
      • Aba "Lista de Provas" AUTO-renderiza a sanfona por dia (colapsada). Cada
        dia é <p class="grid_accordion" onclick="toggleCard(this)"> + submit OCULTO;
        clicar o <p> dispara o 2º postback que carrega as provas do dia.
      • Aba "Programas/Adendo": postback FULL → upCard vazio; os PDFs vêm no
        page.content() inteiro (div.programa/div.adendo > ul > li > a[href$=.pdf]).
      • Fantasma (dono != FPH): upCard "Loading..." + sem aba Programas → nada a
        extrair (regra de ouro: só a fonte dona).

    RESILIÊNCIA (run 26754246983): cada captura é isolada (_safe) e há fallback de
      page inteira — uma falha não aborta o dump nem perde o que já foi capturado.
    """
    from playwright.sync_api import sync_playwright

    url = source["detalhe_url"].format(id=id_nativo)
    result = {"header_html": None, "provas_html": None, "provas_days": [],
              "provas_diag": [], "provas_page_html": None,
              "docs_html": None, "docs_page_html": None}
    with sync_playwright() as pw:
        browser, ctx, page = _new_page(pw, headless)
        try:
            _safe(lambda: page.goto(url, wait_until="domcontentloaded"))
            _safe(lambda: page.wait_for_timeout(settle_ms))
            result["header_html"] = _safe(lambda: page.content())
            # provas: expande CADA dia da sanfona e acumula os snapshots do upCard
            final_page, days, diag = _safe(
                lambda: _capture_provas(page, settle_ms, timeout_ms), default=(None, [], []))
            result["provas_days"] = days
            result["provas_diag"] = diag
            result["provas_html"] = _safe(lambda: _read_upcard_html(page, timeout_ms))
            result["provas_page_html"] = final_page or _safe(lambda: page.content())
            # recarrega p/ estado limpo: o postback troca o upCard no lugar
            _safe(lambda: page.goto(url, wait_until="domcontentloaded"))
            _safe(lambda: page.wait_for_timeout(settle_ms))
            result["docs_html"] = _safe(
                lambda: _capture_upcard(page, BTN_PROGRAMAS, settle_ms, timeout_ms))
            result["docs_page_html"] = _safe(lambda: page.content())
        finally:
            _safe(lambda: browser.close())
    return result
