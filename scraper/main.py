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


def detalhar(src, native_id, args, writer):
    """Fase B: visita o detalhe de UM torneio, parseia provas+documentos e, com
    --write, faz upsert FK-safe. Sem --write é dry-run (imprime o que gravaria).
    Pré-requisito do write: o torneio já existe em `torneios` (a passada de
    calendário cria); resolve torneio_id por (fonte, id_nativo)."""
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
    print(f"  ✓ provas: {rp} | documentos: {rd}")
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
