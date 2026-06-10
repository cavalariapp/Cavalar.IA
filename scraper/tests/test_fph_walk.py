"""
Testes do BACKFILL histórico da FPH (--fph-walk) — funções PURAS, sem rede/navegador.

Cobrem o cabeçalho AUTO-CONTIDO da página Resultados.aspx?ID=N (parse_resultado_pagina),
a classificação Tipo A/B (detectar_tipo_resultado), a leitura das categorias do
balão (parse_categorias_balao) e a MESCLAGEM num ranking único por barema
(mesclar_resultados — a regra de negócio do cliente: juntar todas as categorias
por desempenho, preservando a categoria).

Fixtures (capturas ao vivo, 2026-06):
  fph_result_2013_1000.html  — 2013, TEMPO IDEAL/FAIXA DE TEMPO (Tipo A, 1 categoria)
  fph_result_tipoA_3000.html — 2015, DESEMPATE, 6 categorias já MESCLADAS pelo site
  fph_result_tipoB_800.html  — 2013, balão "Selecione" (AM/JC/M/MR), sem tabela no GET
"""
import os
from scraper.adapters.macronetwork import (
    parse_resultado_pagina, detectar_tipo_resultado, parse_categorias_balao,
    mesclar_resultados, parse_resultados,
)

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8", errors="replace") as f:
        return f.read()


# ── cabeçalho auto-contido (evento+data+prova+ALTURA+categorias+barema) ──
def test_header_2013_tempo_ideal():
    h = parse_resultado_pagina(_fixture("fph_result_2013_1000.html"))
    assert h["evento"].startswith("TEMPORADA OFICIAL SALTO INICIANTE")
    assert h["data"] == "2013-11-10"
    assert h["numero"] == "01"
    assert h["altura_m"] == 0.40            # altura EXPLÍCITA — base p/ altura máxima
    assert h["categorias"] is None          # categoria única (não vira chip-slash)
    assert "TEMPO IDEAL" in h["tipo_prova"]
    assert h["horario"] == "09h30"


def test_header_tipoA_multi_categoria():
    h = parse_resultado_pagina(_fixture("fph_result_tipoA_3000.html"))
    assert h["data"] == "2015-11-21"
    assert h["altura_m"] == 1.30
    assert h["categorias"] == "AMT/MT/PJR/JCT/JR/SR"
    assert "DESEMPATE" in h["tipo_prova"]


def test_header_tipoB_balao():
    h = parse_resultado_pagina(_fixture("fph_result_tipoB_800.html"))
    assert h["data"] == "2013-10-20"
    assert h["altura_m"] == 1.20
    assert h["categorias"] == "AM/JC/M/MR"


def test_categorias_nao_capturam_data():
    # regressão: a regex de categorias NÃO pode casar a data (10/11/2013)
    h = parse_resultado_pagina(_fixture("fph_resultados_14009.html"))
    assert h["categorias"] is None          # JCA é única → None, nunca uma data


# ── classificação Tipo A (tabela) x Tipo B (balão) x vazio ───────────────
def test_detecta_tipo_tabela():
    assert detectar_tipo_resultado(_fixture("fph_result_2013_1000.html")) == "tabela"
    assert detectar_tipo_resultado(_fixture("fph_result_tipoA_3000.html")) == "tabela"


def test_detecta_tipo_balao():
    assert detectar_tipo_resultado(_fixture("fph_result_tipoB_800.html")) == "balao"


def test_categorias_balao():
    cats = parse_categorias_balao(_fixture("fph_result_tipoB_800.html"))
    labels = [lab for _, lab in cats]
    assert labels == ["AM", "JC", "M", "MR"]   # exclui o placeholder "Selecione"
    assert all(val.isdigit() for val, _ in cats)


def test_tipoA_ja_vem_parseado_e_mesclado():
    # Tipo A: o site já entrega TODAS as categorias num ranking único 1..N
    rows = parse_resultados(_fixture("fph_result_tipoA_3000.html"))
    assert len(rows) == 28
    assert rows[0]["colocacao"] == "1º"
    # genealogia traz NASCIMENTO (base do casamento por nome+nascimento)
    assert any(r.get("cavalo_genealogia") and "/" in r["cavalo_genealogia"] for r in rows)


# ── MESCLAGEM por barema (regra de negócio: 1 ranking, sem fronteira de categoria)
def test_merge_cronometro_faltas_depois_tempo():
    jr = [{"categoria": "JR", "cavalo_nome": "A", "penalidade": "0", "tempo": "70,50"},
          {"categoria": "JR", "cavalo_nome": "B", "penalidade": "4", "tempo": "65,00"}]
    sr = [{"categoria": "SR", "cavalo_nome": "C", "penalidade": "0", "tempo": "68,20"},
          {"categoria": "SR", "cavalo_nome": "D", "penalidade": "0", "tempo": "72,00"},
          {"categoria": "SR", "cavalo_nome": "E", "penalidade": "Eliminado", "tempo": None}]
    out = mesclar_resultados([jr, sr], "CRONÔMETRO TAB A")
    # 0 faltas vence; entre 0 faltas, menor tempo; depois 4 faltas; eliminado por último
    assert [r["cavalo_nome"] for r in out] == ["C", "A", "D", "B", "E"]
    assert [r["colocacao"] for r in out] == ["1º", "2º", "3º", "4º", None]
    # categoria PRESERVADA
    assert {r["cavalo_nome"]: r["categoria"] for r in out}["C"] == "SR"


def test_merge_tempo_ideal_usa_aproximacao():
    # ao TEMPO IDEAL vence quem chega mais perto (menor 'pontos' = aproximação)
    a = [{"categoria": "AM", "cavalo_nome": "X", "penalidade": "0", "tempo": "60,00", "pontos": "1,20"}]
    b = [{"categoria": "M", "cavalo_nome": "Y", "penalidade": "0", "tempo": "80,00", "pontos": "0,10"}]
    out = mesclar_resultados([a, b], "NORMAL COM FAIXA DE TEMPO E TEMPO IDEAL")
    assert [r["cavalo_nome"] for r in out] == ["Y", "X"]   # Y mais perto do ideal


def test_merge_estavel_e_sem_nota_no_fim():
    a = [{"categoria": "JR", "cavalo_nome": "P", "penalidade": "Desistente", "tempo": None}]
    b = [{"categoria": "SR", "cavalo_nome": "Q", "penalidade": "0", "tempo": "50,00"}]
    out = mesclar_resultados([a, b], "CRONÔMETRO")
    assert out[0]["cavalo_nome"] == "Q" and out[0]["colocacao"] == "1º"
    assert out[-1]["cavalo_nome"] == "P" and out[-1]["colocacao"] is None
