"""
Camada de ESCRITA no Supabase (PostgREST), idempotente.

SEGURANÇA:
  • Usa a SERVICE_ROLE key (escrita, ignora RLS). Ela vem SÓ de variável de
    ambiente (SUPABASE_SERVICE_KEY) — NUNCA fica no código nem no frontend.
    Em produção: GitHub Secrets. Local: export antes de rodar com --write.
  • Sem env key configurada, o writer recusa escrever (modo dry-run força).

IDEMPOTÊNCIA:
  • torneios: upsert por (fonte, id_nativo) — a chave estável criada na
    migração 025. Rodar o scraper N vezes não duplica: atualiza no lugar.
  • fingerprint NÃO é gravado pelo scraper de propósito: o esquema legado é
    ambíguo e a dedup canônica (sql/024_resolver) usa nome normalizado +
    sobreposição de datas, não fingerprint. id_nativo é a chave nova.
"""
import os
import re
import json
import datetime as _dt
import requests

TIMEOUT = 30


def _norm_url(u):
    """Canoniza a URL do PDF p/ casar com o que o chatbot já gravou no banco:
    colapsa '//' no caminho (a FPH emite '/sportmanager//uploads/...') e troca
    espaço por %20 (o href cru vem com espaço; o banco guarda %20). Sem isso o
    upsert de documentos não casa por url_pdf e DUPLICA — perdendo o vínculo com
    o texto_extraido/conteudo_estruturado que o chatbot já produziu. Idempotente."""
    if not u:
        return u
    u = u.strip()
    scheme, sep, rest = u.partition("://")
    u = (scheme + sep + re.sub(r"/{2,}", "/", rest)) if sep else re.sub(r"/{2,}", "/", u)
    return u.replace(" ", "%20")


def _url_key(u):
    """Chave canônica p/ DEDUP de PDFs: DECODIFICA o percent-encoding (%C2%AA == ª,
    %20 == espaço) e colapsa '//' — assim a MESMA URL em encodings diferentes casa
    (senão um doc com acento no nome vira 2). Usada só pra COMPARAR; o url_pdf
    gravado segue o _norm_url (continua uma URL válida)."""
    if not u:
        return u
    from urllib.parse import unquote
    s = unquote(u.strip())
    scheme, sep, rest = s.partition("://")
    s = (scheme + sep + re.sub(r"/{2,}", "/", rest)) if sep else re.sub(r"/{2,}", "/", s)
    return re.sub(r"\s+", " ", s).strip().lower()


class SupabaseWriter:
    def __init__(self, url=None, key=None):
        self.url = (url or os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.key = key or os.environ.get("SUPABASE_SERVICE_KEY") or ""

    @property
    def configured(self):
        return bool(self.url and self.key)

    def _headers(self, prefer):
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": prefer,
        }

    def _require(self):
        if not self.configured:
            raise RuntimeError(
                "Supabase não configurado: defina SUPABASE_URL e "
                "SUPABASE_SERVICE_KEY no ambiente (ou rode em --dry-run)."
            )

    # ── helpers HTTP (PostgREST) ─────────────────────────────────────
    def _get(self, path):
        """GET PostgREST → JSON (lista). Usado pra ler o estado atual antes do
        upsert manual (casamento por chave nativa em Python)."""
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}"}
        r = requests.get(self.url + path, headers=h, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path, rows, return_repr=False):
        prefer = "return=representation" if return_repr else "return=minimal"
        r = requests.post(self.url + path, headers=self._headers(prefer),
                          data=json.dumps(rows), timeout=TIMEOUT)
        r.raise_for_status()
        return r.json() if return_repr else None

    def _patch(self, path, patch):
        r = requests.patch(self.url + path, headers=self._headers("return=minimal"),
                           data=json.dumps(patch), timeout=TIMEOUT)
        r.raise_for_status()

    def _delete(self, path, return_repr=False):
        """DELETE PostgREST. Com return_repr=True devolve as linhas apagadas
        (pra contar quantas saíram — útil no delete+reinsert de resultados)."""
        prefer = "return=representation" if return_repr else "return=minimal"
        r = requests.delete(self.url + path, headers=self._headers(prefer),
                            timeout=TIMEOUT)
        r.raise_for_status()
        return r.json() if return_repr else None

    def upsert_genealogia(self, rows, chunk=1000):
        """Upsert em `genealogia` por cd_token (PK → ON CONFLICT nativo funciona).
        Grava em lotes; return=minimal (a tabela tem ~46k linhas). Devolve o total
        enviado."""
        self._require()
        rows = [r for r in rows if r.get("cd_token")]
        if not rows:
            return 0
        enviados = 0
        for i in range(0, len(rows), chunk):
            lote = rows[i:i + chunk]
            r = requests.post(
                f"{self.url}/rest/v1/genealogia?on_conflict=cd_token",
                headers=self._headers("resolution=merge-duplicates,return=minimal"),
                data=json.dumps(lote), timeout=TIMEOUT)
            r.raise_for_status()
            enviados += len(lote)
        return enviados

    # ── torneios (Fase A: calendário) ────────────────────────────────
    def find_torneio_id(self, fonte, id_nativo):
        """Resolve torneios.id por (fonte, id_nativo) — a chave estável da
        migração 025. None se o torneio ainda não existe (a passada de
        calendário precisa rodar antes). É o FK de provas/torneio_documentos."""
        self._require()
        rows = self._get(
            f"/rest/v1/torneios?fonte=eq.{fonte}&id_nativo=eq.{id_nativo}&select=id")
        return rows[0]["id"] if rows else None

    # ── reconciliação do id_nativo (legado N8N sem a chave nativa) ────
    def torneios_sem_id_nativo(self, fonte):
        """Torneios da fonte SEM id_nativo (legado) — alvos da reconciliação.
        Devolve [{id, nome, data_inicio, data_fim}] ordenado por data."""
        self._require()
        return self._get(
            f"/rest/v1/torneios?fonte=eq.{fonte}&id_nativo=is.null"
            f"&select=id,nome,data_inicio,data_fim&order=data_inicio.asc")

    def id_nativos_existentes(self, fonte):
        """Conjunto dos id_nativo JÁ usados pela fonte (pra não duplicar a chave
        ao reconciliar — se o id_nativo já existe, a linha legada é dup a fundir,
        não a casar)."""
        self._require()
        rows = self._get(
            f"/rest/v1/torneios?fonte=eq.{fonte}&id_nativo=not.is.null&select=id_nativo")
        return {str(r["id_nativo"]) for r in rows}

    def set_torneio_id_nativo(self, torneio_id, id_nativo):
        """Backfilla o id_nativo de UM torneio existente (reconciliação)."""
        self._require()
        self._patch(f"/rest/v1/torneios?id=eq.{torneio_id}", {"id_nativo": id_nativo})

    def atualizar_prova_meta(self, prova_id, prova_row):
        """Alinha a METADATA da prova (nome/altura/descrição/data/tipo) ao retrato
        ATUAL da página de resultados. CRÍTICO: o MacroNetwork RENUMERA o
        Resultados.aspx?ID ao longo do tempo → a metadata (lida via ListaProvas num
        momento) podia ficar de uma prova e os resultados (lidos via Resultados.aspx
        depois) de OUTRA (ex.: resultados de 0,90m colados num rótulo '1,55M'). Ao
        gravar resultados, realinhamos a prova ao MESMO retrato. Só sobrescreve
        colunas não-nulas (não apaga dado existente). Idempotente."""
        self._require()
        patch = {k: prova_row[k] for k in self._PROVA_PATCH
                 if k in prova_row and prova_row[k] is not None}
        if patch:
            self._patch(f"/rest/v1/provas?id=eq.{prova_id}", patch)

    def refresh_genetica(self):
        """Recompila a materialized view dos rankings/alturas (rpc refresh_genetica).
        Chamado ao FIM dos fluxos que gravam resultados (backfill/próximos) p/ que o
        dado novo já entre na genética sem passo manual. Tolerante a falha."""
        if not self.configured:
            return False
        try:
            r = requests.post(f"{self.url}/rest/v1/rpc/refresh_genetica",
                              headers=self._headers("return=minimal"), data="{}", timeout=180)
            return r.ok
        except Exception:
            return False

    def update_torneio_datas(self, torneio_id, data_inicio, data_fim):
        """Alarga a janela data_inicio/data_fim de um torneio (backfill por prova:
        descobrimos as datas reais do evento conforme varremos as provas)."""
        self._require()
        patch = {}
        if data_inicio:
            patch["data_inicio"] = data_inicio
        if data_fim:
            patch["data_fim"] = data_fim
        if patch:
            self._patch(f"/rest/v1/torneios?id=eq.{torneio_id}", patch)

    def find_prova_id(self, id_origem, fonte=None):
        """Resolve provas.id pelo id_origem (o ID=N de Resultados.aspx?ID=N) — é
        o FK que resultados/ordem_entrada usam. None se a prova ainda não foi
        gravada (rode --detail do torneio antes).

        id_origem é a CHAVE NATIVA da plataforma MacroNetwork e NÃO é única entre
        fontes diferentes (ex.: ID=13602 existe na FPH e na FAH apontando provas
        distintas). Por isso, quando a fonte é conhecida, FILTRA por ela via join
        em torneios.fonte (provas não tem coluna `fonte` própria) — assim um
        --write nunca grava resultados de uma fonte na prova de outra."""
        self._require()
        if fonte:
            rows = self._get(
                f"/rest/v1/provas?id_origem=eq.{id_origem}"
                f"&select=id,torneios!inner(fonte)&torneios.fonte=eq.{fonte}")
        else:
            rows = self._get(f"/rest/v1/provas?id_origem=eq.{id_origem}&select=id")
        return rows[0]["id"] if rows else None

    _TORNEIO_PATCH = ("nome", "data_inicio", "data_fim", "organizador")

    def upsert_torneios(self, rows):
        """
        Upsert idempotente em torneios por (fonte, id_nativo) — MANUAL (find →
        PATCH/POST). NÃO usa ON CONFLICT: o banco não tem constraint único em
        (fonte, id_nativo) (PostgREST devolve 42P10), o que quebrava o upsert
        nativo p/ TODAS as fontes. Aqui resolvemos a chave estável na mão:
        existe → PATCH (preserva torneios.id e os vínculos de provas/docs);
        novo → POST. Dedup intra-lote por (fonte, id_nativo).
        `rows`: [{nome, fonte, data_inicio, data_fim, id_nativo, organizador}].
        Retorna [{id, ...}] (inclui id + id_nativo) p/ ligar provas.
        """
        self._require()
        if not rows:
            return []
        from collections import defaultdict
        # 1) busca existentes por fonte (id_nativo em lote)
        by_fonte = defaultdict(set)
        for r in rows:
            if r.get("id_nativo") is not None and r.get("fonte"):
                by_fonte[r["fonte"]].add(str(r["id_nativo"]))
        existing = {}
        for fonte, idns in by_fonte.items():
            inlist = ",".join('"%s"' % i.replace('"', "") for i in idns)
            got = self._get(
                f"/rest/v1/torneios?fonte=eq.{fonte}&id_nativo=in.({inlist})"
                f"&select=id,id_nativo")
            for g in got:
                existing[(fonte, str(g["id_nativo"]))] = g["id"]
        # 2) PATCH existentes / acumula novos (dedup intra-lote)
        out, novos, vistos = [], [], set()
        for r in rows:
            idn = r.get("id_nativo")
            key = (r.get("fonte"), str(idn)) if idn is not None else None
            tid = existing.get(key) if key else None
            if tid:
                patch = {k: r[k] for k in self._TORNEIO_PATCH if k in r}
                if patch:
                    self._patch(f"/rest/v1/torneios?id=eq.{tid}", patch)
                out.append({"id": tid, **r})
            elif key and key not in vistos:
                vistos.add(key)
                novos.append(r)
        if novos:
            rep = self._post("/rest/v1/torneios", novos, return_repr=True) or []
            out.extend(rep)
        return out

    # ── provas (Fase B) — UPSERT FK-SAFE por (torneio_id, id_origem) ──
    #  DESCOBERTA-CHAVE (recon 3436): cada prova tem ID NATIVO estável no href
    #  de Resultados.aspx?ID=N (= id_origem, coluna real de `provas`). Por isso
    #  NÃO apagamos+reinserimos (isso ORFANARIA resultados, cujo FK aponta pra
    #  provas.id): fazemos UPSERT manual casando id_origem, mantendo provas.id
    #  ESTÁVEL. Provas que sumiram da fonte NÃO são apagadas (preservar
    #  resultados > limpar prova obsoleta). Sem chave nativa (id_origem nulo) a
    #  prova é PULADA — sem ela não dá pra deduplicar com segurança no re-scrape.
    #  Grava só colunas CONFIRMADAS de `provas` (information_schema): nome,
    #  id_origem, numero, descricao, categorias, tipo_prova, data_prova,
    #  dia_semana. (horario/local saem do parser mas NÃO são colunas — o front
    #  popula horario client-side a partir do quadro; mandá-los daria 400.)
    _PROVA_PATCH = ("nome", "numero", "descricao", "categorias",
                    "tipo_prova", "data_prova", "dia_semana")

    @staticmethod
    def _plan_provas_upsert(existentes, provas_rows):
        """Decide quais provas viram PATCH (já existem) e quais são INSERT, por
        (id_origem). NORMALIZA id_origem a str DOS DOIS LADOS: a coluna
        `provas.id_origem` é INTEIRA no banco (PostgREST devolve int), mas o
        parser entrega STRING (do href Resultados.aspx?ID=N). Sem normalizar, o
        dict-lookup int↔str nunca casa → o upsert reinsere tudo e DUPLICA a cada
        scrape (foi a causa do 4+12 no 1º write do 3436). Pura (sem rede) p/
        testar. Devolve (patches=[(prova_id, row)], novas=[row], puladas:int)."""
        id_por_origem = {str(r["id_origem"]): r["id"]
                         for r in existentes if r.get("id_origem") is not None}
        patches, novas, puladas = [], [], 0
        for row in provas_rows:
            oid = row.get("id_origem")
            if oid is None or str(oid).strip() == "":   # sem chave nativa: não dedup
                puladas += 1
                continue
            pid = id_por_origem.get(str(oid))
            if pid is not None:
                patches.append((pid, row))
            else:
                novas.append(row)
        return patches, novas, puladas

    def upsert_provas(self, torneio_id, provas_rows):
        """Upsert FK-safe de provas por (torneio_id, id_origem). `provas_rows`
        já vêm convertidas por prova_to_row. Devolve contagem por ação."""
        self._require()
        if not provas_rows:
            return {"inseridas": 0, "atualizadas": 0, "puladas": 0}
        existentes = self._get(
            f"/rest/v1/provas?torneio_id=eq.{torneio_id}&select=id,id_origem")
        patches, novas, puladas = self._plan_provas_upsert(existentes, provas_rows)
        for pid, row in patches:             # já existe → PATCH (preserva provas.id)
            patch = {k: row[k] for k in self._PROVA_PATCH if k in row}
            self._patch(f"/rest/v1/provas?id=eq.{pid}", patch)
        inseridas = 0
        if novas:
            res = self._post("/rest/v1/provas", novas, return_repr=True)
            inseridas = len(res or [])
        return {"inseridas": inseridas, "atualizadas": len(patches), "puladas": puladas}

    # ── documentos (Fase B) — UPSERT por url_pdf, PRESERVANDO o que o chatbot
    #  extraiu. torneio_documentos tem campos que NÃO são do scraper:
    #  texto_extraido / texto_extraido_em / conteudo_estruturado / estruturado_em
    #  (pipeline do chatbot). O upsert casa por url_pdf e dá PATCH só nos campos
    #  do scraper (tipo, titulo, data_publicacao, visto_em), nunca tocando os
    #  extraídos. Doc novo entra com criado_em+visto_em = agora.
    _DOC_PATCH = ("tipo", "titulo", "data_publicacao")

    def upsert_documentos(self, torneio_id, docs_rows):
        """Upsert de documentos por url_pdf, preservando texto_extraido/
        conteudo_estruturado (consumidos pelo chatbot). `docs_rows` já vêm
        convertidas por documento_to_row. Devolve contagem por ação."""
        self._require()
        if not docs_rows:
            return {"inseridos": 0, "atualizados": 0}
        existentes = self._get(
            f"/rest/v1/torneio_documentos?torneio_id=eq.{torneio_id}&select=id,url_pdf")
        # casa por url CANÔNICA (_norm_url): o banco pode ter %20/single-slash e o
        # scraper espaço/'//'. Sem normalizar, não casa e duplica — perdendo o
        # texto_extraido do chatbot, que vive no doc já existente.
        id_por_url = {_url_key(r["url_pdf"]): r["id"]
                      for r in existentes if r.get("url_pdf")}
        agora = _dt.datetime.now(_dt.timezone.utc).isoformat()
        novos, atualizados = [], 0
        for row in docs_rows:
            url = _url_key(row.get("url_pdf"))
            if not url:
                continue
            doc_id = id_por_url.get(url)
            if doc_id is not None:           # já existe → PATCH (não toca extraídos)
                patch = {k: row[k] for k in self._DOC_PATCH if k in row}
                patch["visto_em"] = agora
                self._patch(
                    f"/rest/v1/torneio_documentos?id=eq.{doc_id}", patch)
                atualizados += 1
            else:
                novos.append({**row, "visto_em": agora, "criado_em": agora})
        inseridos = 0
        if novos:
            res = self._post("/rest/v1/torneio_documentos", novos, return_repr=True)
            inseridos = len(res or [])
        return {"inseridos": inseridos, "atualizados": atualizados}

    def docs_para_estruturar(self, limit=15):
        """Docs (programa/horarios/adendo) SEM conteudo_estruturado.
        [{id, tipo, url_pdf}] — alvo do passo de estruturação (PDF→texto→Claude)."""
        self._require()
        return self._get(
            "/rest/v1/torneio_documentos?select=id,tipo,url_pdf&url_pdf=not.is.null"
            "&tipo=in.(programa,horarios,adendo)&conteudo_estruturado=is.null"
            f"&order=id.desc&limit={limit}")

    def set_documento_estruturado(self, doc_id, texto=None, estrut=None):
        """Grava texto_extraido / conteudo_estruturado (+ timestamps) de um doc."""
        self._require()
        agora = _dt.datetime.now(_dt.timezone.utc).isoformat()
        patch = {}
        if texto is not None:
            patch["texto_extraido"] = texto[:200000]
            patch["texto_extraido_em"] = agora
        if estrut is not None:
            patch["conteudo_estruturado"] = estrut
            patch["estruturado_em"] = agora
        if patch:
            self._patch(f"/rest/v1/torneio_documentos?id=eq.{doc_id}", patch)

    def upsert_news(self, rows):
        """Insere notícias NOVAS (dedup por source_url); não toca nas existentes."""
        self._require()
        if not rows:
            return {"inseridos": 0, "vistos": 0}
        existentes, off = set(), 0
        while True:
            chunk = self._get(f"/rest/v1/news?select=source_url&limit=1000&offset={off}")
            existentes |= {r["source_url"] for r in chunk if r.get("source_url")}
            if len(chunk) < 1000:
                break
            off += 1000
        novos = [r for r in rows if r.get("source_url") and r["source_url"] not in existentes]
        if novos:
            self._post("/rest/v1/news", novos)
        return {"inseridos": len(novos), "vistos": len(rows)}

    # ── resultados (Fase C) — DELETE+REINSERT por prova_id ───────────
    #  POR QUE apagar+reinserir (e não upsert como provas): `resultados` é
    #  FOLHA — nada referencia resultados.id (ao contrário de provas, cujo id
    #  é FK de resultados; por isso provas NÃO se apaga). Apagar+reinserir os
    #  resultados de UMA prova é seguro e dá DOIS ganhos:
    #    1) FIDELIDADE: re-raspar = retrato exato da fonte (some linha corrigida,
    #       entra a nova) — sem linhas-fantasma de scrapes antigos.
    #    2) CURA o legado: o pipeline N8N TROCAVA as colunas da FPH (faltas caíam
    #       em `tempo`, equipe em `penalidade`, tempo em `pontos`). Ao re-raspar
    #       uma prova, as linhas erradas são apagadas e reentram no MAPA CANÔNICO
    #       (resultado_to_row). Provas nunca re-raspadas seguem com o legado —
    #       backfill é tarefa à parte.
    #  GUARDA: só apaga se há linhas novas parseadas (parse vazio/erro NÃO zera o
    #  que já existe). Idempotente.
    def upsert_resultados(self, prova_id, resultados_rows):
        """Substitui os resultados da prova (delete+reinsert por prova_id).
        `resultados_rows` já vêm convertidas por resultado_to_row. Devolve
        contagem (apagados x inseridos) — útil pra logar a cura do legado."""
        self._require()
        if not resultados_rows:
            return {"inseridos": 0, "apagados": 0}
        apagados = self._delete(
            f"/rest/v1/resultados?prova_id=eq.{prova_id}", return_repr=True)
        res = self._post("/rest/v1/resultados", resultados_rows, return_repr=True)
        return {"inseridos": len(res or []), "apagados": len(apagados or [])}

    # ── ordem de entrada (Fase C) — DELETE+REINSERT por prova_id ─────
    #  Mesma lógica: a ordem é o retrato da prova num momento; re-raspar
    #  substitui o conjunto inteiro. Tabela nova (sql/026), índice único
    #  (prova_id, ordem) protege contra duplicata.
    def upsert_ordem_entrada(self, prova_id, ordem_rows):
        """Substitui a ordem de entrada da prova (delete+reinsert por prova_id).
        `ordem_rows` já vêm convertidas por ordem_to_row."""
        self._require()
        if not ordem_rows:
            return {"inseridos": 0, "apagados": 0}
        apagados = self._delete(
            f"/rest/v1/ordem_entrada?prova_id=eq.{prova_id}", return_repr=True)
        res = self._post("/rest/v1/ordem_entrada", ordem_rows, return_repr=True)
        return {"inseridos": len(res or []), "apagados": len(apagados or [])}


def evento_to_torneio_row(ev, fonte):
    """Converte um evento parseado (adapters) na linha de torneios."""
    return {
        "nome": ev.get("nome"),
        "fonte": fonte,
        "data_inicio": ev.get("data_inicio"),
        "data_fim": ev.get("data_fim"),
        "id_nativo": ev.get("id_nativo"),
        "organizador": ev.get("organizador_entidade"),
    }


# token de ALTURA (não é categoria): "1,30m", "1.30 m", "0,80M", "1,30"
_ALTURA_TOK = re.compile(r"^\d[.,]\d{2}\s*m?$", re.IGNORECASE)


def _categorias_e_descricao(prova):
    """Separa CATEGORIAS (códigos p/ os chips '/'-split do front) de DESCRIÇÃO
    (texto com altura, de onde o front extrai '1,30m' via alturaPrincipal).

    O parser entrega 'categorias' como o "resto" do nome já deduplicado
    (ex.: 'PR. 01 - 1,30M - JCT - 1,30M - JCT' → '1,30M - JCT'). Aqui:
      • descricao  = esse texto (mantém a ALTURA p/ o front extrair)
      • categorias = só os códigos não-altura, únicos, juntados por '/'
        (contrato do front em resultados.html: categorias.split('/'))
    Ex.: '1,30M - JCT' → descricao='1,30M - JCT', categorias='JCT'."""
    base = (prova.get("categorias") or "").strip()
    segs = [s.strip() for s in base.split(" - ") if s.strip()]
    descricao = " - ".join(segs) or None
    codigos, vistos = [], set()
    for s in segs:
        if _ALTURA_TOK.match(s):              # descarta altura (vai p/ descricao)
            continue
        k = s.lower()
        if k not in vistos:
            vistos.add(k)
            codigos.append(s)
    return ("/".join(codigos) or None), descricao


def prova_to_row(prova, torneio_id):
    """Converte uma prova do parser (macronetwork.parse_provas) na linha de
    `provas`. Só colunas CONFIRMADAS; id_origem é a chave do upsert FK-safe.
    numero vira int (o front ordena por ele aritmeticamente)."""
    categorias, descricao = _categorias_e_descricao(prova)
    numero = prova.get("numero")
    try:
        numero = int(numero) if numero is not None else None
    except (TypeError, ValueError):
        pass                                   # mantém o cru se não for numérico
    return {
        "torneio_id": torneio_id,
        "id_origem": prova.get("id_origem"),
        "nome": prova.get("nome"),
        "numero": numero,
        "descricao": descricao,
        "categorias": categorias,
        "tipo_prova": prova.get("tipo_prova"),
        "data_prova": prova.get("data_prova"),
        "dia_semana": prova.get("dia_semana"),
    }


def documento_to_row(doc, torneio_id):
    """Converte um doc do parser (macronetwork.parse_documentos) na linha de
    `torneio_documentos` — só os campos do SCRAPER. Os campos extraídos pelo
    chatbot (texto_extraido/conteudo_estruturado/...) não são tocados aqui;
    o writer os preserva no upsert por url_pdf."""
    return {
        "torneio_id": torneio_id,
        "tipo": doc.get("tipo"),
        "titulo": doc.get("titulo"),
        "url_pdf": _norm_url(doc.get("url_pdf")),
        "data_publicacao": doc.get("data_publicacao"),
    }


def _join_nome(nome, extra):
    """Junta nome + 2ª linha (entidade/genealogia) no formato que o front
    resultados.html espera: nomeCavaleiro/parseCavalo fazem split('\\n')[0] e
    leem a 2ª linha como entidade/genealogia. Sem 2ª linha → só o nome."""
    nome = (nome or "").strip()
    extra = (extra or "").strip()
    if not nome:
        return None
    return f"{nome}\n{extra}" if extra else nome


def resultado_to_row(r, prova_id):
    """Converte um resultado (macronetwork.parse_resultados) na linha de
    `resultados`, no MAPA CANÔNICO que o front espera (resultados.html):
      • tempo        ← TEMPO da 1ª volta ('63,13')          [N8N punha faltas]
      • penalidade   ← FALTAS da 1ª volta ('0'/'Eliminado') [N8N punha equipe]
      • pontos       ← Resultado final/pontos (None no CRONÔMETRO e Desempate)
      • tempo_2      ← TEMPO da 2ª volta/fase  (None em prova de 1 volta)
      • penalidade_2 ← FALTAS da 2ª volta      (None em Duas Fases e 1 volta)
      • equipe       ← nome da equipe, quando houver (provas por equipe)
    cavaleiro_nome='NOME\\nENTIDADE' e cavalo_nome='NOME\\nGENEALOGIA' (o front
    faz split('\\n')[0]). r.get() devolve None para chaves ausentes: provas de
    1 volta (sem penalidade_2/tempo_2/equipe no parser) entram com esses campos
    NULL — comportamento seguro e idêntico ao anterior para CRONÔMETRO."""
    return {
        "prova_id": prova_id,
        "colocacao": r.get("colocacao"),
        "cavaleiro_nome": _join_nome(r.get("cavaleiro_nome"), r.get("entidade")),
        "cavalo_nome": _join_nome(r.get("cavalo_nome"), r.get("cavalo_genealogia")),
        "tempo": r.get("tempo"),
        "penalidade": r.get("penalidade"),
        "pontos": r.get("pontos"),
        "penalidade_2": r.get("penalidade_2"),
        "tempo_2": r.get("tempo_2"),
        "equipe": r.get("equipe"),
    }


def ordem_to_row(o, prova_id):
    """Converte uma linha da ordem de entrada (macronetwork.parse_ordem_entrada)
    na linha de `ordem_entrada` (tabela nova, sql/026). Campos LIMPOS e
    separados (genealogia em coluna própria) — sem o legado de `resultados`."""
    cav = (o.get("cavaleiro_nome") or "").strip()
    cavalo = (o.get("cavalo_nome") or "").strip()
    return {
        "prova_id": prova_id,
        "ordem": o.get("ordem"),
        "cavaleiro_nome": cav or None,
        "cavalo_nome": cavalo or None,
        "genealogia": o.get("cavalo_genealogia"),
        "categoria": o.get("categoria"),
        "pontuacao": o.get("pontuacao"),
        "id_cavaleiro_fonte": o.get("id_cavaleiro_fonte"),
    }
