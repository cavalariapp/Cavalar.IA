"""
Orquestrador do scraper.  PADRÃO = DRY-RUN (não grava nada).

Exemplos:
  # mês atual da FPH, sem navegador, só imprime o que extrairia (seguro):
  python -m scraper.main --source FPH --current

  # mês atual de todas as fontes ativas (dry-run):
  python -m scraper.main --current

  # mês/ano específico (usa navegador Playwright — requer instalado):
  python -m scraper.main --source FPH --year 2026 --month 7

  # GRAVAR no Supabase (exige SUPABASE_URL + SUPABASE_SERVICE_KEY no ambiente):
  python -m scraper.main --source FPH --current --write

Saída do dry-run: tabela de eventos + contagem + nulos + organizadores
(sinal de fantasma). Nada é enviado ao banco sem --write.
"""
import argparse
import datetime as _dt
import re
import sys

from scraper import sources as SRC
from scraper.adapters import macronetwork as mn
from scraper.db import (
    SupabaseWriter, evento_to_torneio_row, prova_to_row, documento_to_row,
)


def coletar(source, args):
    """Coleta eventos de UMA fonte. Devolve (eventos, modo_usado)."""
    if source["plataforma"] != "macronetwork":
        print(f"  [{source['codigo']}] plataforma '{source['plataforma']}' "
              f"ainda não implementada — pulando.")
        return [], "skip"

    from scraper import fetch

    if args.current:
        html = fetch.fetch_calendar_current(source)
        ano = args.year or _dt.date.today().year
        return mn.parse_calendar(html, ano, base_url=source["calendario_url"]), "requests(mês atual)"

    # mês/ano específico → navegador
    ano = args.year or _dt.date.today().year
    mes = args.month or _dt.date.today().month
    paginas = fetch.fetch_calendar_month(source, ano, mes, headless=not args.headed)
    evs = []
    for html in paginas:
        evs.extend(mn.parse_calendar(html, ano, base_url=source["calendario_url"]))
    # dedup por id_nativo (páginas do pager podem repetir nada, mas garante)
    vistos, uniq = set(), []
    for e in evs:
        if e["id_nativo"] in vistos:
            continue
        vistos.add(e["id_nativo"]); uniq.append(e)
    return uniq, f"playwright({ano}-{mes:02d}, {len(paginas)} pág.)"


def imprimir(source, evs, modo):
    print(f"\n══ {source['codigo']} — {source['nome']}  [{modo}] ══")
    if not evs:
        print("  (nenhum evento)")
        return
    nulos = sum(1 for e in evs if not e["data_inicio"])
    print(f"  {len(evs)} eventos | sem data: {nulos}")
    for e in evs:
        print(f"   ID {str(e['id_nativo']):>5} | {e['data_inicio']}→{e['data_fim']} "
              f"| {str(e['disciplina'] or '?'):<22} | org#{str(e['organizador_entidade'] or '?'):<6} "
              f"| {e['nome']}")
    # agrupamento por organizador (sinal de fantasma: muitos org distintos)
    orgs = {}
    for e in evs:
        orgs.setdefault(e["organizador_entidade"], 0)
        orgs[e["organizador_entidade"]] += 1
    print(f"  organizadores distintos: {len(orgs)} "
          f"(>1 evento: {sorted([(o,n) for o,n in orgs.items() if n>1], key=lambda x:-x[1])})")


def _melhor_provas_html(d):
    """Escolhe a melhor fonte do HTML das provas: o upCard final (todos os dias
    expandidos); cai pro último snapshot por dia; por fim a página inteira."""
    cands = [d.get("provas_html")]
    days = d.get("provas_days") or []
    if days:
        cands.append(days[-1])
    cands.append(d.get("provas_page_html"))
    return next((h for h in cands if h and h.strip()), None)


def _curar_resultados_torneio(src, torneio_id, writer):
    """Pós-detalhe: raspa os RESULTADOS (GET simples, sem navegador) de cada prova
    do torneio e grava (delete+reinsert por prova). Idempotente; parse vazio não
    apaga. Reaproveita o parser canônico (cura o legado N8N de quebra)."""
    from scraper import fetch, db as _db
    provas = writer._get(
        f"/rest/v1/provas?torneio_id=eq.{torneio_id}&id_origem=not.is.null&select=id,id_origem")
    n = ins = ap = meta = 0
    for p in provas:
        try:
            html = fetch.fetch_resultados(src, p["id_origem"])
            R = mn.parse_resultados(html)
            if not R:
                continue
            # ANTI-DESSINCRONIA: o MacroNetwork renumera Resultados.aspx?ID → a
            # metadata da prova (altura/nome, lida via ListaProvas) podia ficar de um
            # retrato e os resultados de outro. Realinhamos a prova ao MESMO retrato
            # (cabeçalho auto-contido da página de resultados) antes de gravar.
            hdr = mn.parse_resultado_pagina(html)
            if hdr:
                hdr["_id"] = p["id_origem"]
                writer.atualizar_prova_meta(p["id"], _prova_row_de_header(hdr, torneio_id))
                meta += 1
            rs = writer.upsert_resultados(p["id"], [_db.resultado_to_row(r, p["id"]) for r in R])
            ins += rs["inseridos"]; ap += rs["apagados"]; n += 1
        except Exception:
            pass
    return {"provas": n, "inseridos": ins, "apagados": ap, "meta_alinhada": meta}


def _inserir_aviso(writer, torneio_id, tipo, titulo):
    """Registra um AVISO de algo NOVO num torneio (programa/adendo/horário/ordem).
    O Database Webhook em avisos_torneio dispara o push pros FAVORITOS. Best-effort:
    nunca derruba o scrape."""
    try:
        writer._post("/rest/v1/avisos_torneio",
                     [{"torneio_id": torneio_id, "tipo": tipo, "titulo": (titulo or "")[:120]}])
    except Exception as e:
        print(f"  ⚠ aviso falhou ({tipo}): {e.__class__.__name__}", file=sys.stderr)


def detalhar(src, native_id, args, writer, avisar=False):
    """Fase B: visita o detalhe de UM torneio, parseia provas+documentos e, com
    --write, faz upsert FK-safe. Sem --write é dry-run (imprime o que gravaria).
    Pré-requisito do write: o torneio já existe em `torneios` (a passada de
    calendário cria); resolve torneio_id por (fonte, id_nativo). Com avisar=True
    (fluxo de próximos), registra aviso quando entra documento NOVO."""
    from scraper import fetch
    detail_url = src["detalhe_url"].format(id=native_id)
    d = fetch.fetch_detail(src, native_id, headless=not args.headed)

    provas_html = _melhor_provas_html(d)
    docs_html = d.get("docs_page_html") or d.get("docs_html")
    provas = mn.parse_provas(provas_html, base_url=detail_url) if provas_html else []
    docs = mn.parse_documentos(docs_html, base_url=detail_url) if docs_html else []

    print(f"\n══ DETALHE {src['codigo']} ID={native_id} ({detail_url}) ══")
    print(f"  provas: {len(provas)} | documentos: {len(docs)}")
    for p in provas[:25]:
        print(f"   PR.{str(p['numero'] or '?'):>3} {p['data_prova']} "
              f"{p['horario'] or '--:--'} | id_origem {p['id_origem']} | {p['nome']}")
    for doc in docs:
        print(f"   [{doc['tipo']}] {doc['data_publicacao']} {doc['titulo']}")

    # SINAL DE FANTASMA (regra de ouro): se a fonte não traz provas NEM docs,
    # ela provavelmente NÃO é dona do evento → não há o que gravar dela.
    if not provas and not docs:
        print("  ⚠ sem provas e sem documentos — possível FANTASMA (fonte não-dona). "
              "Nada a gravar.")
        return 0

    if not args.write:
        print("  (dry-run: nada gravado — use --write para persistir)")
        return 0

    torneio_id = writer.find_torneio_id(src["codigo"], native_id)
    if torneio_id is None:
        print(f"  ⚠ torneio {src['codigo']}/{native_id} ainda não está em `torneios`. "
              f"Rode o calendário (--current --write) antes. Nada gravado.",
              file=sys.stderr)
        return 4

    rp = writer.upsert_provas(
        torneio_id, [prova_to_row(p, torneio_id) for p in provas])
    rd = writer.upsert_documentos(
        torneio_id, [documento_to_row(doc, torneio_id) for doc in docs])
    rr = _curar_resultados_torneio(src, torneio_id, writer)
    print(f"  ✓ provas: {rp} | documentos: {rd} | resultados: {rr}")
    if avisar and rd.get("inseridos", 0) > 0:        # doc novo → notifica favoritos
        _inserir_aviso(writer, torneio_id, "documento", f"{rd['inseridos']} novo(s) documento(s)")
    return 0


def inspecionar_prova(src, prova_id, args):
    """Fase C (dry-run): lê RESULTADOS + ORDEM DE ENTRADA de UMA prova pelo
    id_origem (GET simples, sem navegador) e imprime o que extrairia. A gravação
    entra depois (precisa do schema de `resultados` confirmado e de uma tabela de
    ordem de entrada criada). Imprime as duas listas pra conferência ao vivo."""
    from scraper import fetch
    res_url = src["resultados_url"].format(id=prova_id)
    ord_url = src["ordem_url"].format(id=prova_id)

    res_html = fetch.fetch_resultados(src, prova_id)
    tipo = mn.parse_prova_tipo(res_html)
    R = mn.parse_resultados(res_html)
    print(f"\n══ RESULTADOS {src['codigo']} prova={prova_id}  [tipo: {tipo or '?'}] ══")
    print(f"  {res_url}")
    print(f"  {len(R)} colocações")
    for r in R:
        print(f"   {str(r['colocacao'] or '?'):>4} | falta {str(r['penalidade'] or '-'):>9} "
              f"| tempo {str(r['tempo'] or '--'):>7} | id_res {r['id_origem']} "
              f"| {r['cavaleiro_nome']} >> {r['cavalo_nome']} ({r['categoria'] or '?'})")

    ord_html = fetch.fetch_ordem_entrada(src, prova_id)
    O = mn.parse_ordem_entrada(ord_html)
    print(f"\n══ ORDEM DE ENTRADA {src['codigo']} prova={prova_id} ══")
    print(f"  {ord_url}")
    print(f"  {len(O)} entradas")
    for o in O:
        print(f"   {str(o['ordem'] or '?'):>3}ª | {o['cavaleiro_nome']} >> {o['cavalo_nome']} "
              f"({o['categoria'] or '?'}) | pont {o['pontuacao'] or '-'}")

    if not R and not O:
        print("  ⚠ sem resultados e sem ordem — prova ainda não disputada/publicada?")

    if not args.write:
        print("\n  (dry-run: nada gravado — use --write para persistir)")
        return 0

    # ── GRAVAÇÃO (--write) — resolve provas.id pelo id_origem e faz upsert ──
    from scraper import db
    writer = db.SupabaseWriter()
    if not writer.configured:
        print("\n⚠ --write pedido mas SUPABASE_URL/SUPABASE_SERVICE_KEY ausentes. "
              "Nada gravado.")
        return 1
    prova_db_id = writer.find_prova_id(prova_id, fonte=src["codigo"])
    if prova_db_id is None:
        print(f"\n⚠ prova id_origem={prova_id} não está em `provas` — rode o --detail "
              f"do torneio dela antes (a passada de provas precede a de resultados). "
              f"Nada gravado.")
        return 1

    res_rows = [db.resultado_to_row(r, prova_db_id) for r in R]
    rs = writer.upsert_resultados(prova_db_id, res_rows)
    print(f"\n  resultados → provas.id={prova_db_id}: "
          f"apagados {rs['apagados']}, inseridos {rs['inseridos']} (mapa canônico).")

    ord_rows = [db.ordem_to_row(o, prova_db_id) for o in O]
    try:
        os_ = writer.upsert_ordem_entrada(prova_db_id, ord_rows)
        print(f"  ordem_entrada → provas.id={prova_db_id}: "
              f"apagados {os_['apagados']}, inseridos {os_['inseridos']}.")
    except Exception as e:                       # tabela nova pode não existir ainda
        print(f"  ⚠ ordem_entrada falhou ({e.__class__.__name__}: {e}). A tabela "
              f"existe? Rode a migração sql/026_ordem_entrada.sql e tente de novo.")
    return 0


def backfill_calendario(args, writer):
    """--backfill: scrapeia o CALENDÁRIO de TODOS os meses de um ANO (--year) via
    navegador, gravando torneios (idempotente por fonte+id_nativo). Rode 1x por ano
    de 2013 até hoje p/ popular o histórico. Depois rode --completar p/ os resultados."""
    import argparse as _ap
    import datetime as _dt2
    ano = args.year or _dt2.date.today().year
    hoje = _dt2.date.today()
    fontes = [SRC.get(args.source)] if args.source else SRC.ativos()
    total = 0
    for source in fontes:
        if source.get("plataforma") != "macronetwork" or not source.get("calendario_url"):
            continue
        for mes in range(1, 13):
            if ano == hoje.year and mes > hoje.month:
                break
            a2 = _ap.Namespace(**vars(args)); a2.year = ano; a2.month = mes
            try:
                evs, modo = coletar(source, a2)
                if args.write:
                    rows = [evento_to_torneio_row(e, source["codigo"]) for e in evs if e["id_nativo"]]
                    g = writer.upsert_torneios(rows)
                    print(f"  {source['codigo']} {ano}-{mes:02d}: {len(evs)} eventos → {len(g)} gravados")
                else:
                    print(f"  {source['codigo']} {ano}-{mes:02d}: {len(evs)} eventos (dry-run)")
                total += len(evs)
            except Exception as e:
                print(f"  ⚠ {source['codigo']} {ano}-{mes:02d}: {e.__class__.__name__}: {e}", file=sys.stderr)
    print(f"=== BACKFILL CALENDÁRIO {ano} === {total} eventos "
          f"({'GRAVADO' if args.write else 'DRY-RUN'}).")
    return 0


def reconciliar_calendario(src, args, writer):
    """--reconcile: casa eventos do calendário AO VIVO (que TRAZEM o id_nativo)
    com os torneios do banco SEM id_nativo (mesma fonte) e backfilla a chave.
    Sem --write é dry-run (mostra os pareamentos pra conferência). Mês atual usa
    requests; --month/--year usa navegador (Playwright)."""
    from scraper import fetch, reconcile
    ano = args.year or _dt.date.today().year
    if args.month:
        paginas = fetch.fetch_calendar_month(src, ano, args.month, headless=not args.headed)
        eventos = []
        for html in paginas:
            eventos += mn.parse_calendar(html, ano, base_url=src["calendario_url"])
        modo = f"playwright({ano}-{args.month:02d})"
    else:
        html = fetch.fetch_calendar_current(src)
        eventos = mn.parse_calendar(html, ano, base_url=src["calendario_url"])
        modo = "requests(mês atual)"
    # dedup eventos ao vivo por id_nativo (pager pode repetir)
    vist, uniq = set(), []
    for e in eventos:
        if e.get("id_nativo") in vist:
            continue
        vist.add(e.get("id_nativo")); uniq.append(e)
    eventos = uniq

    db_rows = writer.torneios_sem_id_nativo(src["codigo"])
    usados = writer.id_nativos_existentes(src["codigo"])
    res = reconcile.casar(eventos, db_rows, id_nativos_usados=usados)
    m = res["matches"]

    print(f"\n══ RECONCILIAÇÃO {src['codigo']} [{modo}] ══")
    print(f"  vivos={len(eventos)} | banco s/ id_nativo={len(db_rows)} | "
          f"matches={len(m)} | vivos sem par={len(res['vivos_sem_match'])} | "
          f"banco sem par(este recorte)={len(res['banco_sem_match'])} | "
          f"pulados(id_nativo já existe)={len(res['pulados_dup'])}")
    for x in m:
        flag = "✓" if x["confianca"] == "alta" else "?"
        print(f"   {flag} {x['data']} id_nativo={str(x['id_nativo']):>6} "
              f"score={x['score']:.2f} | banco: {(x['nome_banco'] or '')[:38]:38} "
              f"⟷ vivo: {(x['nome_vivo'] or '')[:38]}")
    if not args.write:
        print("  (dry-run: nada gravado — use --write para backfillar id_nativo)")
        return 0
    n = 0
    for x in m:
        try:
            writer.set_torneio_id_nativo(x["torneio_id"], x["id_nativo"])
            n += 1
        except Exception as e:
            print(f"   ⚠ falhou torneio_id={x['torneio_id']} id_nativo={x['id_nativo']}: "
                  f"{e.__class__.__name__}: {e}", file=sys.stderr)
    print(f"  ✓ id_nativo backfillado em {n}/{len(m)} torneios.")
    return 0


def completar(args, writer):
    """--completar: completa torneios MacroNetwork INCOMPLETOS (id_nativo já
    setado) de 2024→hoje, em LOTE. Pra cada: detalhar (provas+dia+docs+resultados).
    "Incompleto" = 0 provas OU multi-dia com <=5 provas. Processa até CAVALARIA_MAX
    (default 40) por execução; re-rode p/ continuar (some da lista após o detalhe)."""
    import os
    import datetime as _dt2
    hoje = _dt2.date.today().isoformat()
    MAX = int(os.environ.get("CAVALARIA_MAX", "40"))
    # piso de data configurável (default 2024) — p/ backfill histórico use 2013-01-01
    desde = os.environ.get("CAVALARIA_DESDE", "2024-01-01")
    alvos = []
    for src in [s for s in SRC.ativos() if s["plataforma"] == "macronetwork"]:
        rows = writer._get(
            f"/rest/v1/torneios?fonte=eq.{src['codigo']}&id_nativo=not.is.null"
            f"&data_inicio=gte.{desde}&data_inicio=lte.{hoje}"
            f"&select=id,id_nativo,nome,data_inicio,data_fim,provas(id)"
            f"&order=data_inicio.desc")
        for t in rows:
            np = len(t.get("provas") or [])
            dur = None
            try:
                d0 = _dt2.date.fromisoformat(t["data_inicio"])
                d1 = _dt2.date.fromisoformat(t.get("data_fim") or t["data_inicio"])
                dur = (d1 - d0).days + 1
            except Exception:
                pass
            if np == 0 or (dur and dur >= 2 and np <= 5):
                alvos.append((src, t["id_nativo"], t["id"], np, (t.get("nome") or "")))
    print(f"COMPLETAR: {len(alvos)} torneios incompletos no total; processando até {MAX}.")
    for (src, idn, tid, np, nome) in alvos[:MAX]:
        print(f"\n→ {src['codigo']} torneio {tid} (id_nativo {idn}, {np} provas) {nome[:42]}")
        try:
            detalhar(src, idn, args, writer)
        except Exception as e:
            print(f"  ⚠ erro: {e.__class__.__name__}: {e}", file=sys.stderr)
    print(f"\n=== COMPLETAR === processados {min(len(alvos), MAX)}/{len(alvos)} "
          f"({'COMPLETO' if len(alvos) <= MAX else 'PARCIAL — re-rode p/ continuar'})")
    return 0


def recurar_resultados(args, writer):
    """--recurar: RE-CURA resultados JÁ existentes p/ sanar a DESSINCRONIA causada
    pela renumeração de id_origem no MacroNetwork (metadata da prova de um retrato,
    resultados de outro → ex.: 0,90m colado em rótulo '1,55M'). Pra CADA prova com
    id_origem, relê a página de resultados e REALINHA metadata (altura/nome/data) +
    resultados ao MESMO retrato — SEM ListaProvas (não cria prova nova/duplicada).
    Por fonte (--source) ou todas MacroNetwork. Janela por env CAVALARIA_RECURAR_DESDE/
    _ATE (default 2024-01-01..amanhã); lote CAVALARIA_MAX torneios (re-rode p/ seguir;
    use a janela de datas p/ avançar por períodos). --write grava + refresca a genética."""
    import os
    import datetime as _d
    MAX = int(os.environ.get("CAVALARIA_MAX", "60"))
    desde = os.environ.get("CAVALARIA_RECURAR_DESDE", "2024-01-01")
    ate = os.environ.get("CAVALARIA_RECURAR_ATE",
                         (_d.date.today() + _d.timedelta(days=1)).isoformat())
    fontes = ([SRC.get(args.source)] if args.source
              else [s for s in SRC.ativos() if s["plataforma"] == "macronetwork"])
    fontes = [f for f in fontes if f and f.get("resultados_url")]
    print(f"=== RECURAR (anti-dessincronia) janela [{desde}, {ate}] | lote {MAX} | "
          f"{'GRAVA' if args.write else 'DRY-RUN'} ===")
    tot_t = tot_meta = tot_ins = 0
    for src in fontes:
        rows = writer._get(
            f"/rest/v1/torneios?fonte=eq.{src['codigo']}&id_nativo=not.is.null"
            f"&data_inicio=gte.{desde}&data_inicio=lte.{ate}"
            f"&select=id,nome,data_inicio&order=data_inicio.desc&limit={MAX}")
        print(f"\n{src['codigo']}: {len(rows)} torneios na janela")
        if not (args.write and writer.configured):
            continue
        for t in rows:
            try:
                r = _curar_resultados_torneio(src, t["id"], writer)
            except Exception as e:
                print(f"  ⚠ {t.get('nome','')[:40]}: {e.__class__.__name__}", file=sys.stderr)
                continue
            tot_t += 1; tot_meta += r.get("meta_alinhada", 0); tot_ins += r.get("inseridos", 0)
            if r.get("provas"):
                print(f"  ✓ {(t.get('nome') or '')[:46]}: {r['provas']} provas, "
                      f"{r.get('meta_alinhada', 0)} meta-alinhadas, {r['inseridos']} result.")
    if args.write and writer.configured and tot_ins:
        ok = writer.refresh_genetica()
        print(f"\n  refresh_genetica: {'ok' if ok else 'falhou (rode --refresh-genetica)'}")
    print(f"\n=== RECURAR fim === {tot_t} torneios, {tot_meta} provas realinhadas, "
          f"{tot_ins} resultados ({'GRAVADO' if args.write else 'DRY-RUN'}).")
    return 0


def _curar_ordem_torneio(src, torneio_id, writer):
    """Raspa a ORDEM DE ENTRADA (GET simples) de cada prova do torneio e grava
    (delete+reinsert por prova). Idempotente; parse vazio não apaga. Só faz
    sentido p/ eventos PRÓXIMOS (a ordem sai véspera/manhã)."""
    from scraper import fetch, db as _db
    provas = writer._get(
        f"/rest/v1/provas?torneio_id=eq.{torneio_id}&id_origem=not.is.null&select=id,id_origem")
    n = ins = novas = 0
    for p in provas:
        try:
            O = mn.parse_ordem_entrada(fetch.fetch_ordem_entrada(src, p["id_origem"]))
            if not O:
                continue
            rs = writer.upsert_ordem_entrada(p["id"], [_db.ordem_to_row(o, p["id"]) for o in O])
            ins += rs["inseridos"]; n += 1
            if rs.get("apagados", 0) == 0 and rs.get("inseridos", 0) > 0:
                novas += 1                            # 0→N = ordem publicada pela 1ª vez
        except Exception:
            pass
    return {"provas": n, "inseridos": ins, "novas": novas}


def atualizar_frescor(args, writer):
    """--frescor: refresh LEVE e FREQUENTE (só requests, SEM Playwright/detalhe) de
    ORDEM DE ENTRADA + RESULTADOS dos torneios ACONTECENDO AGORA (data_inicio em
    [hoje-5, hoje+2]). Feito p/ rodar de hora em hora no dia de prova: ordens
    publicadas tarde se preenchem sozinhas, e resultados PARCIAIS (ex.: 11 de 31)
    convergem pro final a cada passada. Re-alinha a metadata da prova de quebra
    (anti-dessincronia). Barato → pode rodar com alta frequência."""
    import datetime as _d
    hoje = _d.date.today()
    ini = (hoje - _d.timedelta(days=5)).isoformat()
    fim = (hoje + _d.timedelta(days=2)).isoformat()
    n_t = n_ord = n_res = 0
    for src in [s for s in SRC.ativos() if s["plataforma"] == "macronetwork"]:
        rows = writer._get(
            f"/rest/v1/torneios?fonte=eq.{src['codigo']}&id_nativo=not.is.null"
            f"&data_inicio=gte.{ini}&data_inicio=lte.{fim}"
            f"&select=id,nome&order=data_inicio.desc")
        for t in rows:
            if not (args.write and writer.configured):
                print(f"  (dry) {src['codigo']} {(t.get('nome') or '')[:44]}")
                continue
            try:
                rr = _curar_resultados_torneio(src, t["id"], writer)
                oo = _curar_ordem_torneio(src, t["id"], writer)
            except Exception as e:
                print(f"  ⚠ {(t.get('nome') or '')[:36]}: {e.__class__.__name__}", file=sys.stderr)
                continue
            n_t += 1; n_res += rr.get("inseridos", 0); n_ord += oo.get("inseridos", 0)
            if rr.get("provas") or oo.get("provas"):
                print(f"  ✓ {(t.get('nome') or '')[:42]}: {rr.get('inseridos', 0)} result., "
                      f"{oo.get('inseridos', 0)} ordem")
    if args.write and writer.configured and n_res:
        writer.refresh_genetica()
    print(f"=== FRESCOR === {n_t} torneios, {n_res} resultados, {n_ord} ordens "
          f"({'GRAVADO' if args.write else 'DRY-RUN'}).")
    return 0


def atualizar_proximos(args, writer):
    """--proximos: mantém FRESCO o que importa pros próximos torneios. Seleciona
    torneios MacroNetwork COM id_nativo na janela [hoje-7, hoje+60] e, pra cada:
    detalhar (provas+dia+docs+resultados) + ordem de entrada. É o coração da
    automação (programa/adendo/horário semanas antes; ordem na véspera/manhã)."""
    import datetime as _d3
    hoje = _d3.date.today()
    ini = (hoje - _d3.timedelta(days=7)).isoformat()
    fim = (hoje + _d3.timedelta(days=60)).isoformat()
    alvos = []
    for src in [s for s in SRC.ativos() if s["plataforma"] == "macronetwork"]:
        rows = writer._get(
            f"/rest/v1/torneios?fonte=eq.{src['codigo']}&id_nativo=not.is.null"
            f"&data_inicio=gte.{ini}&data_inicio=lte.{fim}"
            f"&select=id,id_nativo,nome,data_inicio&order=data_inicio.asc")
        for t in rows:
            alvos.append((src, t["id_nativo"], t["id"], t.get("nome") or ""))
    print(f"PRÓXIMOS: {len(alvos)} torneios na janela [{ini} … {fim}]")
    for (src, idn, tid, nome) in alvos:
        print(f"\n→ {src['codigo']} torneio {tid} (id_nativo {idn}) {nome[:42]}")
        try:
            detalhar(src, idn, args, writer, avisar=args.write)   # +aviso de doc novo
            if args.write:
                ro = _curar_ordem_torneio(src, tid, writer)
                print(f"  ✓ ordem: {ro}")
                if ro.get("novas", 0) > 0:                        # ordem publicada → avisa
                    _inserir_aviso(writer, tid, "ordem", "Ordem de entrada")
        except Exception as e:
            print(f"  ⚠ erro: {e.__class__.__name__}: {e}", file=sys.stderr)
    print(f"\n=== PRÓXIMOS === {len(alvos)} torneios processados")
    return 0


def processar_noticias(args, writer):
    """--noticias: coleta multi-fonte RSS → dedup (url + fingerprint) → reescreve
    com Claude (memória das recentes) → imagem Unsplash → grava. Lote
    CAVALARIA_NEWS (default 60). Sem ANTHROPIC_API_KEY: insere cru (RSS)."""
    import os
    from scraper import news as N
    key = os.environ.get("ANTHROPIC_API_KEY")
    unsplash = os.environ.get("UNSPLASH_ACCESS_KEY")
    MAX = int(os.environ.get("CAVALARIA_NEWS", "60"))

    itens = N.coletar()
    print(f"NOTÍCIAS: {len(itens)} itens em {len(N.FEEDS)} fontes | IA={'on' if key else 'off (cru)'}")
    urls, fps = set(), set()
    if writer.configured:
        off = 0
        while True:
            ch = writer._get(f"/rest/v1/news?select=source_url,event_fingerprint&limit=1000&offset={off}")
            for r in ch:
                if r.get("source_url"):
                    urls.add(r["source_url"])
                if r.get("event_fingerprint"):
                    fps.add(r["event_fingerprint"])
            if len(ch) < 1000:
                break
            off += 1000
    novos = [it for it in itens if it["link"] not in urls]
    print(f"  novos por URL: {len(novos)} | processando até {MAX}")
    memoria = ""
    if writer.configured:
        rec = writer._get("/rest/v1/news?select=title,excerpt&order=created_at.desc&limit=15")
        memoria = "\n".join(f"- {r.get('title','')}: {(r.get('excerpt') or '')[:140]}" for r in rec)

    if not key:
        print("  ⚠ ANTHROPIC_API_KEY ausente — abortando (não gravamos notícia crua).", file=sys.stderr)
        return
    rows = []
    for it in novos[:MAX]:
        rw = N.reescrever(it, N.fetch_artigo(it["link"]), memoria, key)
        if not rw:                    # reescrita falhou → pula (não grava título solto)
            continue
        fp = rw["fingerprint"]
        if fp and fp in fps:          # dedup semântica (mesmo evento de outra fonte)
            continue
        if fp:
            fps.add(fp)
        rows.append({
            "title": rw["titulo"][:300], "excerpt": (rw["resumo"] or "")[:400],
            "body": rw["conteudo"], "body_raw": rw["conteudo"],
            "date": it.get("pubDate", ""), "cat": "hipismo", "featured": False,
            "source_url": it["link"], "image_url": N.imagem_unsplash(unsplash),
            "event_fingerprint": fp,
        })
    print(f"  prontas p/ gravar: {len(rows)}")
    if args.write and writer.configured:
        print("  ", writer.upsert_news(rows))
    else:
        for r in rows[:8]:
            print("   •", r["title"][:72])
    return 0


def processar_shb(args, writer):
    """--shb: ingere RESULTADO POR PROVA do sistema shb.app.br (Scriptcase).
    Lista concursos públicos → por concurso cria/atualiza torneio + provas +
    resultados (classificação geral). Idempotente: upsert torneio/prova por chave
    estável e delete+reinsert de resultados por prova. id_origem da prova é
    sintético e estável (concurso*1000 + posição). CAVALARIA_SHB_MAX limita
    quantos concursos por execução (default: todos do grid público)."""
    import os
    from scraper.adapters import shb
    from scraper.db import prova_to_row, resultado_to_row
    key = os.environ.get("ANTHROPIC_API_KEY")   # habilita fallback de PDF (IA)
    src = SRC.get("SHB")
    token = (src or {}).get("token")
    if not token:
        print("⚠ SHB: token ausente na config.", file=sys.stderr)
        return 3
    try:
        concursos = shb.listar_concursos(token)
    except Exception as e:
        print(f"⚠ SHB: falha ao listar concursos: {e}", file=sys.stderr)
        return 3
    MAX = int(os.environ.get("CAVALARIA_SHB_MAX", str(len(concursos) or 1)))
    print(f"SHB: {len(concursos)} concursos no grid público; processando até {MAX}.")
    tot_prov = tot_res = 0
    for cid in concursos[:MAX]:
        try:
            d = shb.detalhar_concurso(cid)
        except Exception as e:
            print(f"  ⚠ concurso {cid}: {e.__class__.__name__}: {e}", file=sys.stderr)
            continue
        ini, fim = shb.parse_periodo(d["periodo"])
        print(f"\n→ concurso {cid}: {d['nome'][:48]} ({ini}..{fim}) — {len(d['provas'])} provas")
        if not d["provas"]:
            continue                                  # concurso vazio: não cria torneio
        if not (args.write and writer.configured):
            continue
        trow = {"nome": d["nome"], "fonte": "SHB", "data_inicio": ini,
                "data_fim": fim, "id_nativo": str(cid), "organizador": "SHB"}
        rep = writer.upsert_torneios([trow])
        tid = (rep[0]["id"] if rep else None) or writer.find_torneio_id("SHB", str(cid))
        if not tid:
            print("   ⚠ sem torneio_id; pulando", file=sys.stderr)
            continue
        prova_rows = []
        for seq, p in enumerate(d["provas"]):
            alt = p["nome"].split(" - ", 1)[1] if " - " in p["nome"] else None
            prova_rows.append(prova_to_row({
                "id_origem": cid * 1000 + seq, "nome": p["nome"],
                "numero": p["numero"], "categorias": alt,
                "tipo_prova": p.get("tipo_prova"), "data_prova": p.get("data_prova"),
            }, tid))
        writer.upsert_provas(tid, prova_rows)
        tot_prov += len(prova_rows)
        # PDFs "RESULTADO FINAL" (fallback p/ provas sem resultado online)
        pdf_map = {}
        if key:
            try:
                pdf_map = shb.resultado_pdfs(cid)
            except Exception:
                pdf_map = {}
        cres = npdf = 0
        for seq, p in enumerate(d["provas"]):
            pid = writer.find_prova_id(cid * 1000 + seq, fonte="SHB")
            if not pid:
                continue
            try:
                R = shb.parse_resultados(cid, p["codigo"])
            except Exception:
                R = []
            if not R and pdf_map:                  # online vazio → tenta o PDF (IA)
                url = pdf_map.get(shb._provanorm(p["codigo"]))
                if url:
                    R = shb.parse_resultados_pdf(url, key)
                    if R:
                        npdf += 1
            if not R:
                continue
            rs = writer.upsert_resultados(pid, [resultado_to_row(r, pid) for r in R])
            cres += rs.get("inseridos", 0)
        tot_res += cres
        print(f"   ✓ {len(prova_rows)} provas, {cres} resultados"
              + (f" ({npdf} via PDF/IA)" if npdf else ""))
    print(f"\n=== SHB === {tot_prov} provas, {tot_res} resultados. "
          f"{'GRAVADO' if args.write else 'DRY-RUN'}.")
    return 0


def _norm_evento(nome):
    """Normaliza nome de evento p/ chave de dedup: maiúsculas, sem acento, espaços
    colapsados, sem pontuação de borda. (Edições diferentes do mesmo evento ficam
    no MESMO grupo dentro de um ano — ver _evento_key.)"""
    import unicodedata
    if not nome:
        return ""
    s = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", " ", s).upper().strip()
    return re.sub(r"\s+", " ", s)


def _evento_key(evento, data_iso):
    """Chave estável do torneio no backfill: (evento normalizado + ANO). Provas do
    mesmo evento (mesmo nome, mesmo ano) caem no MESMO torneio; edições de anos
    diferentes ficam separadas. id_nativo sintético = 'FPHW-{ano}-{hash8}' (não
    colide com os id_nativo numéricos do calendário)."""
    import hashlib
    ano = (data_iso or "")[:4] or "0000"
    base = _norm_evento(evento)
    h = hashlib.md5(base.encode()).hexdigest()[:8]
    return f"FPHW-{ano}-{h}", base, ano


def _prova_row_de_header(h, torneio_id):
    """Monta a linha de `provas` (via prova_to_row) a partir do cabeçalho
    auto-contido (parse_resultado_pagina). Reconstrói o campo `categorias` no
    formato que _categorias_e_descricao espera ('{altura}m - COD - COD'): assim a
    DESCRIÇÃO preserva a altura (o front extrai '1,30m') e os CÓDIGOS viram chips."""
    alt = h.get("altura_m")
    alt_str = (f"{alt:.2f}".replace(".", ",") + "m") if alt is not None else None
    cods = [c for c in (h.get("categorias") or "").split("/") if c]
    partes = ([alt_str] if alt_str else []) + cods
    categorias_field = " - ".join(partes) or None
    nome = h.get("prova_nome") or alt_str or "Prova"
    return prova_to_row({
        "id_origem": h["_id"],
        "nome": nome,
        "numero": h.get("numero"),
        "categorias": categorias_field,
        "tipo_prova": h.get("tipo_prova"),
        "data_prova": h.get("data"),
    }, torneio_id)


def _gravar_prova_walk(writer, cache, h, resultados, args):
    """Grava UMA prova do backfill: resolve/insere o torneio (dedup por evento+ano,
    cache em memória), faz upsert da prova (id_origem real) e dos resultados
    (mapa canônico). `cache` = {evento_key: {'tid','ini','fim'}}. Retorna nº de
    resultados gravados. Dry-run (sem --write) só conta."""
    from scraper.db import resultado_to_row
    ekey, base, ano = _evento_key(h.get("evento"), h.get("data"))
    data = h.get("data")
    if not (args.write and writer.configured):
        return len(resultados)
    ent = cache.get(ekey)
    if ent is None:
        trow = {"nome": h.get("evento") or base.title(), "fonte": "FPH",
                "data_inicio": data, "data_fim": data,
                "id_nativo": ekey, "organizador": "FPH"}
        rep = writer.upsert_torneios([trow])
        tid = (rep[0]["id"] if rep else None) or writer.find_torneio_id("FPH", ekey)
        ent = {"tid": tid, "ini": data, "fim": data}
        cache[ekey] = ent
    else:
        tid = ent["tid"]
        # alarga a janela de datas do torneio se esta prova é mais cedo/tarde
        novo_ini = min(x for x in (ent["ini"], data) if x) if data else ent["ini"]
        novo_fim = max(x for x in (ent["fim"], data) if x) if data else ent["fim"]
        if (novo_ini, novo_fim) != (ent["ini"], ent["fim"]):
            writer.update_torneio_datas(tid, novo_ini, novo_fim)
            ent["ini"], ent["fim"] = novo_ini, novo_fim
    if not tid:
        return 0
    writer.upsert_provas(tid, [_prova_row_de_header(h, tid)])
    pid = writer.find_prova_id(h["_id"], fonte="FPH")
    if not pid or not resultados:
        return 0
    rs = writer.upsert_resultados(pid, [resultado_to_row(r, pid) for r in resultados])
    return rs.get("inseridos", 0)


def walk_resultados(args, writer):
    """--fph-walk: BACKFILL HISTÓRICO da FPH varrendo Resultados.aspx?ID=N (a página
    é auto-contida: evento+data+prova+ALTURA+categorias+resultados). NÃO depende do
    calendário/ListaProvas. Dedup de torneio por evento+ano; prova por id_origem (N).

    Janela (env, p/ rodar em blocos retomáveis — upserts idempotentes):
      CAVALARIA_WALK_ID_MIN / _MAX   range de IDs a varrer (default 1..14200)
      CAVALARIA_WALK_DESDE / _ATE    só grava provas com data em [desde, ate)
                                     (default 2013-01-01 .. 2024-01-01 — o recente
                                     já é coberto pelo calendário/--completar)
    Duas passadas: (1) requests p/ Tipo A ('tabela', ~70%, já mesclado pelo site) e
    catálogo; (2) Playwright em lote p/ Tipo B ('balao', categorias via postback) →
    mescla num ranking único por barema (regra de negócio do cliente)."""
    import os
    from scraper import fetch
    from scraper.adapters import macronetwork as M
    src = SRC.get("FPH")
    id_min = int(os.environ.get("CAVALARIA_WALK_ID_MIN", "1"))
    id_max = int(os.environ.get("CAVALARIA_WALK_ID_MAX", "14200"))
    desde = os.environ.get("CAVALARIA_WALK_DESDE", "2013-01-01")
    ate = os.environ.get("CAVALARIA_WALK_ATE", "2024-01-01")
    print(f"=== FPH WALK (backfill) IDs {id_min}..{id_max} | datas [{desde}, {ate}) "
          f"| {'GRAVA' if args.write else 'DRY-RUN'} ===")

    cache = {}                       # evento_key -> {tid, ini, fim}
    balao = []                       # [(id, header)] p/ a 2ª passada (Playwright)
    n_tabela = n_vazio = n_fora = n_erro = tot_res = 0

    for idv in range(id_min, id_max + 1):
        try:
            html = fetch.fetch_resultados(src, idv)
        except Exception:
            n_erro += 1
            continue
        h = M.parse_resultado_pagina(html)
        if not h or not h.get("data"):
            n_vazio += 1
            continue
        if not (desde <= h["data"] < ate):     # fora da janela de backfill
            n_fora += 1
            continue
        h["_id"] = idv
        tipo = M.detectar_tipo_resultado(html)
        if tipo == "balao":
            balao.append((idv, h))
            continue
        # 'tabela' (site já mesclou todas as categorias) ou 'vazio' (sem classif.)
        res = M.parse_resultados(html) if tipo == "tabela" else []
        if tipo == "tabela":
            n_tabela += 1
        else:
            n_vazio += 1
        tot_res += _gravar_prova_walk(writer, cache, h, res, args)
        if (n_tabela + n_vazio) % 200 == 0 and (n_tabela + n_vazio) > 0:
            print(f"  …ID {idv}: {n_tabela} tabela, {len(balao)} balão, "
                  f"{n_fora} fora-janela, {tot_res} resultados")

    print(f"\n— Passada 1 (requests): {n_tabela} Tipo A, {len(balao)} Tipo B (balão), "
          f"{n_vazio} vazias, {n_fora} fora-janela, {n_erro} erros HTTP.")

    # ── Passada 2: Tipo B via Playwright (um navegador p/ todas), merge por barema
    if balao:
        print(f"\n— Passada 2 (Playwright): mesclando categorias de {len(balao)} provas…")
        hdr_por_id = {idv: h for idv, h in balao}
        ids = [idv for idv, _ in balao]
        try:
            n_b = 0
            for idv, cats_html in fetch.fetch_resultados_balao_lote(
                    src, ids, headless=not args.headed):
                h = hdr_por_id[idv]
                listas = [M.parse_resultados(html) for _, html in cats_html]
                merged = M.mesclar_resultados(listas, h.get("tipo_prova"))
                tot_res += _gravar_prova_walk(writer, cache, h, merged, args)
                n_b += 1
                if n_b % 50 == 0:
                    print(f"  …balão {n_b}/{len(ids)} (ID {idv}): {len(merged)} conjuntos")
        except ImportError:
            print("  ⚠ Playwright indisponível — Tipo B (balão) pulado. "
                  f"{len(ids)} provas pendentes (rode no CI com navegador).",
                  file=sys.stderr)

    # auto-refresh da genética: o que entrou já vale nos rankings/alturas (sem passo manual)
    if args.write and writer.configured and tot_res:
        ok = writer.refresh_genetica()
        print(f"  refresh_genetica: {'ok' if ok else 'falhou (rode --refresh-genetica)'}")

    print(f"\n=== FPH WALK fim === {len(cache)} torneios, {tot_res} resultados "
          f"({'GRAVADO' if args.write else 'DRY-RUN'}).")
    return 0


def processar_fgee(args, writer):
    """--fgee: resultado POR PROVA da FGEE via LiveHorse (PDF → Claude). Lista
    eventos (mais recentes primeiro) → por evento, cada PDF 'resultado-...pN' →
    extrai texto → Claude estrutura → torneio+prova+resultados. RESUMÁVEL: pula
    prova que já tem resultado (não re-chama Claude). CAVALARIA_FGEE_MAX limita
    PDFs por execução (default 60); CAVALARIA_FGEE_PAGS = páginas de eventos (25/pág)."""
    import os
    from scraper.adapters import livehorse as lh
    from scraper.db import prova_to_row, resultado_to_row
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("⚠ --fgee precisa de ANTHROPIC_API_KEY (estruturação dos PDFs).", file=sys.stderr)
        return 3
    MAXPDF = int(os.environ.get("CAVALARIA_FGEE_MAX", "60"))
    PAGS = int(os.environ.get("CAVALARIA_FGEE_PAGS", "3"))
    if args.write and writer.configured:
        _dedup_fgee_shells(writer)               # limpa shells N8N já superados
    eventos = lh.listar_eventos(max_paginas=PAGS)
    print(f"FGEE/LiveHorse: {len(eventos)} eventos; orçamento {MAXPDF} PDFs/execução.")
    feitos = tot_res = tot_prov = 0
    for ev in eventos:
        if feitos >= MAXPDF:
            print("  (orçamento de PDFs atingido — re-rode p/ continuar)")
            break
        try:
            pdfs = lh.resultado_pdfs(ev["link"])
        except Exception:
            continue
        if not pdfs:
            continue
        # torneio por (fonte=FGEE, id_nativo=id do evento LiveHorse) — chave estável.
        # NÃO reconcilia com shells legados por nome: etapas da mesma série colidem
        # (ordinais são descartados na tokenização) e grudariam resultado na etapa
        # errada. Shells vazios do N8N são tratados à parte.
        tid = None
        if args.write and writer.configured:
            tid = writer.find_torneio_id("FGEE", str(ev["id"]))
            if not tid:
                rep = writer.upsert_torneios([{
                    "nome": ev["nome"], "fonte": "FGEE", "data_inicio": None,
                    "data_fim": None, "id_nativo": str(ev["id"]), "organizador": "FGEE"}])
                tid = (rep[0]["id"] if rep else None) or writer.find_torneio_id("FGEE", str(ev["id"]))
        datas = []
        for url in pdfs:
            if feitos >= MAXPDF:
                break
            oid = lh.id_origem_de(url)
            # resumável: já tem resultado nesta prova? pula (sem Claude)
            if args.write and writer.configured and tid:
                ex = writer._get(f"/rest/v1/provas?torneio_id=eq.{tid}&id_origem=eq.{oid}"
                                 f"&select=id,resultados(id)")
                if ex and (ex[0].get("resultados")):
                    continue
            try:
                texto = lh.extrair_texto_pdf(url)
                est = lh.estruturar_resultado(texto, key)
            except Exception as e:
                print(f"    ⚠ {url.split('/')[-1]}: {e.__class__.__name__}", file=sys.stderr)
                continue
            feitos += 1
            if not est or not est.get("resultados"):
                continue
            dprova = lh.data_iso(est.get("data"))
            if dprova:
                datas.append(dprova)
            numero = re.sub(r"[^\d]", "", str(est.get("prova_numero") or "")) or None
            if not (args.write and writer.configured and tid):
                print(f"  [{ev['id']}] {ev['nome'][:34]} | {url.split('/')[-1][:34]}: "
                      f"{len(est['resultados'])} linhas (dry)")
                continue
            prow = prova_to_row({
                "id_origem": oid, "nome": _clean_prova(est.get("prova_nome")) or f"Prova {numero or ''}".strip(),
                "numero": numero, "categorias": est.get("tabela"),
                "tipo_prova": est.get("tabela"), "data_prova": dprova,
            }, tid)
            writer.upsert_provas(tid, [prow])
            pr = writer._get(f"/rest/v1/provas?torneio_id=eq.{tid}&id_origem=eq.{oid}&select=id")
            if not pr:
                continue
            pid = pr[0]["id"]
            rows = [resultado_to_row(r, pid) for r in est["resultados"]
                    if (r.get("cavaleiro_nome") or r.get("cavalo_nome"))]
            rs = writer.upsert_resultados(pid, rows)
            tot_res += rs.get("inseridos", 0)
            tot_prov += 1
        # datas do torneio = min/max das provas
        if args.write and writer.configured and tid and datas:
            writer._patch(f"/rest/v1/torneios?id=eq.{tid}",
                          {"data_inicio": min(datas), "data_fim": max(datas)})
    print(f"\n=== FGEE === {feitos} PDFs processados, {tot_prov} provas, {tot_res} "
          f"resultados. {'GRAVADO' if args.write else 'DRY-RUN'}.")
    return 0


def _clean_prova(s):
    return re.sub(r"\s+", " ", (s or "").strip()) or None


def processar_spotify_show(args, writer):
    """--spotify-show URL: importa TODOS os episódios de um show do Spotify pra
    tabela `media` (tipo=podcast), já com a tag do programa (--programa, ou o nome
    do show), a capa e a cor da marca. Idempotente: pula episódios já presentes
    (mesma url). Requer SPOTIFY_CLIENT_ID/SECRET."""
    import os
    from scraper.adapters import spotify as sp
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    sec = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not (cid and sec):
        print("⚠ --spotify-show precisa de SPOTIFY_CLIENT_ID e SPOTIFY_CLIENT_SECRET.", file=sys.stderr)
        return 3
    sid = sp.show_id(args.spotify_show)
    try:
        tok = sp.token(cid, sec)
        sh = sp.show(sid, tok)
        eps = sp.episodes(sid, tok)
    except Exception as e:
        print(f"⚠ Spotify: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 3
    nome_show = sh.get("name") or "Podcast"
    logo = ((sh.get("images") or [{}])[0] or {}).get("url")
    programa = (args.programa or nome_show).strip()
    cor = sp.cor_dominante(logo) if logo else None
    print(f"SPOTIFY: '{nome_show}' → {len(eps)} episódios | programa='{programa}' | cor={cor}")
    if not (args.write and writer.configured):
        print("  (dry-run: nada gravado — use --write para persistir)")
        return 0
    # mapa url→id já existente (dedup + backfill da data nos antigos ao re-rodar)
    existentes, off = {}, 0
    while True:
        ch = writer._get(f"/rest/v1/media?tipo=eq.podcast&select=id,url&limit=1000&offset={off}")
        for x in ch:
            if x.get("url"):
                existentes[x["url"]] = x["id"]
        if len(ch) < 1000:
            break
        off += 1000
    mx = writer._get("/rest/v1/media?tipo=eq.podcast&select=ordem&order=ordem.desc&limit=1")
    base = (mx[0]["ordem"] + 1) if mx else 0
    rows, atualizados = [], 0
    for i, e in enumerate(eps):
        u = (e.get("external_urls") or {}).get("spotify")
        if not u:
            continue
        dp = sp.release_to_date(e.get("release_date"))
        if u in existentes:                      # já existe → só completa a data
            if dp:
                writer._patch(f"/rest/v1/media?id=eq.{existentes[u]}", {"data_pub": dp})
                atualizados += 1
            continue
        rows.append({"tipo": "podcast", "url": u, "titulo": e.get("name"),
                     "programa": programa, "cor": cor, "imagem": logo,
                     "data_pub": dp, "ordem": base + i})
    if rows:
        writer._post("/rest/v1/media", rows)
    print(f"  ✓ {len(rows)} episódios novos inseridos, {atualizados} datas atualizadas em '{programa}'.")
    return 0


def processar_abcch(args, writer):
    """--abcch: espelha o studbook da ABCCH (api.abcch.com.br) → tabela
    genealogia. Varre /pesquisa/ por a–z + 0–9, deduplica por CdToken (~46k
    animais com pai/mãe) e faz upsert idempotente. CAVALARIA_ABCCH_CHARS permite
    restringir os caracteres (debug)."""
    import os
    from scraper.adapters import abcch
    chars = os.environ.get("CAVALARIA_ABCCH_CHARS", abcch.CHARS)
    print(f"ABCCH: varrendo studbook ({len(chars)} caracteres)...")
    uni = abcch.varrer_todos(chars=chars)
    rows = [abcch.to_row(a) for a in uni.values() if a.get("CdToken")]
    print(f"\nABCCH: {len(rows)} animais únicos "
          f"(com pai: {sum(1 for r in rows if r['pai'])}, "
          f"com mãe: {sum(1 for r in rows if r['mae'])}).")
    if args.write and writer.configured:
        n = writer.upsert_genealogia(rows)
        print(f"  ✓ {n} gravados/atualizados em genealogia")
    else:
        print("  (dry-run: nada gravado — use --write para persistir)")
    return 0


def _dedup_fgee_shells(writer):
    """Apaga shells legados do N8N (fonte=FGEE, id_nativo nulo, 0 provas) que já
    foram SUPERADOS por um torneio rico (id_nativo setado, com data) de mesmo
    nome (Jaccard≥0.6) e data ±2 dias. DATE-GATED: não confunde etapas diferentes
    da mesma série (1ª vs 3ª etapa têm datas distintas). Só roda no --write."""
    import datetime as _d
    from scraper.reconcile import _tokens, _jaccard
    tor = writer._get("/rest/v1/torneios?fonte=eq.FGEE"
                      "&select=id,nome,data_inicio,id_nativo,provas(id)") or []
    rich = [t for t in tor if t.get("id_nativo") and t.get("data_inicio")]
    shells = [t for t in tor if not t.get("id_nativo") and not (t.get("provas") or [])]

    def _dp(s):
        try:
            return _d.date.fromisoformat(s) if s else None
        except Exception:
            return None
    apagados = 0
    for sh in shells:
        st, sd = _tokens(sh["nome"]), _dp(sh.get("data_inicio"))
        for r in rich:
            rd = _dp(r.get("data_inicio"))
            if sd and rd and abs((sd - rd).days) <= 2 and \
                    _jaccard(st, _tokens(r["nome"])) >= 0.6:
                writer._delete(f"/rest/v1/torneios?id=eq.{sh['id']}")
                apagados += 1
                break
    if apagados:
        print(f"  dedup FGEE: {apagados} shells legados superados apagados")


def estruturar_docs(writer, limit=None):
    """--estruturar: estrutura docs (programa/horário) ainda SEM conteudo_estruturado:
    baixa o PDF → extrai texto → Claude → grava texto_extraido + conteudo_estruturado.
    Lote CAVALARIA_DOCS (default 15); re-rode p/ continuar. Requer ANTHROPIC_API_KEY."""
    import os
    from scraper import estruturar as E
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("⚠ ANTHROPIC_API_KEY ausente — não dá pra estruturar. Abortando.", file=sys.stderr)
        return 3
    lim = limit or int(os.environ.get("CAVALARIA_DOCS", "15"))
    docs = writer.docs_para_estruturar(limit=lim)
    print(f"ESTRUTURAR: {len(docs)} doc(s) a processar (lote {lim})")
    ok = err = 0
    for d in docs:
        try:
            texto = E.extrair_texto_pdf(d["url_pdf"])
            # Um QUADRO DE HORÁRIOS pode chegar rotulado como 'adendo' (ex.: "Quadro
            # atualizado"). Se o título indica horários E o texto é um quadro de
            # verdade (muitos HH:MM), estrutura com o schema 'horarios' (dias) — assim
            # o app mostra com os acordeões como "Quadro de Horários Atualizado".
            tipo_efetivo = d["tipo"]
            if tipo_efetivo != "horarios":
                titulo = (d.get("titulo") or "")
                if re.search(r"quadro|hor[áa]rio", titulo, re.I) and \
                   len(re.findall(r"\b\d{1,2}[:h]\d{2}\b", texto or "")) >= 5:
                    tipo_efetivo = "horarios"
            estrut = E.estruturar(tipo_efetivo, texto, key)
            writer.set_documento_estruturado(d["id"], texto=texto, estrut=estrut)
            print(f"  ✓ doc {d['id']} [{d['tipo']}"
                  f"{'→horarios' if tipo_efetivo != d['tipo'] else ''}] texto={len(texto)}c "
                  f"estrut={'sim' if estrut else 'não'}")
            ok += 1
        except Exception as e:
            print(f"  ⚠ doc {d['id']}: {e.__class__.__name__}: {e}", file=sys.stderr)
            err += 1
    print(f"=== ESTRUTURAR === ok={ok} erros={err} (re-rode p/ continuar a fila)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Scraper Cavalar.IA (dry-run por padrão)")
    ap.add_argument("--source", help="código da fonte (ex.: FPH). Padrão: todas ativas.")
    ap.add_argument("--current", action="store_true",
                    help="mês atual via requests (sem navegador).")
    ap.add_argument("--year", type=int, help="ano (ddlAno).")
    ap.add_argument("--month", type=int, help="mês 1-12 (ddlMes) — usa navegador.")
    ap.add_argument("--headed", action="store_true", help="navegador visível (debug).")
    ap.add_argument("--write", action="store_true",
                    help="GRAVA no Supabase (exige SUPABASE_URL+SUPABASE_SERVICE_KEY).")
    ap.add_argument("--dump-detail", type=str, metavar="ID", dest="dump_detail",
                    help="DEBUG/Fase B: captura o detalhe (provas+docs via navegador) "
                         "do torneio ID e imprime o HTML do upCard. Não grava. Use no "
                         "CI com um torneio CONCLUÍDO pra gerar a fixture do parser.")
    ap.add_argument("--detail", type=str, metavar="ID",
                    help="Fase B: visita o detalhe do torneio ID (navegador), parseia "
                         "provas+documentos e, com --write, faz upsert FK-safe. Sem "
                         "--write é dry-run (imprime o que gravaria). Exige que o "
                         "torneio já exista em `torneios` (rode o calendário antes).")
    ap.add_argument("--prova", type=str, metavar="ID",
                    help="Fase C: lê RESULTADOS + ORDEM DE ENTRADA da prova ID "
                         "(id_origem, GET simples — sem navegador) e imprime. Com "
                         "--write resolve provas.id e grava (resultados no mapa "
                         "canônico + ordem_entrada). Exige a migração sql/026 e que a "
                         "prova já exista em `provas` (rode --detail do torneio antes).")
    ap.add_argument("--reconcile", action="store_true",
                    help="Reconcilia id_nativo: casa eventos do calendário ao vivo "
                         "(mês atual via requests; --month/--year via navegador) com "
                         "torneios do banco SEM id_nativo (mesma fonte) e backfilla. "
                         "Dry-run sem --write. Destrava docs/ordens e o --write do "
                         "calendário (sem duplicar).")
    ap.add_argument("--completar", action="store_true",
                    help="Completa torneios MacroNetwork incompletos (id_nativo setado) "
                         "de 2024→hoje em lote: detalhe (provas+dia+docs) + resultados. "
                         "Lote de CAVALARIA_MAX (default 40); re-rode p/ continuar. "
                         "--write grava.")
    ap.add_argument("--backfill", action="store_true",
                    help="BACKFILL do CALENDÁRIO: scrapeia os 12 meses de um ANO "
                         "(--year) via navegador, gravando torneios. Rode 1x por ano "
                         "(ex.: 2013..hoje) p/ popular o histórico. Combine c/ "
                         "--source FPH --write. Depois use --completar (CAVALARIA_DESDE).")
    ap.add_argument("--fph-walk", action="store_true", dest="fph_walk",
                    help="BACKFILL HISTÓRICO da FPH varrendo Resultados.aspx?ID=N "
                         "(página auto-contida: evento+data+prova+ALTURA+resultados). "
                         "Não usa calendário. Janela por env CAVALARIA_WALK_ID_MIN/_MAX "
                         "e CAVALARIA_WALK_DESDE/_ATE. Tipo A via requests; Tipo B "
                         "(categorias/balão) via navegador + merge por barema. --write grava.")
    ap.add_argument("--recurar", action="store_true",
                    help="RE-CURA resultados existentes p/ sanar dessincronia por "
                         "renumeração de id_origem (MacroNetwork): relê a página de "
                         "cada prova e realinha metadata (altura/nome) + resultados. "
                         "Janela env CAVALARIA_RECURAR_DESDE/_ATE; lote CAVALARIA_MAX. "
                         "--write grava + refresca a genética.")
    ap.add_argument("--frescor", action="store_true",
                    help="Refresh LEVE e FREQUENTE (só requests) de ORDEM + RESULTADOS "
                         "dos torneios acontecendo agora (data em [hoje-5,+2]). Pra "
                         "rodar de hora em hora: ordem zerada se preenche e resultado "
                         "parcial vira final. --write grava.")
    ap.add_argument("--proximos", action="store_true",
                    help="FRESCOR dos próximos torneios (janela [hoje-7, hoje+60], "
                         "MacroNetwork c/ id_nativo): detalhe (provas+dia+docs) + "
                         "ordem de entrada + resultados. É o que o cron roda. --write grava.")
    ap.add_argument("--estruturar", action="store_true",
                    help="Estrutura docs (programa/horário) sem conteudo_estruturado: "
                         "PDF→texto→Claude→grava. Lote CAVALARIA_DOCS (default 15). "
                         "Requer ANTHROPIC_API_KEY.")
    ap.add_argument("--noticias", action="store_true",
                    help="Coleta notícias (Google News RSS pt-BR de hipismo) e insere as "
                         "NOVAS na tabela news (dedup por source_url). --write grava.")
    ap.add_argument("--shb", action="store_true",
                    help="Ingere resultado POR PROVA do sistema shb.app.br (concursos "
                         "públicos → torneios+provas+resultados). --write grava.")
    ap.add_argument("--fgee", action="store_true",
                    help="Ingere resultado POR PROVA da FGEE via LiveHorse (PDF→Claude). "
                         "Requer ANTHROPIC_API_KEY. --write grava.")
    ap.add_argument("--abcch", action="store_true",
                    help="Espelha o studbook genealógico da ABCCH (pai/mãe) na tabela "
                         "genealogia. --write grava.")
    ap.add_argument("--refresh-genetica", action="store_true", dest="refresh_genetica",
                    help="Atualiza a materialized view dos rankings genéticos (rpc).")
    ap.add_argument("--spotify-show", dest="spotify_show", metavar="URL",
                    help="Importa todos os episódios de um show do Spotify pra media. --write grava.")
    ap.add_argument("--programa", help="Nome do programa/aba (default: nome do show).")
    args = ap.parse_args(argv)

    # Fase B: captura de detalhe pra inspeção (gera a fixture do parser no CI).
    if args.dump_detail:
        src = SRC.get(args.source) if args.source else SRC.get("FPH")
        if not src:
            print(f"--dump-detail: fonte inválida: {args.source}", file=sys.stderr)
            return 2
        from scraper import fetch
        d = fetch.fetch_detail(src, args.dump_detail, headless=not args.headed)
        print(f"\n══ DETALHE {src['codigo']} ID={args.dump_detail} "
              f"({src['detalhe_url'].format(id=args.dump_detail)}) ══")
        # provas_days = um snapshot do upCard por dia EXPANDIDO (as provas reais).
        # *_page_html = página inteira (os PDFs de docs moram aí; postback full-nav).
        diag = d.get("provas_diag") or []
        print(f"\n──────── provas_diag ({len(diag)} dia(s) visitado(s)) ────────")
        for row in diag:
            print(f"   {row}")
        days = d.get("provas_days") or []
        for i, h in enumerate(days):
            h = h or ""
            print(f"\n──────── BEGIN provas_day{i} ({len(h)} chars) ────────")
            print(h)
            print(f"──────── END provas_day{i} ────────")
        for k in ("provas_html", "provas_page_html", "docs_html", "docs_page_html"):
            h = d.get(k) or ""
            print(f"\n──────── BEGIN {k} ({len(h)} chars) ────────")
            print(h)
            print(f"──────── END {k} ────────")
        print("\n(dump-detail: nada gravado. Cole o HTML acima numa fixture "
              "scraper/tests/fixtures/ pra travar parse_provas/parse_documentos.)")
        return 0

    # Fase B: parse+upsert do detalhe de UM torneio (provas + documentos).
    if args.detail:
        src = SRC.get(args.source) if args.source else SRC.get("FPH")
        if not src:
            print(f"--detail: fonte inválida: {args.source}", file=sys.stderr)
            return 2
        writer = SupabaseWriter()
        if args.write and not writer.configured:
            print("⚠ --write pedido mas SUPABASE_URL/SUPABASE_SERVICE_KEY ausentes. "
                  "Abortando (nada gravado).", file=sys.stderr)
            return 3
        return detalhar(src, args.detail, args, writer)

    # Fase C: resultados + ordem de entrada de UMA prova (dry-run, GET simples).
    if args.prova:
        src = SRC.get(args.source) if args.source else SRC.get("FPH")
        if not src:
            print(f"--prova: fonte inválida: {args.source}", file=sys.stderr)
            return 2
        if not src.get("resultados_url"):
            print(f"--prova: fonte {src['codigo']} sem resultados_url configurado.",
                  file=sys.stderr)
            return 2
        return inspecionar_prova(src, args.prova, args)

    # Reconciliação do id_nativo (calendário ao vivo ⟷ torneios legados do banco).
    if args.reconcile:
        if args.source:
            s = SRC.get(args.source)
            if not s:
                print(f"--reconcile: fonte desconhecida: {args.source}", file=sys.stderr)
                return 2
            fontes = [s]
        else:
            fontes = SRC.ativos()
        writer = SupabaseWriter()
        if not writer.configured:
            print("⚠ --reconcile precisa de SUPABASE_URL/SUPABASE_SERVICE_KEY "
                  "(lê torneios e grava id_nativo). Abortando.", file=sys.stderr)
            return 3
        for src in fontes:
            if src.get("plataforma") != "macronetwork" or not src.get("calendario_url"):
                continue
            reconciliar_calendario(src, args, writer)
        return 0

    # Backfill do calendário: 12 meses de um ano (histórico).
    if args.backfill:
        writer = SupabaseWriter()
        if args.write and not writer.configured:
            print("⚠ --backfill --write precisa de SUPABASE_URL/SUPABASE_SERVICE_KEY.", file=sys.stderr)
            return 3
        return backfill_calendario(args, writer)

    # Completar torneios incompletos (detalhe + resultados) em lote.
    if args.completar:
        writer = SupabaseWriter()
        if args.write and not writer.configured:
            print("⚠ --completar --write precisa de SUPABASE_URL/SUPABASE_SERVICE_KEY.",
                  file=sys.stderr)
            return 3
        return completar(args, writer)

    # Backfill histórico da FPH varrendo Resultados.aspx?ID=N (auto-contido).
    if args.fph_walk:
        writer = SupabaseWriter()
        if args.write and not writer.configured:
            print("⚠ --fph-walk --write precisa de SUPABASE_URL/SUPABASE_SERVICE_KEY.",
                  file=sys.stderr)
            return 3
        return walk_resultados(args, writer)

    # Re-cura resultados existentes (sana dessincronia por renumeração de id_origem).
    if args.recurar:
        writer = SupabaseWriter()
        if args.write and not writer.configured:
            print("⚠ --recurar --write precisa de SUPABASE_URL/SUPABASE_SERVICE_KEY.",
                  file=sys.stderr)
            return 3
        return recurar_resultados(args, writer)

    # Refresh leve e frequente (ordem + resultados dos torneios de hoje).
    if args.frescor:
        writer = SupabaseWriter()
        if args.write and not writer.configured:
            print("⚠ --frescor --write precisa de SUPABASE_URL/SUPABASE_SERVICE_KEY.",
                  file=sys.stderr)
            return 3
        return atualizar_frescor(args, writer)

    # Frescor dos próximos torneios (docs + ordem + resultados) — o que o cron roda.
    if args.proximos:
        writer = SupabaseWriter()
        if args.write and not writer.configured:
            print("⚠ --proximos --write precisa de SUPABASE_URL/SUPABASE_SERVICE_KEY.",
                  file=sys.stderr)
            return 3
        return atualizar_proximos(args, writer)

    # Notícias (multi-fonte RSS + reescrita IA) — reconstrução do feed sem N8N.
    if args.noticias:
        writer = SupabaseWriter()
        if args.write and not writer.configured:
            print("⚠ --noticias --write precisa de SUPABASE_URL/SUPABASE_SERVICE_KEY.",
                  file=sys.stderr)
            return 3
        return processar_noticias(args, writer)

    # SHB — resultado por prova do sistema shb.app.br (não-MacroNetwork).
    if args.shb:
        writer = SupabaseWriter()
        if args.write and not writer.configured:
            print("⚠ --shb --write precisa de SUPABASE_URL/SUPABASE_SERVICE_KEY.",
                  file=sys.stderr)
            return 3
        return processar_shb(args, writer)

    # FGEE — resultado por prova via LiveHorse (PDF→Claude).
    if args.fgee:
        writer = SupabaseWriter()
        if args.write and not writer.configured:
            print("⚠ --fgee --write precisa de SUPABASE_URL/SUPABASE_SERVICE_KEY.",
                  file=sys.stderr)
            return 3
        return processar_fgee(args, writer)

    # ABCCH — espelha o studbook genealógico (pai/mãe) na tabela genealogia.
    if args.abcch:
        writer = SupabaseWriter()
        if args.write and not writer.configured:
            print("⚠ --abcch --write precisa de SUPABASE_URL/SUPABASE_SERVICE_KEY.",
                  file=sys.stderr)
            return 3
        return processar_abcch(args, writer)

    # Importa um show inteiro do Spotify → media (tipo=podcast).
    if args.spotify_show:
        writer = SupabaseWriter()
        if args.write and not writer.configured:
            print("⚠ --spotify-show --write precisa de SUPABASE_URL/SUPABASE_SERVICE_KEY.",
                  file=sys.stderr)
            return 3
        return processar_spotify_show(args, writer)

    # Atualiza a materialized view dos rankings genéticos (após scrapes do dia).
    if args.refresh_genetica:
        writer = SupabaseWriter()
        if not writer.configured:
            print("⚠ --refresh-genetica precisa de SUPABASE_URL/SUPABASE_SERVICE_KEY.", file=sys.stderr)
            return 3
        import requests as _rq
        r = _rq.post(f"{writer.url}/rest/v1/rpc/refresh_genetica",
                     headers=writer._headers("return=minimal"), data="{}", timeout=180)
        print(f"refresh_genetica: {r.status_code}")
        return 0 if r.ok else 3

    # Estruturar docs (PDF→Claude→conteudo_estruturado) — programa/horário no app.
    if args.estruturar:
        writer = SupabaseWriter()
        if not writer.configured:
            print("⚠ --estruturar precisa de SUPABASE_URL/SUPABASE_SERVICE_KEY.", file=sys.stderr)
            return 3
        return estruturar_docs(writer)

    if args.source:
        s = SRC.get(args.source)
        if not s:
            print(f"fonte desconhecida: {args.source}", file=sys.stderr)
            return 2
        fontes = [s]
    else:
        fontes = SRC.ativos()

    writer = SupabaseWriter()
    if args.write and not writer.configured:
        print("⚠ --write pedido mas SUPABASE_URL/SUPABASE_SERVICE_KEY ausentes. "
              "Abortando (nada foi gravado).", file=sys.stderr)
        return 3

    total = 0
    for source in fontes:
        evs, modo = coletar(source, args)
        imprimir(source, evs, modo)
        total += len(evs)

        if args.write:
            rows = [evento_to_torneio_row(e, source["codigo"]) for e in evs if e["id_nativo"]]
            gravados = writer.upsert_torneios(rows)
            print(f"  ✓ gravados/atualizados em torneios: {len(gravados)}")
        else:
            print("  (dry-run: nada gravado — use --write para persistir)")

    print(f"\nTOTAL: {total} eventos em {len(fontes)} fonte(s). "
          f"{'GRAVADO' if args.write else 'DRY-RUN'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
