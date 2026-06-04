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
#   ⚠ Este bloco cobre CRONÔMETRO single-round. Tipos de DUAS voltas têm parser
#     próprio, p/ os quais parse_resultados desvia (ver dispatch logo abaixo):
#       "2 PERCURSOS IDENTICOS/DISTINTOS" → _parse_resultados_dois_percursos
#       "DUAS FASES"                      → _parse_resultados_duas_fases
#       "...COM UM DESEMPATE"             → _parse_resultados_desempate

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

    # GUARDA DE LAYOUT: tipos de prova com DUAS voltas (faltas+tempo por volta) NÃO
    # usam tr.table-row-styling e têm parser próprio. Detecta pelo cabeçalho
    # (div.tipo-prova) ou pelo container lv* e desvia. CRONÔMETRO single-round (e
    # demais tipos) seguem no fluxo padrão abaixo.
    tp_el = soup.select_one("div.tipo-prova")
    tp_txt = (tp_el.get_text(" ", strip=True) if tp_el else "") or ""
    tp_up = tp_txt.upper()
    # DUAS VOLTAS = duas voltas pontuáveis, MESMA estrutura de DOIS PERCURSOS
    # (faltas+tempo por volta). Confirmado ao vivo (FEHGO 9241, container
    # lvResultadoDuasVolta: 0 -> 24 linhas). Roteia pro parser de dois percursos.
    if ("PERCURSO" in tp_up or "DUAS VOLTAS" in tp_up
            or soup.find(id=re.compile("lvPercursos|lvResultadoDuasVolta", re.I))):
        return _parse_resultados_dois_percursos(soup)
    if "DUAS FASES" in tp_up or soup.find(id=re.compile("lvResultadoDuasFases", re.I)):
        return _parse_resultados_duas_fases(soup)
    # "COM ... DESEMPATE" (com jump-off) → parser de desempate. ATENÇÃO: "S/DESEMPATE"
    # e "SEM DESEMPATE" são o OPOSTO (sem jump-off) e NÃO podem cair aqui — vão pro
    # parser geral (que lê faltas/tempo nas células align_center, com o fallback).
    if (("DESEMPATE" in tp_up and not re.search(r"S/\s*DESEMPATE|SEM\s+DESEMPATE", tp_up))
            or soup.find(id=re.compile("lvResultadoDesempate", re.I))):
        return _parse_resultados_desempate(soup)
    # TEMPO OCULTO = TEMPO IDEAL na exibição (só não revela o tempo antes do fim
    # da prova); mesma estrutura de resultado → mesmo parser. Confirmado ao vivo
    # (FPH oid 12281/13710/13773: header "TEMPO OCULTO", classif. por aproximação;
    # o container NÃO se chama lvResultadoTempoOculto, então roteia pelo texto).
    if ("TEMPO IDEAL" in tp_up or "TEMPO OCULTO" in tp_up
            or soup.find(id=re.compile(r"lvResultadoTempo(Ideal|Oculto)", re.I))):
        return _parse_resultados_tempo_ideal(soup)

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

        # penalidade (faltas): b.falta-soma-color / td.error (eliminado)
        falta_el = r.select_one("b.falta-soma-color") or r.select_one("td.error")
        penalidade = _clean(falta_el.get_text() if falta_el else None)
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

        # FALLBACK (layout S/CRONÔMETRO e afins): penalidade/tempo vêm em
        # td.align_center SIMPLES (sem b.falta-soma-color nem ícone de relógio),
        # nas últimas células — ex.: "0 (0+0)" (faltas) e "78,54" (tempo). Sem isto
        # essas provas apareciam com colocação mas SEM tempo/faltas.
        if penalidade is None or tempo is None:
            vals = []
            for td in r.find_all("td"):
                cls = " ".join(td.get("class") or [])
                if "align_center" not in cls or "classfic-data" in cls or "border-mobile-data" in cls:
                    continue
                if td.find("strong"):   # pula células de nome (cavaleiro/cavalo)
                    continue
                t = _clean(td.get_text(" ", strip=True))
                if t:
                    vals.append(t)
            if tempo is None:
                tempo = next((t for t in vals if re.fullmatch(r"\d{1,3},\d{1,2}", t)), None)
            if penalidade is None:
                penalidade = next((t for t in vals if t != tempo and (
                    re.match(r"^\d+\b", t) or re.search(r"elim|aband|desclass|retir", t, re.I))), None)

        # normaliza "0 (0+0)" → "0" (o front destaca percurso zerado só em '0' exato)
        if penalidade and "(" in penalidade:
            penalidade = penalidade.split("(")[0].strip()

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
            "penalidade": penalidade,
            "tempo": tempo,
        })
    return out


# ── RESULTADOS de "2 PERCURSOS IDENTICOS / DISTINTOS" (seletivas) ─────
# DESCOBERTAS DA RECON AO VIVO (FPH 2026-06; IDs 13903/13906/13909):
#   • div.tipo-prova = "Tipo de Prova: 2 PERCURSOS IDENTICOS". Container
#     lvPercursosIdenticos (há também lvPercursosDistintos). NÃO usam
#     tr.table-row-styling → as linhas são <tr> com td.classfic-data.
#   • O cavaleiro corre OS DOIS percursos. Cada volta carrega OU (faltas, tempo)
#     OU um STATUS (Eliminado/Desistente/…) numa única célula td.error (sem
#     tempo). O RESULTADO final é o status se houve em QUALQUER volta (prevalece,
#     1ª ou 2ª, tanto faz); senão a soma de faltas. A fonte já calcula o Resultado.
#   • Em DESKTOP (td.is-desktop; os td.is-mobile são duplicatas e o td.btnHidden2Volta
#     final é só botão de vídeo) a sequência de células de valor é:
#       1ª Volta: [faltas (b.falta-soma-color)] [tempo (img.icon-tempo>span)]
#                 — OU 1 célula td.error com o status (sem tempo)
#       2ª Volta: idem
#       Resultado: 1 célula (faltas-soma OU status)
#     Volta com status COLAPSA de 2 células p/ 1 → o parser anda por SEÇÕES
#     (não por índice fixo de coluna).
#   • Campos ocultos úteis (futuro): hfTempoDoisPercursos (tempo somado/desempate),
#     hfResultadoJunto (resultado final), hfHorsConcours (fora de concurso).
#   • Para o site exibir as 4 colunas (Pen 1ª|T 1ª|Pen 2ª|T 2ª) a prova precisa
#     estar com provas.tipo_prova='Duas Voltas' (hoje muitas estão 'Outro').

# ── HELPERS compartilhados pelos parsers de DUAS voltas ──────────────
def _eh_pontuacao(td):
    """True se a célula carrega PONTUAÇÃO: status (td.error), soma de faltas
    (b.falta-soma-color) ou tempo (img.icon-tempo). Distingue de colunas como
    Equipe/Categoria/Prêmio e de placeholders '---'/'-----' (sem marcador)."""
    cls = td.get("class") or []
    return ("error" in cls
            or td.find("b", class_="falta-soma-color") is not None
            or td.find("img", class_="icon-tempo") is not None)


def _falta_de(td):
    """Soma de faltas da célula (texto do b.falta-soma-color, ex.: '0'/'4'/'16';
    senão o texto cru da célula)."""
    b = td.find("b", class_="falta-soma-color")
    return _clean(b.get_text() if b else td.get_text(" ", strip=True))


def _tempo_de(td):
    """Tempo da célula (texto do span junto ao img.icon-tempo; senão texto cru)."""
    sp = td.find("span")
    return _clean(sp.get_text() if sp else td.get_text(" ", strip=True))


def _meta_linha(r):
    """Campos COMUNS a toda linha de resultado (colocação, cavaleiro, cavalo,
    categoria e ids), independentes do tipo de prova. Cada parser específico
    completa depois os campos de PONTUAÇÃO (penalidade/tempo/voltas/pontos)."""
    cl = r.find("td", class_="classfic-data")
    coloc = None
    if cl:
        b = cl.find("b")
        coloc = (_clean(b.get_text()) if b
                 else _clean(cl.find(string=True, recursive=False))
                 or _clean(cl.get_text(" ", strip=True)))
    cav_el = (r.select_one("td.colunaCavaleiro .format-coluna-competidor strong")
              or r.select_one("td.colunaCavaleiro strong"))
    ent_el = r.select_one("span.descCompetidor")
    idc_el = r.select_one("td.colunaCavaleiro input[id$=hfIDCavaleiro]")
    cavalo_el = r.select_one("td.colunaCavalo strong")
    gen_el = r.select_one("td.colunaCavalo span.descCavalo")
    # categoria: td.border-mobile-data; se ausente (ex.: DESEMPATE), a 1ª td com
    # span.is-mobile-block. Pega o texto DIRETO (antes do span duplicado p/ mobile).
    cat_el = r.select_one("td.border-mobile-data")
    if cat_el is None:
        for td in r.find_all("td"):
            if td.find("span", class_="is-mobile-block"):
                cat_el = td
                break
    categoria = None
    if cat_el is not None:
        direto = cat_el.find(string=True, recursive=False)
        categoria = _clean(direto) or _clean(cat_el.get_text(" ", strip=True))
    return {
        "id_origem": cl.get("id") if cl else None,
        "colocacao": coloc,
        "cavaleiro_nome": _clean(cav_el.get_text() if cav_el else None),
        "entidade": _clean(ent_el.get_text() if ent_el else None),
        "id_cavaleiro_fonte": (idc_el.get("value") if idc_el else None) or None,
        "cavalo_nome": _clean(cavalo_el.get_text() if cavalo_el else None),
        "cavalo_genealogia": _clean(gen_el.get_text(" ") if gen_el else None),
        "categoria": categoria,
    }


def _ler_secoes_dois_percursos(desk):
    """Recebe as células de PONTUAÇÃO (faltas/tempo/status, já SEM a coluna
    Equipe), na ordem do documento, e devolve (pen1, tempo1, pen2, tempo2, resultado).
    Anda por SEÇÕES: uma volta com status ocupa 1 célula; uma volta normal ocupa
    2 (faltas + tempo). O Resultado final é sempre 1 célula (ausente no DESEMPATE)."""
    def is_err(td):
        return "error" in (td.get("class") or [])

    i, n = 0, len(desk)
    pen1 = t1 = pen2 = t2 = resultado = None
    # 1ª volta
    if i < n:
        if is_err(desk[i]):
            pen1 = _clean(desk[i].get_text(" ", strip=True)); i += 1
        else:
            pen1 = _falta_de(desk[i]); i += 1
            if i < n:
                t1 = _tempo_de(desk[i]); i += 1
    # 2ª volta
    if i < n:
        if is_err(desk[i]):
            pen2 = _clean(desk[i].get_text(" ", strip=True)); i += 1
        else:
            pen2 = _falta_de(desk[i]); i += 1
            if i < n:
                t2 = _tempo_de(desk[i]); i += 1
    # resultado final (1 célula: status ou soma de faltas)
    if i < n:
        resultado = (_clean(desk[i].get_text(" ", strip=True))
                     if is_err(desk[i]) else _falta_de(desk[i]))
    return pen1, t1, pen2, t2, resultado


def _parse_resultados_dois_percursos(soup):
    """Resultados de prova '2 PERCURSOS IDENTICOS/DISTINTOS'. Devolve a MESMA
    estrutura de dicts que parse_resultados, preenchendo as DUAS voltas:
      penalidade / tempo       → 1ª volta (faltas-soma ou status; tempo)
      penalidade_2 / tempo_2   → 2ª volta
      pontos                   → Resultado final (status prevalece; senão soma)
    Foco INDIVIDUAL: a coluna Equipe é ignorada por ora (equipe=None).
    """
    out = []
    rows = [tr for tr in soup.find_all("tr") if tr.find("td", class_="classfic-data")]
    for r in rows:
        # células DESKTOP (is-mobile são duplicatas; btnHidden2Volta é só botão).
        # Em provas COM equipe há uma coluna "Equipe" extra (is-desktop, sem marcador
        # de pontuação): as células de PONTUAÇÃO vão pro walk das voltas e a célula de
        # Equipe é capturada à parte. (Ranking de equipe fica pra depois — foco
        # individual; aqui só guardamos o NOME da equipe, se houver.)
        desk = [td for td in r.find_all("td") if "is-desktop" in (td.get("class") or [])]
        score_cells = [td for td in desk if _eh_pontuacao(td)]
        equipe = None
        for td in desk:
            if not _eh_pontuacao(td):
                e = _clean(td.get_text(" ", strip=True))
                equipe = e if e and e != "-" else None
                break  # coluna Equipe = 1ª célula não-pontuação (vem antes das voltas)
        pen1, t1, pen2, t2, resultado = _ler_secoes_dois_percursos(score_cells)

        linha = _meta_linha(r)
        linha.update({
            "equipe": equipe,
            "penalidade": pen1, "tempo": t1,
            "penalidade_2": pen2, "tempo_2": t2, "pontos": resultado,
        })
        out.append(linha)
    return out


# ── RESULTADOS de "DUAS FASES" ───────────────────────────────────────
# RECON AO VIVO (FPH 2026-06; IDs 13604/13606/13671/13673):
#   • div.tipo-prova = "Tipo de Prova: DUAS FASES". Container lvResultadoDuasFases.
#     Linhas = <tr> com td.classfic-data (NÃO tr.table-row-styling).
#   • Cada cavaleiro tem UMA soma de faltas (b.falta-soma-color, ex.: "0 (0+0)",
#     "10 (8+2)") e DOIS tempos (img.icon-tempo): Fase 1 e Fase 2.
#       → penalidade = faltas; tempo = tempo Fase 1; tempo_2 = tempo Fase 2.
#   • STATUS (Desistente/Forfait/…) COLAPSA as 3 células numa única td.error.
#       → penalidade = status; tempo = tempo_2 = None; pontos = status.
#   • Exibição (resultados.html): 'Duas Fases' → [Pen. | T 1ª | T 2ª].

def _parse_resultados_duas_fases(soup):
    """Resultados de prova 'DUAS FASES'. Devolve a MESMA estrutura de dicts que
    parse_resultados: penalidade = soma de faltas (ou status); tempo = Fase 1;
    tempo_2 = Fase 2. penalidade_2 não se aplica (None)."""
    out = []
    rows = [tr for tr in soup.find_all("tr") if tr.find("td", class_="classfic-data")]
    for r in rows:
        # células de PONTUAÇÃO (faltas/tempos/status), sem duplicatas mobile
        score = [td for td in r.find_all("td")
                 if "is-mobile" not in (td.get("class") or []) and _eh_pontuacao(td)]
        err = next((td for td in score if "error" in (td.get("class") or [])), None)
        pen = t1 = t2 = pontos = None
        if err is not None:
            # status colapsa tudo numa célula; é o resultado final
            pen = _clean(err.get_text(" ", strip=True))
            pontos = pen
        else:
            faltas_td = next((td for td in score
                              if td.find("b", class_="falta-soma-color")), None)
            tempos = [td for td in score if td.find("img", class_="icon-tempo")]
            if faltas_td is not None:
                pen = _falta_de(faltas_td)
            if len(tempos) >= 1:
                t1 = _tempo_de(tempos[0])
            if len(tempos) >= 2:
                t2 = _tempo_de(tempos[1])
        linha = _meta_linha(r)
        linha.update({
            "equipe": None,
            "penalidade": pen, "tempo": t1,
            "penalidade_2": None, "tempo_2": t2, "pontos": pontos,
        })
        out.append(linha)
    return out


# ── RESULTADOS de "CRONÔMETRO COM UM DESEMPATE" ──────────────────────
# RECON AO VIVO (FPH 2026-06; ID 13635, 55 linhas):
#   • div.tipo-prova = "…COM UM DESEMPATE". Container lvResultadoDesempate.
#     Linhas = <tr> com td.classfic-data.
#   • Estrutura = 2 voltas idênticas às seletivas: [faltas1, tempo1] + [faltasD, tempoD],
#     PORÉM SEM célula final de Resultado. Só quem zera a 1ª volta vai ao desempate;
#     quem não vai tem as células do desempate como placeholders "---"/"-----"
#     (SEM marcador) → naturalmente ignorados (só entram células com marcador).
#   • STATUS (Desistente/Forfait) pode estar na 1ª volta (1 td.error + placeholders)
#     ou no desempate (faltas1/tempo1 normais + 1 td.error). O caminhador por seções
#     (_ler_secoes_dois_percursos) trata ambos.
#       → penalidade/tempo = 1ª volta; penalidade_2/tempo_2 = desempate; pontos = None.
#   • Exibição (resultados.html): 'Desempate' → [Pen 1ª | T 1ª | Pen 2ª | T 2ª].

def _parse_resultados_desempate(soup):
    """Resultados de prova 'CRONÔMETRO COM UM DESEMPATE'. 2 voltas (1ª + desempate),
    sem Resultado final. Reaproveita o caminhador por seções; placeholders
    "---"/"-----" do desempate (sem marcador) ficam de fora."""
    out = []
    rows = [tr for tr in soup.find_all("tr") if tr.find("td", class_="classfic-data")]
    for r in rows:
        # células de PONTUAÇÃO = is-desktop COM marcador (exclui categoria,
        # btnHidden2Volta, prêmio e os placeholders "---"/"-----").
        score = [td for td in r.find_all("td")
                 if "is-desktop" in (td.get("class") or []) and _eh_pontuacao(td)]
        pen1, t1, pen2, t2, _ = _ler_secoes_dois_percursos(score)
        linha = _meta_linha(r)
        linha.update({
            "equipe": None,
            "penalidade": pen1, "tempo": t1,
            "penalidade_2": pen2, "tempo_2": t2, "pontos": None,
        })
        out.append(linha)
    return out


def _parse_resultados_tempo_ideal(soup):
    """Resultados de prova ao TEMPO IDEAL e TEMPO OCULTO — exibição idêntica (o
    Oculto só não revela o tempo antes do fim da prova). Container
    lvResultadoTempoIdeal quando existe; no OCULTO cai no fallback p/ soup. A
    CLASSIFICAÇÃO é pela APROXIMAÇÃO = |tempo do conjunto − tempo ideal|, que o
    site mostra como coluna numérica própria e que guardamos em `pontos` (é o
    dado que importa p/ o competidor). As células de valor em DESKTOP
    (td.is-desktop) vêm na ordem [faltas, tempo, aproximação]; ELIMINADOS trazem
    só um status (td.error), sem tempo nem aproximação. Reaproveita _meta_linha
    p/ os campos comuns. Tolerante: célula/linha faltando vira None."""
    pn = (soup.find(id=re.compile(r"pnResultadoTempo(Ideal|Oculto)", re.I))
          or soup.find(id=re.compile(r"lvResultadoTempo(Ideal|Oculto)", re.I)) or soup)
    out = []
    rows = [tr for tr in pn.find_all("tr") if tr.find("td", class_="classfic-data")]
    for r in rows:
        linha = _meta_linha(r)
        # células de valor em desktop, na ordem da página
        desk = [td for td in r.find_all("td") if "is-desktop" in (td.get("class") or [])]
        # tempo: a célula do ícone de relógio; APROXIMAÇÃO = a célula logo após
        # (mais robusto que desk[-1], caso haja botão de vídeo no fim).
        tempo, aprox, tempo_idx = None, None, None
        for i, td in enumerate(desk):
            if td.find("img", class_="icon-tempo") is not None:
                tempo = _tempo_de(td)
                tempo_idx = i
                break
        if tempo_idx is not None and tempo_idx + 1 < len(desk):
            aprox = _clean(desk[tempo_idx + 1].get_text(" ", strip=True))
        # penalidade: soma de faltas (b.falta-soma-color) ou status (td.error)
        falta_el = r.select_one("b.falta-soma-color") or r.select_one("td.error")
        linha.update({
            "equipe": None,
            "penalidade": _clean(falta_el.get_text() if falta_el else None),
            "tempo": tempo,
            "pontos": aprox,
            "penalidade_2": None,
            "tempo_2": None,
        })
        out.append(linha)
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
