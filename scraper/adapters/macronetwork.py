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
# "PR. 01 - ..." → número da prova
_PR_NUM = re.compile(r"\bPR\.?\s*(\d+)", re.IGNORECASE)


def _id_from_query(href):
    """ID nativo da query: 'Resultados.aspx?ID=14017' → '14017'. None se não casar."""
    if not href:
        return None
    m = re.search(r"[?&]ID=(\d+)", href, re.IGNORECASE)
    return m.group(1) if m else None


def _clean(s):
    """Colapsa espaços/quebras e tira as bordas. '-' e '' viram None (a fonte usa
    '-' como 'vazio' em equipe/categoria). Preserva o conteúdo real."""
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return None if s in ("", "-") else s


def _so_digitos(s):
    """'1 °' / '10º' → 10 (int). None se não houver dígito."""
    if not s:
        return None
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


def _dedup_segmentos(texto):
    """'1,30M - JCT - 1,30M - JCT' → '1,30M - JCT' (tira repetição, preserva ordem)."""
    if not texto:
        return None
    segs, vistos = [], set()
    for s in (p.strip() for p in texto.split(" - ")):
        k = s.lower()
        if s and k not in vistos:
            vistos.add(k)
            segs.append(s)
    return " - ".join(segs) or None


def parse_provas(upcard_html, base_url=None):
    """Extrai as PROVAS do upCard (sanfona por dia, TODOS os dias já expandidos).

    DOM real (dump CI 3436, run 26768218275):
      <div class="card ml-0" id="divPrincipal">            ← um por DIA
        <p class="grid_accordion">
          <span class="gridCol">quinta-feira - 28/mai/2026</span>   ← data do dia (c/ ANO)
        <div id="...ctlNN_upInfoCard">                     ← provas do dia (só se expandido)
          <div class="info_card">                          ← uma por PROVA
            <span class="horario_prova">11:30</span>
            <span id="...litNomeProva">PR. 01 - 1,30M - JCT - 1,30M - JCT</span>
            <span id="...litTipoProva">CRONÔMETRO</span>
            <span id="...lblLocalProva">Pista de GRAMA</span>
            <a class="btn_resultado_entrada" href="Resultados.aspx?ID=14017">  ← id_origem!
            <a class="btn_ordem_entrada"     href="OrdemEntrada.aspx?ID=14017">

    O ID de Resultados.aspx?ID=N é a CHAVE NATIVA da prova (id_origem) — estável,
    permite upsert por (torneio_id, id_origem) SEM orfanar resultados (FK→provas.id),
    e é a própria URL pra raspar os resultados depois (Fase C). data_prova já vem
    amarrada (a gridCol traz o ano). Tolerante: dia sem header/linha é ignorado.
    Devolve lista de dicts, uma por prova.
    """
    from urllib.parse import urljoin
    soup = _soup(upcard_html)
    out = []
    for card in soup.find_all("div", class_="card"):
        header = card.find("p", class_="grid_accordion")
        if not header:                       # só os cards de DIA têm header de sanfona
            continue
        gc = header.find("span", class_="gridCol")
        m = _DIA_GRID.search(gc.get_text(" ", strip=True) if gc else "")
        if not m:
            continue
        dia_semana = m.group(1).lower()
        dd, mon_abbr, yyyy = int(m.group(2)), m.group(3).lower(), int(m.group(4))
        mes = PT_MONTHS.get(mon_abbr[:3])
        try:
            data_prova = _dt.date(yyyy, mes, dd).isoformat() if mes else None
        except ValueError:
            data_prova = None
        for ic in card.find_all("div", class_="info_card"):
            nome_el = ic.select_one("span[id$=litNomeProva]")
            nome = nome_el.get_text(" ", strip=True) if nome_el else None
            if not nome:
                continue
            a = (ic.select_one("a.btn_resultado_entrada[href]")
                 or ic.select_one("a.btn_ordem_entrada[href]"))
            href = a.get("href") if a else None
            tipo_el = ic.select_one("span[id$=litTipoProva]")
            local_el = ic.select_one("span[id$=lblLocalProva]")
            hora_el = ic.select_one("span.horario_prova")
            num_m = _PR_NUM.search(nome)
            resto = re.sub(r"^\s*PR\.?\s*\d+\s*-\s*", "", nome, flags=re.IGNORECASE)
            out.append({
                "id_origem": _id_from_query(href),
                "numero": num_m.group(1) if num_m else None,
                "nome": nome,
                "categorias": _dedup_segmentos(resto),
                "tipo_prova": tipo_el.get_text(strip=True) if tipo_el else None,
                "data_prova": data_prova,
                "dia_semana": dia_semana,
                "horario": hora_el.get_text(strip=True) if hora_el else None,
                "local": local_el.get_text(strip=True) if local_el else None,
                "resultado_url": urljoin(base_url, href) if (base_url and href) else href,
            })
    return out


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


# ── RESULTADOS (Resultados.aspx?ID=N) — Fase C ────────────────────────
# DESCOBERTAS DA RECON AO VIVO (FPH 2026-06; PR03 do 3436 = id_origem 14009):
#   • A página RENDERIZA NO GET SIMPLES (sem postback/Playwright!) — ao contrário
#     da sanfona de provas. ViewState vem VAZIO (a FPH o desativou). Capturável
#     com requests → fixture local fph_resultados_14009.html.
#   • Tipo da prova: div.tipo-prova → "Tipo de Prova: CRONÔMETRO".
#   • UMA <table>. As linhas REAIS de resultado são <tr class="table-row-styling">;
#     entre elas há <tr class="detalhe-conjunto hide"> (só um <hr/> escondido —
#     IGNORAR). Cada linha de resultado:
#       td.classfic-data[id=499150] > b   → COLOCAÇÃO ("1º"); o id é a CHAVE NATIVA
#                                            do resultado na fonte (id_origem p/ upsert)
#       td.colunaCavaleiro .format-coluna-competidor strong → CAVALEIRO
#         + span.descCompetidor                              → ENTIDADE/clube
#         + input[id$=hfIDCavaleiro][value]                  → id do cavaleiro na fonte
#       td.colunaCavalo strong            → CAVALO
#         + span.descCavalo               → genealogia (Nasc | Sexo | UF | Criador | ...)
#       td.border-mobile-data (texto)     → CATEGORIA ("JCA")
#       td.is-desktop                     → EQUIPE ("SHRP" ou "-")
#       b.falta-soma-color                → PENALIDADE/faltas ("0"/"4"/"16")
#         OU td.error                     → status ("Eliminado") quando sem faltas
#       td c/ img.icon-tempo > span       → TEMPO ("63,13"); ausente p/ eliminado
#   • Cruza 1:1 com a Ordem de Entrada (mesmos 18 conjuntos cavaleiro+cavalo).
#   ⚠ Layout confirmado p/ CRONÔMETRO. Outros tipos (Duas Fases/Desempate) têm
#     colunas extras de falta/tempo — endereçar com fixtures próprias antes de
#     escrever no banco (ver task #14).

def parse_resultados(html, base_url=None):
    """Extrai os RESULTADOS de uma prova (Resultados.aspx?ID=N), já renderizada
    no GET. Devolve lista de dicts (uma por conjunto cavaleiro+cavalo), na ordem
    de classificação. Campos:
      id_origem          id do resultado na fonte (td.classfic-data[id]) — chave upsert
      colocacao          "1º"… (formato que resultados.html/RPCs esperam)
      cavaleiro_nome     nome do competidor
      entidade           clube/entidade do competidor
      id_cavaleiro_fonte id do cavaleiro na fonte (hfIDCavaleiro) — futuro cruzamento
      cavalo_nome        nome do cavalo
      cavalo_genealogia  "Nasc | Sexo | UF | Criador | …" (texto colapsado)
      categoria          ex.: "JCA"
      equipe             ex.: "SHRP" (ou None)
      penalidade         "0"/"4"/… ou "Eliminado" (status quando sem faltas)
      tempo              "63,13" (vírgula decimal BR) ou None
    Tolerante: célula/linha faltando não quebra (campos viram None).
    """
    soup = _soup(html)
    out = []
    for r in soup.select("tr.table-row-styling"):
        cl = r.select_one("td.classfic-data")
        coloc = _clean(cl.find("b").get_text() if cl and cl.find("b") else None)
        rid = cl.get("id") if cl else None

        cav_el = (r.select_one("td.colunaCavaleiro .format-coluna-competidor strong")
                  or r.select_one("td.colunaCavaleiro strong"))
        ent_el = r.select_one("span.descCompetidor")
        idc_el = r.select_one("td.colunaCavaleiro input[id$=hfIDCavaleiro]")

        cavalo_el = r.select_one("td.colunaCavalo strong")
        gen_el = r.select_one("td.colunaCavalo span.descCavalo")

        cat_el = r.select_one("td.border-mobile-data")
        categoria = None
        if cat_el:
            direto = cat_el.find(string=True, recursive=False)  # texto antes do span mobile
            categoria = _clean(direto) or _clean(cat_el.get_text(" ", strip=True))
        eq_el = r.select_one("td.is-desktop")

        # penalidade: a soma de faltas; se eliminado, o status (td.error)
        falta_el = r.select_one("b.falta-soma-color") or r.select_one("td.error")
        # tempo: a td que tem o ícone de relógio (ausente p/ eliminado)
        tempo = None
        tempo_td = r.select_one("td:has(img.icon-tempo)")
        if tempo_td is None:  # :has pode não existir no html.parser — fallback manual
            for td in r.find_all("td", recursive=False):
                if td.find("img", class_="icon-tempo"):
                    tempo_td = td
                    break
        if tempo_td is not None:
            sp = tempo_td.find("span")
            tempo = _clean(sp.get_text() if sp else None)

        out.append({
            "id_origem": rid,
            "colocacao": coloc,
            "cavaleiro_nome": _clean(cav_el.get_text() if cav_el else None),
            "entidade": _clean(ent_el.get_text() if ent_el else None),
            "id_cavaleiro_fonte": (idc_el.get("value") if idc_el else None) or None,
            "cavalo_nome": _clean(cavalo_el.get_text() if cavalo_el else None),
            "cavalo_genealogia": _clean(gen_el.get_text(" ") if gen_el else None),
            "categoria": categoria,
            "equipe": _clean(eq_el.get_text() if eq_el else None),
            "penalidade": _clean(falta_el.get_text() if falta_el else None),
            "tempo": tempo,
        })
    return out


def parse_prova_tipo(html):
    """Lê 'Tipo de Prova: CRONÔMETRO' do cabeçalho (div.tipo-prova). Serve de GUARDA:
    layouts de resultado mudam por tipo. Devolve a string crua do tipo ou None."""
    soup = _soup(html)
    el = soup.select_one("div.tipo-prova")
    if not el:
        return None
    t = el.get_text(" ", strip=True)
    m = re.search(r"tipo de prova\s*:?\s*(.+)$", t, re.IGNORECASE)
    return _clean(m.group(1)) if m else _clean(t)


# ── ORDEM DE ENTRADA (OrdemEntrada.aspx?ID=N) — Fase C ────────────────
# DESCOBERTAS DA RECON AO VIVO (FPH 2026-06; PR03 do 3436 = id_origem 14009):
#   • Também RENDERIZA NO GET SIMPLES (sem Playwright). ViewState vazio.
#   • UMA <table>. As linhas de ordem são <tr> com td.ordem-font-classific;
#     separadas por <tr class="detalhe-conjunto hide"> (hr escondido — IGNORAR).
#     Cada linha:
#       td.ordem-font-classific > b                 → ORDEM ("1 °") → int 1
#       td.colunaCavaleiro .orderm-coluna-cavaleiro strong → CAVALEIRO
#         + input[id$=hfIDCavaleiro][value]         → id do cavaleiro na fonte
#       td.colunaCavalo strong                      → CAVALO
#         + resto do div                            → genealogia
#       td.align_center > b (1ª)                    → CATEGORIA ("JCA")
#       última td > strong                          → PONTUAÇÃO/ranking ("19")
#   • A ordem de entrada SAI no início do dia e PODE ser corrigida (re-publicada);
#     a chave estável por linha é (prova, cavaleiro+cavalo). Posições podem ter
#     gaps (conjunto retirado) — normal.

def parse_ordem_entrada(html, base_url=None):
    """Extrai a ORDEM DE ENTRADA de uma prova (OrdemEntrada.aspx?ID=N), no GET.
    Devolve lista de dicts (uma por conjunto), na ordem publicada. Campos:
      ordem              posição de entrada (int) — pode ter gaps
      cavaleiro_nome     competidor
      id_cavaleiro_fonte id do cavaleiro na fonte (hfIDCavaleiro)
      cavalo_nome        cavalo
      cavalo_genealogia  texto da genealogia (colapsado)
      categoria          ex.: "JCA"
      pontuacao          pontos de ranking exibidos ("19") ou None
    Tolerante a célula/linha faltando.
    """
    soup = _soup(html)
    tbl = soup.find("table")
    if not tbl:
        return []
    out = []
    for r in tbl.find_all("tr"):
        oc = r.select_one("td.ordem-font-classific")
        if oc is None:               # só as linhas de ordem têm essa td
            continue
        ordem = _so_digitos(oc.find("b").get_text() if oc.find("b") else oc.get_text())

        cav_el = (r.select_one("td.colunaCavaleiro .orderm-coluna-cavaleiro strong")
                  or r.select_one("td.colunaCavaleiro strong"))
        idc_el = r.select_one("td.colunaCavaleiro input[id$=hfIDCavaleiro]")
        cavalo_el = r.select_one("td.colunaCavalo strong")

        gen = None
        gd = r.select_one("td.colunaCavalo div") or r.select_one("td.colunaCavalo")
        if gd:
            # genealogia = texto do bloco MENOS o nome do cavalo (o strong)
            full = _clean(gd.get_text(" "))
            nome = _clean(cavalo_el.get_text() if cavalo_el else None)
            if full and nome and full.upper().startswith(nome.upper()):
                gen = _clean(full[len(nome):])
            else:
                gen = full

        # categoria = <b> de uma td.align_center que NÃO é a da ordem
        # (a td.ordem-font-classific também tem a classe align_center → casaria 1º)
        cat_el = None
        for td in r.select("td.align_center"):
            if "ordem-font-classific" in (td.get("class") or []):
                continue
            b = td.find("b")
            if b and _clean(b.get_text()):
                cat_el = b
                break
        tds = r.find_all("td")
        pont_el = tds[-1].find("strong") if tds else None

        out.append({
            "ordem": ordem,
            "cavaleiro_nome": _clean(cav_el.get_text() if cav_el else None),
            "id_cavaleiro_fonte": (idc_el.get("value") if idc_el else None) or None,
            "cavalo_nome": _clean(cavalo_el.get_text() if cavalo_el else None),
            "cavalo_genealogia": gen,
            "categoria": _clean(cat_el.get_text() if cat_el else None),
            "pontuacao": _clean(pont_el.get_text() if pont_el else None),
        })
    return out
