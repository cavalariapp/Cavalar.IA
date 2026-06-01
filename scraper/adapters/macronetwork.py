"""
Adapter MacroNetwork (ASP.NET WebForms) — a plataforma DOMINANTE.
Usada por FPH, FEERJ, FHIMT e como backend de inscrição de vários clubes.

Divisão proposital:
  • PARSING (este módulo, funções puras) — recebe HTML, devolve dados.
    Testável localmente sem rede e sem navegador.
  • FETCHING (fetch.py) — navega o site. O calendário MacroNetwork guarda
    o mês/ano em estado de SESSÃO no servidor (ViewState desativado, params
    de GET ignorados, postback opaco), então a navegação entre meses exige
    um navegador real (Playwright). O parsing abaixo é agnóstico de COMO o
    HTML chegou.

Descobertas da recon ao vivo (FPH, 2026-05):
  • Card: div.div_conteudo_torneio_desktop (sem variante mobile duplicada).
  • Detalhe: a[href*='ListaProvas'] → ID estável do torneio na fonte.
  • Organizador: img src '.../uploads/pessoa/{ENTIDADE_ID}/...' — o ID da
    ENTIDADE que organiza. Sinal-chave de FANTASMA: se a federação lista um
    evento cujo organizador é outra entidade/UF (ex.: FPH listando "SINOP"/MT).
  • Disciplina: primeiro <span> dentro de div.data_torneio_desktop (ex.: SALTO).
  • Data: texto solto APÓS o <svg>, SEM ANO (ex.: "01/ mai a 03/ mai").
    Eventos de 1 dia trazem só o número ("09") → mês vem do filtro (ddlMes).
    => RAIZ dos 179 sem-data: o ano só existe no dropdown ddlAno, nunca no card.
"""

import re
import datetime as _dt

# ── meses PT → número ────────────────────────────────────────────────
PT_MONTHS = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}

# (dia)/(mes-abrev) — tolera espaços: "01/ mai", "03 / mai"
_DAY_MON = re.compile(r"(\d{1,2})\s*/\s*([a-zç]{3,4})", re.IGNORECASE)
# dia "solto" (evento de 1 dia, sem mês): a string é só dígitos
_DAY_ONLY = re.compile(r"^\s*(\d{1,2})\s*$")


def _soup(html):
    """BeautifulSoup com lxml se houver; senão html.parser (stdlib)."""
    from bs4 import BeautifulSoup
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def read_selected_month(html_or_soup):
    """Mês atualmente selecionado no ddlMes (1..12) ou None ('Todos'=0→None)."""
    soup = _soup(html_or_soup) if isinstance(html_or_soup, str) else html_or_soup
    sel = soup.find("select", id="ctl00_ContentPlaceHolder1_ddlMes")
    if not sel:
        return None
    opt = sel.find("option", selected=True)
    if not opt:
        return None
    try:
        v = int(opt.get("value"))
        return v if 1 <= v <= 12 else None
    except (TypeError, ValueError):
        return None


def read_pager_pages(html_or_soup):
    """Lista de números de página do pager (ex.: ['1','2'])."""
    soup = _soup(html_or_soup) if isinstance(html_or_soup, str) else html_or_soup
    return [b.get("value") for b in soup.select("input[name*='btnPageNumber']")]


def parse_date_range(text, year, fallback_month=None):
    """
    Converte o texto cru do card (SEM ano) em (data_inicio, data_fim) ISO.
    `year` vem do ddlAno (amarrado pelo fetcher). `fallback_month` é o mês do
    filtro (ddlMes), usado quando o card traz só o dia ("09").
    Trata virada de ano dez→jan (se mês_fim < mês_inicio, ano_fim = year+1).
    Devolve (None, None) se não der pra extrair.
    """
    if not text:
        return None, None
    text = text.strip().lower()

    pairs = _DAY_MON.findall(text)  # [(dia, mes_abrev), ...]
    if pairs:
        parsed = []
        for d, mon in pairs:
            m = PT_MONTHS.get(mon[:3])
            if not m:
                continue
            parsed.append((int(d), m))
        if not parsed:
            return None, None
        d0, m0 = parsed[0]
        d1, m1 = parsed[-1]
        y0 = year
        y1 = year + 1 if m1 < m0 else year  # virada dez→jan
        try:
            ini = _dt.date(y0, m0, d0).isoformat()
            fim = _dt.date(y1, m1, d1).isoformat()
        except ValueError:
            return None, None
        return ini, fim

    # sem mês no texto → evento de 1 dia, mês vem do filtro
    only = _DAY_ONLY.match(text)
    if only and fallback_month:
        try:
            d = _dt.date(year, fallback_month, int(only.group(1))).isoformat()
            return d, d
        except ValueError:
            return None, None

    return None, None


def _entity_id(src):
    if not src:
        return None
    m = re.search(r"/pessoa/(\d+)/", src)
    return m.group(1) if m else None


def _detail_id(href):
    if not href:
        return None
    m = re.search(r"[?&]ID=(\d+)", href, re.IGNORECASE)
    return m.group(1) if m else None


def parse_calendar(html, year, base_url=None):
    """
    Extrai os eventos de UMA página de calendário MacroNetwork já renderizada.
    Retorna lista de dicts:
      id_nativo            ID do torneio na fonte (estável)  -> chave de dedup
      detail_url           URL absoluta da página de provas
      nome                 título do card
      disciplina           ex.: SALTO, VOLTEIO
      data_inicio/data_fim ISO (ano amarrado do ddlAno)
      organizador_entidade ID da entidade organizadora (sinal de fantasma)
      status               ex.: "Torneio Concluído"
      data_texto_cru       texto original (auditoria)
    """
    soup = _soup(html)
    fallback_month = read_selected_month(soup)
    out = []
    for card in soup.select("div.div_conteudo_torneio_desktop"):
        a = card.select_one("a[href*='ListaProvas']")
        href = a.get("href") if a else None
        id_nativo = _detail_id(href)

        h4 = card.select_one("h4.desc-torneio_desktop")
        nome = h4.get_text(strip=True) if h4 else None

        img = card.select_one("img[src*='pessoa/']")
        organizador = _entity_id(img.get("src")) if img else None

        datebox = card.select_one("div.data_torneio_desktop")
        disciplina = None
        data_texto = ""
        if datebox:
            sp = datebox.select_one("span")
            disciplina = sp.get_text(strip=True) if sp else None
            inner = datebox.select_one("div")
            if inner:
                data_texto = inner.get_text(" ", strip=True)
        ini, fim = parse_date_range(data_texto, year, fallback_month)

        lbl = card.select_one("span.label")
        status = lbl.get_text(strip=True) if lbl else None

        detail_url = href
        if base_url and href:
            from urllib.parse import urljoin
            detail_url = urljoin(base_url, href)

        out.append({
            "id_nativo": id_nativo,
            "detail_url": detail_url,
            "nome": nome,
            "disciplina": disciplina,
            "data_inicio": ini,
            "data_fim": fim,
            "organizador_entidade": organizador,
            "status": status,
            "data_texto_cru": data_texto,
        })
    return out


# ── DETALHE (ListaProvas?ID=N) — Fase B ───────────────────────────────
# DESCOBERTAS DA RECON AO VIVO (FPH 2026-06; ID=3316/3436 dono, 3440 fantasma):
#   • A página de detalhe tem um CABEÇALHO ESTÁTICO (já no GET, sem JS):
#       - título   → div.titulo-data > h2
#       - endereço → span#ctl00_lblEndereco — é o endereço da FEDERAÇÃO/fonte
#         (rodapé de contato), NÃO o local do evento. Não usar como "local".
#   • MENU de 5 ações, todas <a class="card_menu"> com href
#       javascript:__doPostBack('ctl00$ContentPlaceHolder1$mpMenuNovo$<btn>',''):
#         btnListaProva → grade de PROVAS        btnProgramas → PROGRAMAS/ADENDOS (PDFs)
#         btnListInsc   → lista de inscritos     btnMapaLocal → mapa
#         hlInscricao   → link de inscrição
#   • O conteúdo renderiza via AJAX (PageRequestManager) DENTRO do UpdatePanel
#     #ctl00_ContentPlaceHolder1_upCard — NÃO nos painéis pn* (só o RÓTULO).
#   • LAZY-LOAD EM DOIS NÍVEIS (confirmado no dump CI 3436, run 26755256049):
#       1º nível — ao abrir a página, a aba "Lista de Provas" AUTO-renderiza no
#         upCard uma SANFONA por dia do torneio (ex.: QUI 28 / SEX 29 / SÁB 30 /
#         DOM 31), com cada dia COLAPSADO. Cada dia é:
#           <p class="lstProva accordion grid_accordion" onclick="toggleCard(this)">
#             <span class="gridCol">quinta-feira - 28/mai/2026</span>   ← rótulo+data
#             <input type=hidden id=...hfdData value="quinta-feira, 28 de maio de 2026">
#             <img class="load hide" alt="Carregando">                  ← spinner
#             <input type=submit class="hide btnDiaProva">              ← postback OCULTO
#       2º nível — clicar o <p> (NÃO o submit, que é display:none) dispara o 2º
#         postback que carrega as LINHAS de prova daquele dia (não estão no DOM
#         até expandir).
#   • DOCS (Programa/Adendo): a aba "Programas/Adendo" faz postback FULL → o
#     upCard fica VAZIO; os PDFs moram na PÁGINA INTEIRA, em
#       div.programa/div.adendo (class "...box") > ul.nostyle > li >
#         a[href$=.pdf] > span.data (DD/MM/AAAA) + span.title (rótulo).
#   • SINAL DE FANTASMA (regra de ouro): no 3440 (dono = CBH, exibido na FPH) o
#     upCard ficou só "Loading..." (sem sanfona) e a aba "Programas/Adendo"
#     NEM EXISTIA. Dono FPH (3436) tinha as duas. Ausência de grade/Programas
#     ⇒ a FPH não é dona ⇒ não extrair docs/resultados dela.
#   • Postback NÃO se reproduz por requests (loop de pageRedirect) E nem por
#     clique sintético (MCP travou no "Carregando"): exige navegador REAL
#     (Playwright, CI 3.12), que dispara o __doPostBack de verdade. Captura via
#     fetch.fetch_detail / `python -m scraper.main --dump-detail <ID>`.
#   ⚠ parse_documentos: PRONTO (fixture fph_docs_3436.html). parse_provas:
#     aguardando o dump das LINHAS por dia (re-captura com o header certo).

def parse_detalhe_header(html):
    """Cabeçalho ESTÁTICO da página de detalhe (vem no GET, sem AJAX).
    Devolve {'titulo': str|None}. Serve pra CONFIRMAR identidade (resolver) e
    cruzar com o card do calendário. O endereço do rodapé é da federação, não
    do evento, então não é extraído aqui."""
    soup = _soup(html)
    bloco = soup.select_one("div.titulo-data h2") or soup.find("h2")
    titulo = bloco.get_text(strip=True) if bloco else None
    return {"titulo": titulo}


def _br_date_to_iso(text):
    """'12/05/2026' → '2026-05-12'. Devolve None se não casar/for inválida."""
    if not text:
        return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not m:
        return None
    d, mo, y = (int(x) for x in m.groups())
    try:
        return _dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


# cabeçalho de dia da sanfona: "quinta-feira - 28/mai/2026"
_DIA_GRID = re.compile(
    r"([a-zç\-]+feira|s[áa]bado|domingo)\s*-\s*(\d{1,2})/([a-zç]{3,4})/(\d{4})",
    re.IGNORECASE,
)


def parse_provas(upcard_html):
    """⚠ Fase B — parser das LINHAS aguardando o dump por dia (re-captura com o
    header certo, run após f3dbe3d). O upCard é uma SANFONA por dia; o cabeçalho
    de cada dia já é parseável agora:
        <span class="gridCol">quinta-feira - 28/mai/2026</span>
    → (dia_semana, data_prova ISO) via _DIA_GRID. As LINHAS de prova só entram no
    DOM ao expandir o dia (2º postback) — fetch._capture_provas captura um snapshot
    por dia (provas_days). Quando a fixture com linhas chegar, extrair
    {data_prova, dia_semana, numero, nome, categorias, tipo_prova} (colunas reais
    de `provas`) e travar o teste. Gravação: db.replace_provas (a chave de dedup
    — id_origem por prova? — depende do DOM da linha, ainda não visto)."""
    raise NotImplementedError(
        "Fase B: aguardando o dump das LINHAS por dia (provas_dayN do "
        "`--dump-detail 3436` após o fix do header) — não inventar o DOM da linha."
    )


def parse_documentos(html, base_url=None):
    """Extrai os PDFs de Programa/Adendos da PÁGINA INTEIRA do detalhe (após a aba
    "Programas/Adendo": o postback é FULL, o upCard fica vazio e os arquivos vêm
    no page.content() = docs_page_html). Estrutura real (dump CI 3436):
        <div class="programa relative box"><h2>Arquivos relacionados</h2>
          <ul class="nostyle"><li>
            <a href="....pdf" title="PROGRAMA">
              <span class="data">12/05/2026</span><span class="title">PROGRAMA</span>
            </a></li></ul></div>
        <div class="adendo relative box"><h2>Adendos</h2> ...idem... </div>
    `tipo` vem da classe da box (programa/adendo); o "quadro de horários" entra
    como adendo. Só emite de box que TEM PDF (ignora outras .box da página).
    Devolve [{tipo, titulo, url_pdf, data_publicacao}] — colunas reais de
    torneio_documentos (torneio_id/timestamps são preenchidos no writer)."""
    from urllib.parse import urljoin
    soup = _soup(html)
    out, seen = [], set()
    for box in soup.select("div.box"):
        classes = box.get("class") or []
        # tipo = 1ª classe que não é decoração de layout (programa/adendo/...)
        tipo = next((c for c in classes
                     if c not in ("relative", "box", "row-fluid")), None)
        for a in box.select("a[href]"):
            href = a.get("href") or ""
            if ".pdf" not in href.lower():
                continue
            url_pdf = href if href.lower().startswith("http") else (
                urljoin(base_url, href) if base_url else href)
            if url_pdf in seen:
                continue
            seen.add(url_pdf)
            sp_title = a.select_one("span.title")
            sp_data = a.select_one("span.data")
            titulo = (sp_title.get_text(strip=True) if sp_title
                      else (a.get("title") or url_pdf.rsplit("/", 1)[-1]))
            out.append({
                "tipo": tipo,
                "titulo": titulo,
                "url_pdf": url_pdf,
                "data_publicacao": _br_date_to_iso(
                    sp_data.get_text(strip=True) if sp_data else None),
            })
    return out
