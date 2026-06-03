"""
Reconciliação do `id_nativo` — o elo que faltava.

PROBLEMA: os torneios herdados do N8N estão SEM `id_nativo` (o número do torneio
no site da federação). Sem ele, o pipeline de detalhe (provas/docs) e a ordem de
entrada não conseguem achar o torneio, e o `--write` do calendário DUPLICARIA as
linhas (upsert é por (fonte, id_nativo); null nunca casa → reinsere).

SOLUÇÃO: casar cada evento do calendário AO VIVO (que TRAZ o id_nativo) com o
torneio já no banco, pela MESMA fonte, por DATA + semelhança de nome, e backfillar
o id_nativo. Conservador de propósito (o usuário confere no app):

  • agrupa por data_inicio (a data é a âncora forte);
  • mesma data, 1 vivo × 1 banco → casa (alta confiança, mesmo com nome diferente);
  • vários na mesma data → maior sobreposição de tokens do nome (Jaccard ≥ piso);
  • nunca reusa o mesmo torneio nem o mesmo evento;
  • PULA id_nativo que JÁ existe na fonte (aí a linha legada é DUPLICATA a fundir
    num passo de dedup à parte — não casar, pra não violar a chave (fonte,id_nativo)).

Só matching puro aqui (testável sem rede). A leitura do banco e o PATCH ficam no
orquestrador (main.py --reconcile), que injeta os dados e a função de gravação.
"""
import re
import unicodedata

# ruído comum que não distingue um evento do outro (classe, ano, palavras-cola)
_STOP = {
    "DE", "DA", "DO", "DOS", "DAS", "E", "A", "O", "EM", "COM",
    "ETAPA", "CIRCUITO", "CAMPEONATO", "COPA", "TROFEU", "GP", "GRANDE", "PREMIO",
    "CSN", "CSI", "CSIE", "CSIW", "CBS", "CCE", "TACA", "RANKING", "DESAFIO",
    "2024", "2025", "2026", "2027",
}


def _tokens(nome):
    """Conjunto de tokens normalizados (sem acento, MAIÚSCULA, sem pontuação,
    sem stopwords/ordinais)."""
    s = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9 ]", " ", s).upper()
    out = set()
    for t in s.split():
        t = re.sub(r"^\d+[AOª°]?$", "", t)   # descarta ordinais soltos ("4", "4A")
        if len(t) > 1 and t not in _STOP:
            out.add(t)
    return out


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def casar(eventos, db_rows, id_nativos_usados=None, piso=0.30):
    """Casa eventos (live, têm id_nativo) com db_rows (banco, id_nativo NULL).

    eventos : [{id_nativo, nome, data_inicio, data_fim}]
    db_rows : [{id, nome, data_inicio, data_fim}]
    id_nativos_usados : set de id_nativo (str) já existentes na fonte → pular.

    Devolve dict:
      matches        : [{torneio_id, id_nativo, data, nome_banco, nome_vivo,
                         score, confianca}]
      vivos_sem_match: eventos ao vivo sem par no banco (torneios novos)
      banco_sem_match: linhas do banco sem par neste mês (outra data/mês)
      pulados_dup    : eventos cujo id_nativo já existe (dup legada a fundir)
    """
    from collections import defaultdict
    usados = {str(x) for x in (id_nativos_usados or set())}

    # separa eventos cujo id_nativo já existe (não casar — seria duplicar a chave)
    pulados_dup, vivos = [], []
    for e in eventos:
        (pulados_dup if str(e.get("id_nativo")) in usados else vivos).append(e)

    ev_by_date = defaultdict(list)
    for e in vivos:
        ev_by_date[e.get("data_inicio")].append(e)
    db_by_date = defaultdict(list)
    for r in db_rows:
        db_by_date[r.get("data_inicio")].append(r)

    used_live, used_db, matches = set(), set(), []
    for data, evs in ev_by_date.items():
        cands = db_by_date.get(data, [])
        if not data or not cands:
            continue
        unico = len(evs) == 1 and len(cands) == 1
        pares = []
        for e in evs:
            te = _tokens(e.get("nome"))
            for r in cands:
                pares.append((_jaccard(te, _tokens(r.get("nome"))), e, r))
        pares.sort(key=lambda x: -x[0])
        for sc, e, r in pares:
            if id(e) in used_live or r["id"] in used_db:
                continue
            if sc >= piso or unico:
                matches.append({
                    "torneio_id": r["id"], "id_nativo": e["id_nativo"],
                    "data": data, "nome_banco": r.get("nome"),
                    "nome_vivo": e.get("nome"), "score": round(sc, 2),
                    "confianca": "alta" if (unico or sc >= 0.6) else "media",
                })
                used_live.add(id(e)); used_db.add(r["id"])

    vivos_sem = [e for e in vivos if id(e) not in used_live]
    banco_sem = [r for r in db_rows if r["id"] not in used_db]
    return {"matches": matches, "vivos_sem_match": vivos_sem,
            "banco_sem_match": banco_sem, "pulados_dup": pulados_dup}
