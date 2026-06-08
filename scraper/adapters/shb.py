"""Adapter SHB (Sociedade Hípica Brasileira) — sistema shb.app.br/inscricao-online.

Resultado POR PROVA via grid Scriptcase (HTML limpo → parsing determinístico,
sem Claude). Três telas:
  grid_listagem_concursos_publico/?token=TOKEN  → lista de concursos (ids)
  ordem_de_entrada_resultado/?concurso=N        → cabeçalho (nome+datas) + provas
  resultado_online/?concurso=N&prova=PROVA XX&ordem=classificacao_geral → tabela

O TOKEN é público (vem da config da fonte). Encoding das páginas: ISO-8859-1.
"""
import re
import requests
from urllib.parse import quote
from bs4 import BeautifulSoup

BASE = "https://www.shb.app.br/inscricao-online"
H = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
}
_STATUS = re.compile(r"\b(ELIM|FORF|DESC|DESQ|ABAND|RETIR|N\.?C|WD)\b", re.I)
_TEMPO = re.compile(r"^\d{1,3},\d{1,2}$")
_MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
          "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
          "outubro": 10, "novembro": 11, "dezembro": 12}


def _iso(dia, mes_nome, ano):
    m = _MESES.get((mes_nome or "").lower())
    if not m:
        return None
    return f"{int(ano):04d}-{m:02d}-{int(dia):02d}"


def parse_periodo(periodo):
    """'27 A 31 DE MAIO DE 2026' → ('2026-05-27','2026-05-31'); '28 DE MAIO DE
    2026' → (mesma data, mesma data). (ini, fim) ou (None, None)."""
    p = _clean(periodo)
    # "DE" antes do ano é opcional ("03 A 07 DE JUNHO 2026" e "...DE JUNHO DE 2026")
    m = re.search(r"(\d{1,2})\s+A\s+(\d{1,2})\s+DE\s+(\w+)\s+(?:DE\s+)?(\d{4})", p, re.I)
    if m:
        return _iso(m.group(1), m.group(3), m.group(4)), _iso(m.group(2), m.group(3), m.group(4))
    m = re.search(r"(\d{1,2})\s+DE\s+(\w+)\s+(?:DE\s+)?(\d{4})", p, re.I)
    if m:
        d = _iso(m.group(1), m.group(2), m.group(3))
        return d, d
    return None, None


def _get(url):
    r = requests.get(url, headers=H, timeout=45)
    r.encoding = "ISO-8859-1"
    return r.text


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _clean(s):
    # remove caracteres de controle/C1 (lixo de nomes corrompidos na origem SHB,
    # ex.: entidade &#135;) e normaliza espaços.
    s = _CTRL.sub("", (s or "").replace("\xa0", " "))
    return re.sub(r"\s+", " ", s).strip()


def listar_concursos(token):
    """IDs de concurso visíveis no grid público (mês corrente + recentes)."""
    h = _get(f"{BASE}/grid_listagem_concursos_publico/?token={token}")
    return sorted({int(x) for x in re.findall(
        r"ordem_de_entrada_resultado/\?concurso=(\d+)", h)})


def detalhar_concurso(concurso):
    """→ {nome, periodo, provas:[{codigo, nome}]} a partir da tela de ordem."""
    h = _get(f"{BASE}/ordem_de_entrada_resultado/?concurso={concurso}")
    s = BeautifulSoup(h, "html.parser")
    full = _clean(s.get_text(" "))
    # provas + data_prova: varre o HTML em ordem; cabeçalhos "28 de Maio de 2026"
    # precedem as provas daquele dia; cada prova = link resultado_online.
    provas, seen = [], set()
    data_atual = None
    rx = re.compile(
        r"(?P<data>\b\d{1,2}\s+de\s+\w+\s+de\s+\d{4}\b)"
        r"|resultado_online/\?concurso=\d+&prova=(?P<prova>[^&'\"]+)&ordem=", re.I)
    for m in rx.finditer(h):
        if m.group("data"):
            md = re.match(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", m.group("data"), re.I)
            if md:
                data_atual = _iso(md.group(1), md.group(2), md.group(3))
            continue
        cod = _clean(m.group("prova"))
        if cod in seen:
            continue
        seen.add(cod)
        mm = re.search(
            re.escape(cod) + r"\s*-\s*([0-9][0-9,\. ]*M(?:\s*X\s*[0-9][0-9,\. ]*M)?)",
            full)
        num = re.search(r"(\d+)", cod)
        provas.append({
            "codigo": cod,
            "nome": _clean(cod + (" - " + _clean(mm.group(1)) if mm else "")),
            "numero": int(num.group(1)) if num else None,
            "data_prova": data_atual,
            "tipo_prova": ("DUAS FASES" if "DUAS FASES" in full[max(0, m.start() - 200):m.start()].upper() else None),
        })
    # fallback: provas direto do TEXTO quando os links resultado_online não estão
    # no HTML estático (ex.: concurso 402, hospedado por outra federação). Casa
    # "PROVA <cod> - <altura>" e usa o cabeçalho de dia anterior como data.
    if not provas:
        data_atual = None
        rxt = re.compile(
            r"(?P<data>\b\d{1,2}\s+de\s+\w+\s+de\s+\d{4}\b)"
            r"|PROVA\s+(?P<cod>\d+[A-Z]?)\s*-\s*(?P<alt>[0-9][0-9,\.]*\s*M(?:\s*X\s*[0-9][0-9,\.]*\s*M)?)",
            re.I)
        for m in rxt.finditer(full):
            if m.group("data"):
                md = re.match(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", m.group("data"), re.I)
                if md:
                    data_atual = _iso(md.group(1), md.group(2), md.group(3))
                continue
            cod = "PROVA " + m.group("cod").upper()
            if cod in seen:
                continue
            seen.add(cod)
            num = re.search(r"(\d+)", cod)
            provas.append({
                "codigo": cod, "nome": _clean(cod + " - " + _clean(m.group("alt"))),
                "numero": int(num.group(1)) if num else None,
                "data_prova": data_atual, "tipo_prova": None})
    # nome do concurso + período. O cabeçalho é "<FEDERAÇÃO> <NOME> <PERÍODO>";
    # a federação varia (SHB, FEERJ…), então corta a partir do 1º termo de nome
    # de concurso e isola o período no fim ("DE" antes do ano é opcional).
    per = re.search(r"(\d{1,2}\s+A\s+\d{1,2}\s+DE\s+\w+\s+(?:DE\s+)?\d{4}"
                    r"|\d{1,2}\s+DE\s+\w+\s+(?:DE\s+)?\d{4})", full)
    periodo = _clean(per.group(1)) if per else ""
    cabecalho = full[:per.start()] if per else full
    mk = re.search(r"\b(CSN\d*\*?|CSE|CSI\d*\*?|CN|COPA|CONCURSO|CAMPEONATO|TORNEIO|"
                   r"RANKING|PROVA INTERNA|\d+ª?\s*ETAPA|GRANDE\s+PR[EÊ]MIO|GP)\b",
                   cabecalho, re.I)
    nome = _clean(cabecalho[mk.start():]) if mk else f"Concurso SHB {concurso}"
    return {"concurso": concurso, "nome": nome, "periodo": periodo, "provas": provas}


def _num(s):
    """'0'→'0', '4'→'4', '12'→'12'; texto sem dígito volta como veio."""
    s = (s or "").strip()
    d = re.sub(r"[^\d]", "", s)
    return d if d != "" else (s or None)


def _tempo_val(s):
    """Limpa prefixos ('DT: 75,13'→'75,13') e valida formato de tempo."""
    s = re.sub(r"(?i)^\s*(DT|T)\s*:?\s*", "", (s or "").strip())
    return s if _TEMPO.match(s) else None


def _map_header(cells):
    """Índices das colunas a partir de uma linha de cabeçalho da tabela.
    Layouts SHB: 1 par FALTA/TEMPO (TAB.A), 2 pares + TOTAL (DUAS FASES), 2 pares
    sem TOTAL (DESEMPATE/jump-off). Devolve os PARES (falta,tempo) em ordem."""
    up = [c.upper() for c in cells]
    if "CONCORRENTE" not in up or "CAVALO" not in up:
        return None
    falta = [i for i, c in enumerate(up) if c == "FALTA"]
    tempo = [i for i, c in enumerate(up) if c == "TEMPO"]
    total = next((i for i, c in enumerate(up) if c == "TOTAL"), None)
    cl = [i for i, c in enumerate(up) if c == "CL"]
    pares = list(zip(falta, tempo))      # pareia 1:1 na ordem
    return {
        "conc": up.index("CONCORRENTE"),
        "cav": up.index("CAVALO"),
        "pares": pares,
        "total": total,
        "cl": (cl[0] if cl else None),
    }


def parse_resultados(concurso, prova_codigo):
    """Resultados (classificação geral) de UMA prova → linhas canônicas.

    Linha: {colocacao, cavaleiro_nome, cavalo_nome, penalidade, tempo}.
    """
    url = (f"{BASE}/resultado_online/?concurso={concurso}"
           f"&prova={quote(prova_codigo)}&ordem=classificacao_geral")
    s = BeautifulSoup(_get(url), "html.parser")
    out, vistos = [], set()
    for tb in s.find_all("table"):
        hdr = None
        for tr in tb.find_all("tr"):
            cells = [_clean(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
            if not any(cells):
                continue
            mh = _map_header(cells)
            if mh:
                hdr = mh
                continue
            if not hdr or len(cells) <= hdr["conc"]:
                continue
            cav = cells[hdr["cav"]] if hdr["cav"] < len(cells) else ""
            conc = cells[hdr["conc"]] if hdr["conc"] < len(cells) else ""
            if not conc or not cav or conc.upper() == "CONCORRENTE":
                continue
            colo = None
            if hdr["cl"] is not None and hdr["cl"] < len(cells):
                m = re.search(r"\d+", cells[hdr["cl"]])
                colo = int(m.group(0)) if m else None
            # valores dos PARES (falta,tempo) — pega o ÚLTIMO par não-vazio
            # (desempate, se houve; senão a fase/percurso anterior).
            faltas = [cells[fi] for fi, _ in hdr["pares"] if fi < len(cells)]
            tempos = [cells[ti] for _, ti in hdr["pares"] if ti < len(cells)]
            tempo = next((v for v in (_tempo_val(t) for t in reversed(tempos)) if v), None)
            st = _STATUS.search(" ".join(cells))
            if st and tempo is None:                  # elim/forfait sem tempo
                penal, colo = st.group(0).upper().rstrip("."), None
            else:
                penal = None
                tot = hdr["total"]
                if tot is not None and tot < len(cells) and cells[tot].strip():
                    penal = _num(cells[tot])          # DUAS FASES: faltas TOTAIS
                else:
                    penal = next((_num(f) for f in reversed(faltas) if f.strip()), None)
            if colo is None and penal is None and tempo is None:
                continue                              # inscrito sem resultado: ignora
            chave = (conc, cav, colo)
            if chave in vistos:
                continue
            vistos.add(chave)
            out.append({"colocacao": colo, "cavaleiro_nome": conc,
                        "cavalo_nome": cav, "penalidade": penal, "tempo": tempo})
    return out
