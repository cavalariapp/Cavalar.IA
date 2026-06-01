"""
Testes dos CONVERSORES da camada de escrita (db.py) — funções PURAS, sem rede.
Roda com: pytest scraper/tests/  (ou: python -m scraper.tests.test_db)

Garante o CONTRATO com o front (resultados.html) e com o chatbot:
  • provas.categorias = só códigos, juntados por '/'  (o front faz .split('/'))
  • provas.descricao  = texto COM altura  (o front extrai '1,30m' daí)
  • provas.numero     = int  (o front ordena aritmeticamente)
  • só colunas CONFIRMADAS de `provas` saem no row (horario/local NÃO são colunas)
  • documento_to_row NÃO emite os campos extraídos pelo chatbot
    (texto_extraido/conteudo_estruturado/...), que o upsert preserva.
"""
import os
from scraper.adapters.macronetwork import parse_provas, parse_documentos
from scraper.db import prova_to_row, documento_to_row

FIX = os.path.join(os.path.dirname(__file__), "fixtures")

# colunas REAIS de `provas` (information_schema) que o scraper escreve
PROVA_COLS = {"torneio_id", "id_origem", "nome", "numero", "descricao",
              "categorias", "tipo_prova", "data_prova", "dia_semana"}
# colunas de torneio_documentos que NÃO são do scraper (chatbot) — nunca no row
DOC_COLS_PROIBIDAS = {"texto_extraido", "texto_extraido_em",
                      "conteudo_estruturado", "estruturado_em",
                      "visto_em", "criado_em", "id"}


def _fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8", errors="replace") as f:
        return f.read()


# ── prova_to_row ──────────────────────────────────────────────────────
def test_prova_row_separa_categoria_e_altura():
    # nome 'PR. 01 - 1,30M - JCT - 1,30M - JCT' →
    #   descricao mantém a altura, categorias só o código
    p = parse_provas(_fixture("fph_provas_3436.html"))[0]
    row = prova_to_row(p, torneio_id=999)
    assert row["descricao"] == "1,30M - JCT"   # altura preservada p/ o front
    assert row["categorias"] == "JCT"          # só o código (sem '1,30M')

def test_prova_row_numero_eh_int():
    row = prova_to_row(parse_provas(_fixture("fph_provas_3436.html"))[0], 999)
    assert row["numero"] == 1 and isinstance(row["numero"], int)

def test_prova_row_so_colunas_confirmadas():
    # mandar coluna inexistente (horario/local) daria 400 no PostgREST
    row = prova_to_row(parse_provas(_fixture("fph_provas_3436.html"))[0], 999)
    assert set(row.keys()) == PROVA_COLS
    assert "horario" not in row and "local" not in row

def test_prova_row_categorias_sem_altura_em_todas():
    rows = [prova_to_row(p, 999)
            for p in parse_provas(_fixture("fph_provas_3436.html"))]
    # nenhuma categoria pode conter um token de altura ('1,30M' etc.)
    assert all("M" not in (r["categorias"] or "").upper() or
               not any(seg.strip()[:1].isdigit()
                       for seg in (r["categorias"] or "").split("/"))
               for r in rows)
    assert {r["categorias"] for r in rows} == {"JC", "JCA", "JCB", "JCT"}
    assert all(r["torneio_id"] == 999 and r["id_origem"] for r in rows)


# ── documento_to_row ──────────────────────────────────────────────────
def test_doc_row_nao_emite_campos_do_chatbot():
    docs = parse_documentos(_fixture("fph_docs_3436.html"))
    rows = [documento_to_row(d, torneio_id=999) for d in docs]
    for r in rows:
        assert DOC_COLS_PROIBIDAS.isdisjoint(r.keys())  # preservados, não escritos
        assert r["torneio_id"] == 999
        assert r["url_pdf"].lower().endswith(".pdf")
    assert {r["tipo"] for r in rows} == {"programa", "adendo"}


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn(); ok += 1; print(f"  OK  {fn.__name__}")
        except Exception:
            print(f"  XX  {fn.__name__}"); traceback.print_exc()
    print(f"\n{ok}/{len(fns)} passaram")
