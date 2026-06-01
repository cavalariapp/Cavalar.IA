"""
Testes do parser MacroNetwork — funções PURAS, sem rede nem navegador.
Roda com: pytest scraper/tests/  (ou: python -m scraper.tests.test_macronetwork)

A fixture fph_calendario_2026_05.html é uma captura AO VIVO do calendário
da FPH (maio/2026). Prova que o parser amarra o ano do ddlAno e elimina a
raiz dos 179 torneios sem data.
"""
import os
from scraper.adapters.macronetwork import (
    parse_date_range, parse_calendar, read_selected_month, read_pager_pages,
    parse_detalhe_header, parse_documentos, parse_provas,
)

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8", errors="replace") as f:
        return f.read()


# ── parser de datas (o coração: amarra o ano que o card NÃO tem) ──────
def test_intervalo_mesmo_mes():
    assert parse_date_range("01/ mai a 03/ mai", 2026, 5) == ("2026-05-01", "2026-05-03")

def test_dia_solto_usa_mes_do_filtro():
    assert parse_date_range("09", 2026, 5) == ("2026-05-09", "2026-05-09")

def test_data_unica_com_mes():
    assert parse_date_range("09/ mai", 2026, 5) == ("2026-05-09", "2026-05-09")

def test_virada_de_ano_dez_jan():
    assert parse_date_range("30/ dez a 02/ jan", 2025, 12) == ("2025-12-30", "2026-01-02")

def test_cruza_mes_sem_virar_ano():
    assert parse_date_range("27/ nov a 01/ dez", 2026, 11) == ("2026-11-27", "2026-12-01")

def test_dia_solto_sem_filtro_eh_indeterminado():
    assert parse_date_range("09", 2026, None) == (None, None)

def test_texto_vazio_ou_lixo():
    assert parse_date_range("", 2026, 5) == (None, None)
    assert parse_date_range("a definir", 2026, 5) == (None, None)

def test_data_invalida_nao_quebra():
    assert parse_date_range("31/ fev a 01/ mar", 2026, 2) == (None, None)


# ── parser do calendário (fixture ao vivo) ───────────────────────────
def test_calendario_extrai_10_eventos_sem_nulos():
    html = _fixture("fph_calendario_2026_05.html")
    assert read_selected_month(html) == 5
    assert read_pager_pages(html) == ["1", "2"]
    evs = parse_calendar(html, 2026)
    assert len(evs) == 10
    # nenhum evento sem data — o ano foi amarrado do ddlAno
    assert all(e["data_inicio"] and e["data_fim"] for e in evs)
    # campos-chave presentes
    assert all(e["id_nativo"] and e["nome"] and e["organizador_entidade"] for e in evs)

def test_calendario_amarra_ano_2026():
    evs = parse_calendar(_fixture("fph_calendario_2026_05.html"), 2026)
    assert all(e["data_inicio"].startswith("2026-05") for e in evs)

def test_calendario_captura_organizador_fantasma():
    # "CSIe - SINOP" (Sinop/MT) na FPH/SP: organizador-entidade != FPH = sinal de fantasma
    evs = parse_calendar(_fixture("fph_calendario_2026_05.html"), 2026)
    sinop = next(e for e in evs if "SINOP" in e["nome"])
    assert sinop["organizador_entidade"] == "37744"
    assert sinop["id_nativo"] == "3382"


# ── detalhe: cabeçalho estático (Fase B — só o que vem no GET) ────────
def test_detalhe_header_extrai_titulo():
    # O título vem no HTML do GET (div.titulo-data > h2); provas/docs são AJAX.
    head = parse_detalhe_header(_fixture("fph_detalhe_3316.html"))
    assert head["titulo"] == "CSN COPA JK DE HIPISMO"


# ── detalhe: documentos (Programa/Adendos) — fixture ao vivo do dump CI ──
def test_documentos_extrai_programa_e_adendo():
    # fph_docs_3436.html = page.content() após a aba "Programas/Adendo" (dump CI).
    docs = parse_documentos(_fixture("fph_docs_3436.html"))
    assert len(docs) == 2
    assert {d["tipo"] for d in docs} == {"programa", "adendo"}
    # todo doc tem PDF absoluto e título
    assert all(d["url_pdf"].startswith("http") and d["url_pdf"].lower().endswith(".pdf")
               for d in docs)
    assert all(d["titulo"] for d in docs)

def test_documentos_programa_campos():
    docs = parse_documentos(_fixture("fph_docs_3436.html"))
    prog = next(d for d in docs if d["tipo"] == "programa")
    assert prog["titulo"] == "PROGRAMA"
    assert prog["data_publicacao"] == "2026-05-12"   # span.data "12/05/2026"
    assert prog["url_pdf"].endswith("PROGRAMA CP JOVEM CAVALEIRO.pdf")

def test_documentos_adendo_quadro_de_horarios():
    # o "quadro de horários" é publicado como ADENDO
    docs = parse_documentos(_fixture("fph_docs_3436.html"))
    ad = next(d for d in docs if d["tipo"] == "adendo")
    assert ad["titulo"] == "QUADRO ATUALIZADO - 29-05"
    assert ad["data_publicacao"] == "2026-05-29"


# ── detalhe: provas (sanfona por dia, todos expandidos) — fixture ao vivo ──
def test_provas_extrai_12_provas_4_dias():
    # fph_provas_3436.html = upCard com os 4 dias (28-31/mai) já expandidos.
    provas = parse_provas(_fixture("fph_provas_3436.html"))
    assert len(provas) == 12
    assert sorted({p["data_prova"] for p in provas}) == \
        ["2026-05-28", "2026-05-29", "2026-05-30", "2026-05-31"]
    # nenhuma prova sem id nativo nem sem data (data vem amarrada da gridCol)
    assert all(p["id_origem"] and p["data_prova"] for p in provas)

def test_provas_primeira_prova_campos():
    p = parse_provas(_fixture("fph_provas_3436.html"))[0]
    assert p["id_origem"] == "14017"          # Resultados.aspx?ID=14017
    assert p["numero"] == "01"
    assert p["nome"] == "PR. 01 - 1,30M - JCT - 1,30M - JCT"
    assert p["categorias"] == "1,30M - JCT"   # dedup do segmento repetido
    assert p["tipo_prova"] == "CRONÔMETRO"
    assert p["data_prova"] == "2026-05-28"
    assert p["dia_semana"] == "quinta-feira"
    assert p["horario"] == "11:30"
    assert p["local"] == "Pista de GRAMA"

def test_provas_id_origem_unico_por_prova():
    # cada prova tem id nativo PRÓPRIO (chave de upsert FK-safe p/ resultados)
    ids = [p["id_origem"] for p in parse_provas(_fixture("fph_provas_3436.html"))]
    assert len(ids) == len(set(ids)) == 12

def test_provas_resultado_url_absoluta_com_base():
    # a URL de Resultados é onde se raspam os resultados depois (Fase C)
    provas = parse_provas(_fixture("fph_provas_3436.html"),
        base_url="https://www.fph.com.br/calendario/ListaProvas?ID=3436")
    assert provas[0]["resultado_url"] == \
        "https://www.fph.com.br/calendario/Resultados.aspx?ID=14017"


if __name__ == "__main__":
    # roda sem pytest: executa cada test_* e reporta
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn(); ok += 1; print(f"  OK  {fn.__name__}")
        except Exception:
            print(f"  XX  {fn.__name__}"); traceback.print_exc()
    print(f"\n{ok}/{len(fns)} passaram")
