"""Testes do matching da reconciliação (scraper/reconcile.py) — puro, sem rede.
Roda no CI (pytest) e localmente (python -m scraper.tests.test_reconcile)."""
from scraper.reconcile import casar, _tokens, _jaccard


def test_match_1x1_data_unica_mesmo_nome_diferente():
    # mesma data, 1 vivo × 1 banco → casa mesmo com nome abreviado (alta confiança)
    eventos = [{"id_nativo": 3444, "nome": "CSN5* JHSF 55ª COPA SP", "data_inicio": "2026-06-16"}]
    db = [{"id": 1, "nome": "CSN 55ª COPA SÃO PAULO 5ª ETAPA", "data_inicio": "2026-06-16"}]
    r = casar(eventos, db)
    assert len(r["matches"]) == 1
    m = r["matches"][0]
    assert m["torneio_id"] == 1 and m["id_nativo"] == 3444 and m["confianca"] == "alta"


def test_pula_id_nativo_ja_existente():
    # se o id_nativo já existe na fonte, NÃO casa (é dup legada a fundir à parte)
    eventos = [{"id_nativo": 3321, "nome": "CSN COPA VILLAGEMALL", "data_inicio": "2026-06-03"}]
    db = [{"id": 9, "nome": "CSN COPA VILLAGEMALL", "data_inicio": "2026-06-03"}]
    r = casar(eventos, db, id_nativos_usados={"3321"})
    assert r["matches"] == [] and len(r["pulados_dup"]) == 1


def test_sem_data_em_comum_nao_casa():
    eventos = [{"id_nativo": 1, "nome": "GP X", "data_inicio": "2026-06-10"}]
    db = [{"id": 1, "nome": "GP X", "data_inicio": "2026-07-10"}]
    r = casar(eventos, db)
    assert r["matches"] == []
    assert len(r["vivos_sem_match"]) == 1 and len(r["banco_sem_match"]) == 1


def test_dois_na_mesma_data_casa_pelo_melhor_nome():
    eventos = [
        {"id_nativo": 10, "nome": "TROFEU EFICIENCIA VILLAGEMALL", "data_inicio": "2026-06-06"},
        {"id_nativo": 20, "nome": "CENTRAL CUP INICIANTES",        "data_inicio": "2026-06-06"},
    ]
    db = [
        {"id": 1, "nome": "CENTRAL CUP",                          "data_inicio": "2026-06-06"},
        {"id": 2, "nome": "TROFEU EFICIENCIA VILLAGEMALL 2026",   "data_inicio": "2026-06-06"},
    ]
    r = casar(eventos, db)
    by = {m["id_nativo"]: m["torneio_id"] for m in r["matches"]}
    assert by.get(10) == 2 and by.get(20) == 1


def test_tokens_remove_acento_stopword_ordinal():
    t = _tokens("4ª ETAPA TROFÉU EFICIÊNCIA 2026")
    assert "EFICIENCIA" in t            # token distintivo fica
    assert "TROFEU" not in t            # stopword sai
    assert "2026" not in t              # ano sai
    assert all(not x.isdigit() for x in t)  # ordinais soltos saem


def test_jaccard_basico():
    assert _jaccard({"A", "B"}, {"A", "B"}) == 1.0
    assert _jaccard({"A"}, {"B"}) == 0.0
    assert _jaccard(set(), {"A"}) == 0.0


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn(); print(f"  OK  {fn.__name__}"); ok += 1
        except AssertionError as e:
            print(f"  FALHOU {fn.__name__}: {e}")
        except Exception as e:
            print(f"  ERRO {fn.__name__}: {e.__class__.__name__}: {e}")
    print(f"{ok}/{len(fns)} passaram")
    sys.exit(0 if ok == len(fns) else 1)
