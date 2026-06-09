// ═══════════════════════════════════════════════════════════════════
// CHATBOT — Supabase Edge Function (Deno)
//
// Conecta o usuário ao Claude com tool use. Claude tem acesso a
// ferramentas que consultam o Supabase e responde em PT-BR.
//
// Deploy:
//   supabase functions deploy chat
//
// Secrets necessários:
//   supabase secrets set ANTHROPIC_API_KEY=sk-ant-...
// ═══════════════════════════════════════════════════════════════════

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.0";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_ANON = Deno.env.get("SUPABASE_ANON_KEY")!;
// service_role é injetado automaticamente nas Edge Functions do Supabase.
// As ferramentas leem `resultados` (cujo SELECT direto é revogado na 050) — por
// isso usam service_role. É seguro: a função inteira é gateada por is_premium().
const SUPABASE_SERVICE = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? SUPABASE_ANON;
const ANTHROPIC_KEY = Deno.env.get("ANTHROPIC_API_KEY")!;

const sb = createClient(SUPABASE_URL, SUPABASE_SERVICE);

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

// ─── HELPERS ────────────────────────────────────────────────────────
function cleanFirstLine(s: string | null | undefined): string {
  if (!s) return "";
  return s.split(/\n/)[0].split(/\s*\|\s*/)[0].trim();
}
function normNome(s: string): string {
  return (s || "").split("\n")[0].normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/\([^)]*\)/g, "").replace(/[^A-Za-z0-9 ]/g, " ").replace(/\s+/g, " ").trim().toUpperCase();
}
function isPenZero(p: any): boolean {
  if (!p) return false;
  return /^0([\s\n(,]|$)/.test(String(p).trim());
}
/** Converte tempo "33,83" | "72,52" | "1:05,42" pra segundos. null se não parsear. */
function tempoParaSegundos(t: any): number | null {
  if (t == null) return null;
  const s = String(t).trim().replace(",", ".");
  const m = s.match(/^(?:(\d+):)?(\d+(?:\.\d+)?)$/);
  if (!m) return null;
  const min = m[1] ? parseInt(m[1], 10) : 0;
  const seg = parseFloat(m[2]);
  if (Number.isNaN(seg)) return null;
  return min * 60 + seg;
}
/** Diferença absoluta entre dois tempos, formatada "0,64s". null se algum não parsear. */
function difTempoStr(t1: any, t2: any): string | null {
  const a = tempoParaSegundos(t1), b = tempoParaSegundos(t2);
  if (a == null || b == null) return null;
  return Math.abs(b - a).toFixed(2).replace(".", ",") + "s";
}
const HEIGHTS = ["1,00M","1,10M","1,20M","1,30M","1,35M","1,40M","1,45M","1,50M","1,55M","1,60M"];

/** Normaliza nome de torneio pra matching tolerante:
 *  remove acentos, prefixos (CSN/CSI), níveis (5*), ano, stopwords, pontuação. */
function normalizarNomeTorneio(s: string): string {
  let n = (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  n = n.replace(/\d+/g, " ");
  n = n.replace(/\b(csn|csi|csiw|csie|ccn|csio|cce|gp|gpr)\b/g, " ");
  n = n.replace(/\b(da|de|do|das|dos|e|em|para|a|o|d)\b/g, " ");
  n = n.replace(/[^a-z ]+/g, " ").replace(/\s+/g, " ").trim();
  return n;
}

/** Match tolerante: retorna true se TODOS os tokens normalizados da query
 *  aparecem no nome normalizado do candidato. */
function matchSmart(candidato: string, query: string): boolean {
  const c = normalizarNomeTorneio(candidato);
  const q = normalizarNomeTorneio(query);
  if (!q) return false;
  const qTokens = q.split(/\s+/).filter(t => t.length >= 2);
  if (qTokens.length === 0) return false;
  return qTokens.every(t => c.includes(t));
}

// ─── DEFINIÇÃO DE TOOLS ─────────────────────────────────────────────
const TOOLS = [
  { name: "buscar_cavaleiro", description: "Busca cavaleiros pelo nome (parcial). Retorna lista com contagem de participações em 2026.",
    input_schema: { type: "object", properties: { termo: { type: "string" }, limit: { type: "number" } }, required: ["termo"] } },
  { name: "buscar_cavalo", description: "Busca cavalos pelo nome.",
    input_schema: { type: "object", properties: { termo: { type: "string" }, limit: { type: "number" } }, required: ["termo"] } },
  { name: "estatisticas_cavaleiro", description: "Estatísticas de UM cavaleiro: % zeros, vitórias, top 6, total provas. Default = ano corrente. Aceita ano específico (ex: 2025, 2024) ou 'todos' pra histórico completo.",
    input_schema: { type: "object", properties: { nome_exato: { type: "string" }, ano: { type: ["number", "string"], description: "Ano específico (2024, 2025, 2026) ou 'todos'. Default = ano corrente." } }, required: ["nome_exato"] } },
  { name: "estatisticas_cavalo", description: "Estatísticas de UM cavalo. Default = ano corrente.",
    input_schema: { type: "object", properties: { nome_exato: { type: "string" }, ano: { type: ["number", "string"], description: "Ano específico ou 'todos'. Default = ano corrente." } }, required: ["nome_exato"] } },
  { name: "top_zeros_consecutivos", description: "Top 10 do ranking de zeros consecutivos por altura.",
    input_schema: { type: "object", properties: { altura: { type: "string" }, entidade: { type: "string", enum: ["cavaleiro", "cavalo"] } }, required: ["altura", "entidade"] } },
  { name: "proximos_eventos", description: "Próximos eventos/torneios (federações + CBH).",
    input_schema: { type: "object", properties: { dias: { type: "number" }, limit: { type: "number" } } } },
  { name: "buscar_torneio", description: "Busca torneios por nome.",
    input_schema: { type: "object", properties: { termo: { type: "string" }, ano: { type: "number" } }, required: ["termo"] } },
  { name: "resultados_recentes", description: "Últimos resultados de um cavaleiro ou cavalo.",
    input_schema: { type: "object", properties: { entidade: { type: "string", enum: ["cavaleiro", "cavalo"] }, nome_exato: { type: "string" }, limit: { type: "number" } }, required: ["entidade", "nome_exato"] } },
  { name: "vencedor_torneio", description: "Retorna o vencedor E o 2º lugar da prova principal (Grande Prêmio / Copa Ouro / maior altura) de um torneio, COM o tempo decisivo (tempo do desempate/2ª volta quando houver) e a diferença de tempo pro 2º lugar. Aceita nome PARCIAL ou com variação (ex: 'CSN d maio', 'aniversário SHC', 'aachen'). Se não passar ano, pega o mais recente.",
    input_schema: { type: "object", properties: { termo: { type: "string", description: "Nome ou parte do nome do torneio" }, ano: { type: "number", description: "Ano específico (opcional)" } }, required: ["termo"] } },
  { name: "resultado_prova", description: "Retorna resultados (top N) de UMA prova ESPECÍFICA dentro de um torneio, JÁ COM o tempo decisivo do vencedor (tempo_decisivo_vencedor = tempo do desempate/2ª volta quando houver) e a diferença de tempo pro 2º lugar (diferenca_tempo_para_2o). Use quando o usuário menciona o nome da prova: 'Copa Ouro', 'Copa Prata', 'Grande Prêmio', 'PR. 04', etc. Match por keyword no nome da prova.",
    input_schema: { type: "object", properties: {
      torneio_termo: { type: "string", description: "Nome ou parte do nome do torneio" },
      prova_termo:   { type: "string", description: "Nome ou parte do nome da prova (ex: 'copa ouro', 'gp', 'PR 04', '1,45M')" },
      ano:           { type: "number", description: "Ano (opcional)" },
      limit:         { type: "number", description: "Quantos resultados retornar (default 5, podem pedir até 20)" },
    }, required: ["torneio_termo", "prova_termo"] } },
  { name: "buscar_em_documentos", description: "Busca um termo dentro do TEXTO EXTRAÍDO dos programas, adendos e quadros de horários de um torneio. Use quando o usuário pergunta sobre regras, premiação, juízes, desenhador de percurso, horários, etc. Retorna trechos (snippets) do documento onde o termo foi encontrado.",
    input_schema: { type: "object", properties: {
      torneio_termo: { type: "string", description: "Nome ou parte do nome do torneio" },
      query: { type: "string", description: "Palavra-chave ou frase a buscar (ex: 'premiação', 'juiz', 'desenhador', 'horário da copa ouro')" },
      tipo_doc: { type: "string", enum: ["programa", "adendo", "horarios", "outros"], description: "Filtrar por tipo (opcional)" },
    }, required: ["torneio_termo", "query"] } },
  // ───────────────── DOCS ESTRUTURADOS (JSONB) ──────────────────
  { name: "programa_torneio", description: "Retorna o PROGRAMA OFICIAL estruturado de um torneio: oficiais (juiz presidente, desenhador, etc), lista de provas com altura/tabela/categoria/data/horário/premiação, regulamento resumido. Use quando o usuário pergunta sobre detalhes do programa: 'quem é o juiz', 'qual a premiação da PR.04', 'que dia é a Copa Ouro', etc.",
    input_schema: { type: "object", properties: {
      torneio_termo: { type: "string", description: "Nome ou parte do nome do torneio" },
    }, required: ["torneio_termo"] } },
  { name: "horarios_torneio", description: "Retorna o QUADRO DE HORÁRIOS estruturado de um torneio: dias com lista de horas+provas. Use quando o usuário pergunta 'que horas começa', 'qual o horário da prova X', 'agenda do dia tal'. Sempre usa a versão MAIS RECENTE do quadro.",
    input_schema: { type: "object", properties: {
      torneio_termo: { type: "string", description: "Nome ou parte do nome do torneio" },
      data: { type: "string", description: "Filtrar por data específica (formato DD/MM ou DD/MM/AAAA). Opcional." },
    }, required: ["torneio_termo"] } },
  { name: "adendos_torneio", description: "Retorna os ADENDOS publicados de um torneio: lista de mudanças (alteração de prova, horário, premiação, regulamento) + resumo executivo. Use quando o usuário pergunta 'teve adendo?', 'mudou algo?', 'última atualização do programa', 'qual a versão atual'.",
    input_schema: { type: "object", properties: {
      torneio_termo: { type: "string", description: "Nome ou parte do nome do torneio" },
    }, required: ["torneio_termo"] } },
  // ───────────────── GENEALOGIA / REPRODUÇÃO / MÍDIA ────────────────
  { name: "genealogia_cavalo", description: "Genealogia de um cavalo na ABCCH: pai, mãe, sexo, nascimento, registro, proprietário e a lista de FILHOS (progênie). Use pra 'quem é o pai/mãe de X', 'quantos filhos tem o garanhão Y', 'filhos da matriz Z'.",
    input_schema: { type: "object", properties: { nome: { type: "string" } }, required: ["nome"] } },
  { name: "rankings_geneticos", description: "Ranking de reprodutores (garanhões/matrizes): nº de filhos, filhos +4 anos competindo, filhos +8 anos saltando >=1,40m (com %). Use pra 'quais garanhões têm mais filhos competindo', 'matriz que mais produz cavalos de 1,40m', 'top reprodutores'.",
    input_schema: { type: "object", properties: { papel: { type: "string", enum: ["pai", "mae"] }, ano: { type: ["number", "string"], description: "ano específico, ou omitir p/ todos" }, ordenar: { type: "string", enum: ["total", "comp", "pct_comp", "m140", "pct140"], description: "total=nº filhos; comp=competindo; pct_comp=% dos +4a que competem; m140=saltam >=1,40; pct140=% dos +8a em >=1,40" } }, required: ["papel"] } },
  { name: "estatisticas_reprodutor", description: "Estatísticas EXATAS de UM reprodutor específico (garanhão ou matriz): total de filhos, filhos +4 anos, filhos competindo, filhos +8 anos, filhos saltando >=1,40m e as %. Use SEMPRE que a pergunta é sobre UM reprodutor nomeado (ex.: 'quantos filhos do Cornet Obolensky saltando 1,40m esse ano', 'quantos filhos da [matriz] competem'). Passe ano quando o usuário disser 'esse ano'/um ano.",
    input_schema: { type: "object", properties: { nome: { type: "string" }, papel: { type: "string", enum: ["pai", "mae"], description: "pai=garanhão, mae=matriz; omita se não souber" }, ano: { type: ["number", "string"], description: "ano (ex.: 2026) ou omita p/ todos" } }, required: ["nome"] } },
  { name: "buscar_noticias", description: "Notícias do portal. Com 'termo' busca por assunto; sem termo, retorna as mais recentes.",
    input_schema: { type: "object", properties: { termo: { type: "string" }, limit: { type: "number" } } } },
  { name: "buscar_podcasts", description: "Episódios de podcast/videocast/videoaula. Filtra por 'termo' (título) e/ou 'programa' (ex.: PodEquestre, Clac Cast). Use pra 'tem podcast sobre X', 'últimos episódios do PodEquestre'.",
    input_schema: { type: "object", properties: { termo: { type: "string" }, programa: { type: "string" }, limit: { type: "number" } } } },
  { name: "ordem_entrada", description: "Ordem de entrada (lista de largada) de UMA prova de um torneio. Use pra 'qual a ordem de entrada da prova X', 'quem larga primeiro'.",
    input_schema: { type: "object", properties: { torneio_termo: { type: "string" }, prova_termo: { type: "string" }, limit: { type: "number" } }, required: ["torneio_termo", "prova_termo"] } },
];

// ─── EXECUTORES ─────────────────────────────────────────────────────
async function tool_buscar_cavaleiro({ termo, limit = 5 }: any) {
  const ano = new Date().getFullYear();
  const { data } = await sb.from("resultados")
    .select("cavaleiro_nome, provas!inner(torneios!inner(data_inicio))")
    .ilike("cavaleiro_nome", `%${termo}%`)
    .gte("provas.torneios.data_inicio", `${ano}-01-01`).limit(2000);
  const counts: Record<string, number> = {};
  for (const r of (data || [])) {
    const n = cleanFirstLine((r as any).cavaleiro_nome);
    if (n) counts[n] = (counts[n] || 0) + 1;
  }
  return Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,limit).map(([nome, participacoes])=>({nome, participacoes}));
}

async function tool_buscar_cavalo({ termo, limit = 5 }: any) {
  const ano = new Date().getFullYear();
  const { data } = await sb.from("resultados")
    .select("cavalo_nome, provas!inner(torneios!inner(data_inicio))")
    .ilike("cavalo_nome", `%${termo}%`)
    .gte("provas.torneios.data_inicio", `${ano}-01-01`).limit(2000);
  const counts: Record<string, number> = {};
  for (const r of (data || [])) {
    const n = cleanFirstLine((r as any).cavalo_nome);
    if (n) counts[n] = (counts[n] || 0) + 1;
  }
  return Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,limit).map(([nome, participacoes])=>({nome, participacoes}));
}

async function _stats(nome: string, campo: "cavaleiro" | "cavalo", ano: number | string | undefined) {
  const col = campo === "cavaleiro" ? "cavaleiro_nome" : "cavalo_nome";
  const todos = (ano === "todos" || ano === "all");
  const anoNum = (!todos && ano) ? parseInt(String(ano)) : new Date().getFullYear();

  let q = sb.from("resultados")
    .select(`id, colocacao, penalidade, prova_id, ${col}, provas!inner(torneios!inner(data_inicio))`)
    .ilike(col, `%${nome}%`).limit(5000);
  if (!todos) {
    q = q.gte("provas.torneios.data_inicio", `${anoNum}-01-01`)
         .lte("provas.torneios.data_inicio", `${anoNum}-12-31`);
  }
  const { data } = await q;

  const filt = (data || []).filter((r:any)=>cleanFirstLine(r[col]).toLowerCase()===nome.toLowerCase());
  const total = filt.length;
  const provas = new Set(filt.map((r:any)=>r.prova_id)).size;
  const zerados = filt.filter((r:any)=>isPenZero(r.penalidade)).length;
  const vitorias = filt.filter((r:any)=>(r.colocacao||"").trim()==="1º").length;
  const top6 = filt.filter((r:any)=>/^[1-6]º$/.test((r.colocacao||"").trim())).length;
  const pct = (n: number) => total>0 ? Math.round((n/total)*100) : 0;
  return {
    periodo: todos ? "todos os anos" : String(anoNum),
    total_participacoes: total, total_provas: provas,
    percursos_zero: zerados, pct_percursos_zero: pct(zerados),
    vitorias, pct_vitorias: pct(vitorias),
    top6, pct_top6: pct(top6),
  };
}
async function tool_estatisticas_cavaleiro({ nome_exato, ano }: any) { return _stats(nome_exato, "cavaleiro", ano); }
async function tool_estatisticas_cavalo({ nome_exato, ano }: any) { return _stats(nome_exato, "cavalo", ano); }

async function tool_top_zeros_consecutivos({ altura, entidade }: any) {
  if (!HEIGHTS.includes(altura)) return { erro: `altura inválida; use uma de: ${HEIGHTS.join(", ")}` };
  const ano = new Date().getFullYear();
  const { data } = await sb.from("resultados")
    .select(`id, cavaleiro_nome, cavalo_nome, penalidade, provas!inner(id, numero, descricao, torneios!inner(data_inicio))`)
    .eq("provas.descricao", altura)
    .gte("provas.torneios.data_inicio", `${ano}-01-01`).limit(5000);
  const arr = (data || []).map((r:any)=>({
    nome: entidade === "cavaleiro" ? cleanFirstLine(r.cavaleiro_nome) : cleanFirstLine(r.cavalo_nome),
    penalidade: r.penalidade,
    sortKey: `${r.provas?.torneios?.data_inicio || ""}|${String(r.provas?.numero || 0).padStart(4,"0")}|${String(r.id).padStart(8,"0")}`,
  }));
  arr.sort((a,b)=>a.sortKey.localeCompare(b.sortKey));
  const porNome: Record<string, any[]> = {};
  for (const r of arr) { if(!r.nome) continue; (porNome[r.nome] ||= []).push(r); }
  const ranked: any[] = [];
  for (const [nome, lst] of Object.entries(porNome)) {
    let best = 0, active = 0;
    for (const r of lst) {
      if (isPenZero(r.penalidade)) active++;
      else { if (active > best) best = active; active = 0; }
    }
    let length, isActive;
    if (active > 0 && active >= best) { length = active; isActive = true; }
    else { length = best; isActive = false; }
    if (length > 0) ranked.push({ nome, length, ativo: isActive });
  }
  ranked.sort((a,b)=>{ if(b.length!==a.length) return b.length-a.length; return (b.ativo?1:0)-(a.ativo?1:0); });
  return ranked.slice(0,10);
}

async function tool_proximos_eventos({ dias = 30, limit = 10 }: any) {
  const hoje = new Date().toISOString().substring(0,10);
  const f = new Date(); f.setDate(f.getDate() + dias);
  const fim = f.toISOString().substring(0,10);
  const [tor, cbh] = await Promise.all([
    sb.from("torneios").select("id, nome, fonte, data_inicio, data_fim, fingerprint").gte("data_inicio", hoje).lte("data_inicio", fim),
    sb.from("eventos_cbh").select("id, evento, federacao, data_inicio, data_fim, local, estado, fingerprint").gte("data_inicio", hoje).lte("data_inicio", fim),
  ]);
  const torFps = new Set((tor.data || []).map((t:any)=>t.fingerprint).filter(Boolean));
  const cbhFilt = (cbh.data || []).filter((c:any)=>!torFps.has(c.fingerprint));
  const all = [
    ...(tor.data || []).map((t:any)=>({ nome: (t.nome||"").split("\n")[0], fonte: t.fonte, data_inicio: t.data_inicio, data_fim: t.data_fim })),
    ...cbhFilt.map((c:any)=>({ nome: (c.evento||"").split("\n")[0], fonte: `CBH (${c.federacao})`, local: c.local, estado: c.estado, data_inicio: c.data_inicio, data_fim: c.data_fim })),
  ];
  all.sort((a,b)=>(a.data_inicio||"").localeCompare(b.data_inicio||""));
  return all.slice(0,limit);
}

async function buscarTorneiosSmart(termo: string, ano?: number) {
  // Carrega vários torneios e filtra com matchSmart no servidor (mais tolerante que ilike)
  let q = sb.from("torneios").select("id, nome, fonte, data_inicio, data_fim").order("data_inicio", { ascending: false }).limit(500);
  if (ano) q = q.gte("data_inicio", `${ano}-01-01`).lte("data_inicio", `${ano}-12-31`);
  const { data } = await q;
  return (data || []).filter((t: any) => matchSmart(t.nome, termo));
}

async function tool_buscar_torneio({ termo, ano }: any) {
  const matched = await buscarTorneiosSmart(termo, ano);
  const top = matched.slice(0, 10);
  const result: any[] = [];
  for (const t of top) {
    const { count: pCount } = await sb.from("provas").select("id", { count: "exact", head: true }).eq("torneio_id", (t as any).id);
    const { count: dCount } = await sb.from("torneio_documentos").select("id", { count: "exact", head: true }).eq("torneio_id", (t as any).id);
    result.push({ id: (t as any).id, nome: (t as any).nome, fonte: (t as any).fonte, data_inicio: (t as any).data_inicio, data_fim: (t as any).data_fim, provas: pCount, documentos: dCount });
  }
  return result;
}

async function tool_vencedor_torneio({ termo, ano }: any) {
  const matched = await buscarTorneiosSmart(termo, ano);
  if (!matched.length) return { erro: `Nenhum torneio encontrado com "${termo}"${ano ? ` em ${ano}` : ""}` };

  // Pega o mais recente
  const t: any = matched[0];

  // Busca todas as provas com altura conhecida
  const { data: provas } = await sb.from("provas")
    .select("id, nome, numero, descricao, tipo_prova")
    .eq("torneio_id", t.id);

  if (!provas || provas.length === 0) {
    return { torneio: t.nome, data: t.data_inicio, fonte: t.fonte, erro: "Torneio ainda sem provas/resultados no banco." };
  }

  // Identifica a "prova principal": preferência por nome com GP/GRANDE/CLÁSSICO/COPA OURO, OU maior altura
  const heightOf = (p: any): number => {
    const m = (p.descricao || "").match(/(\d+),(\d+)M/);
    return m ? parseInt(m[1])*100 + parseInt(m[2]) : 0;
  };
  const isMain = (p: any): boolean => {
    const n = (p.nome || "").toLowerCase();
    return /grande pr[êe]mio|gp\b|copa ouro|cl[áa]ssic[oa]|championship/.test(n);
  };

  const mains = provas.filter(isMain);
  let provaPrincipal: any;
  if (mains.length > 0) {
    provaPrincipal = mains.sort((a, b) => heightOf(b) - heightOf(a))[0];
  } else {
    provaPrincipal = provas.sort((a, b) => heightOf(b) - heightOf(a))[0];
  }

  // Busca o pódio. Inclui tempo_2/penalidade_2: em Desempate/Duas Voltas/Duas
  // Fases é o resultado DECISIVO (o que define o pódio e o quão rápido foi).
  const { data: resultados } = await sb.from("resultados")
    .select("colocacao, cavaleiro_nome, cavalo_nome, tempo, penalidade, tempo_2, penalidade_2")
    .eq("prova_id", provaPrincipal.id)
    .order("id", { ascending: true });

  if (!resultados || resultados.length === 0) {
    return { torneio: t.nome, data: t.data_inicio, prova_principal: provaPrincipal.nome, erro: "Prova sem resultados ainda." };
  }

  const mapPodio = (r: any) => ({
    colocacao: r.colocacao,
    cavaleiro: cleanFirstLine(r.cavaleiro_nome),
    cavalo: cleanFirstLine(r.cavalo_nome),
    penalidade: r.penalidade,
    tempo: r.tempo,
    ...(r.tempo_2 ? { tempo_2: r.tempo_2 } : {}),
    ...(r.penalidade_2 ? { penalidade_2: r.penalidade_2 } : {}),
  });

  const primeiro = resultados.find((r: any) => (r.colocacao || "").trim() === "1º") || resultados[0];
  const segundo  = resultados.find((r: any) => (r.colocacao || "").trim() === "2º");

  // Tempo DECISIVO = o do desempate/2ª volta quando existe; senão o tempo normal.
  const tDecVenc = primeiro.tempo_2 ?? primeiro.tempo;
  const tDecSeg  = segundo ? (segundo.tempo_2 ?? segundo.tempo) : null;
  const difSeg   = segundo ? difTempoStr(tDecVenc, tDecSeg) : null;

  return {
    torneio: t.nome,
    data: t.data_inicio,
    fonte: t.fonte,
    prova_principal: provaPrincipal.nome,
    tipo_prova: provaPrincipal.tipo_prova,
    altura: provaPrincipal.descricao,
    vencedor: mapPodio(primeiro),
    ...(segundo ? { segundo_lugar: mapPodio(segundo) } : {}),
    ...(difSeg ? { diferenca_tempo_para_2o: difSeg } : {}),
    outras_provas_disponiveis: provas.length,
  };
}

async function tool_resultados_recentes({ entidade, nome_exato, limit = 5 }: any) {
  const col = entidade === "cavaleiro" ? "cavaleiro_nome" : "cavalo_nome";
  const { data } = await sb.from("resultados")
    .select(`id, colocacao, cavaleiro_nome, cavalo_nome, penalidade, tempo, prova_id, provas!inner(nome, descricao, torneios!inner(nome, data_inicio))`)
    .ilike(col, `%${nome_exato}%`).order("id", { ascending: false }).limit(200);
  return (data || [])
    .filter((r:any)=>cleanFirstLine(r[col]).toLowerCase()===nome_exato.toLowerCase())
    .slice(0,limit)
    .map((r:any)=>({
      torneio: r.provas?.torneios?.nome, data: r.provas?.torneios?.data_inicio,
      prova: r.provas?.nome, altura: r.provas?.descricao,
      cavaleiro: cleanFirstLine(r.cavaleiro_nome), cavalo: cleanFirstLine(r.cavalo_nome),
      colocacao: r.colocacao, penalidade: r.penalidade, tempo: r.tempo,
    }));
}

async function tool_resultado_prova({ torneio_termo, prova_termo, ano, limit = 5 }: any) {
  const matched = await buscarTorneiosSmart(torneio_termo, ano);
  if (!matched.length) return { erro: `Nenhum torneio encontrado com "${torneio_termo}"` };
  const t: any = matched[0];

  // Busca provas que casam com prova_termo (ilike no nome)
  const { data: provas } = await sb.from("provas")
    .select("id, nome, numero, descricao, tipo_prova")
    .eq("torneio_id", t.id);

  // Normalize prova_termo (sem prefix CSN, acentos, etc) e faz match
  const provaNorm = (prova_termo || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
  const provaTokens = provaNorm.split(/\s+/).filter(tok => tok.length >= 2);

  const provasMatch = (provas || []).filter((p: any) => {
    const nameNorm = (p.nome || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
    return provaTokens.every(tok => nameNorm.includes(tok));
  });

  if (!provasMatch.length) {
    return {
      erro: `Não achei prova com "${prova_termo}" no torneio. Provas disponíveis: ${(provas || []).slice(0, 10).map((p:any) => p.nome).join(" | ")}`,
    };
  }

  // Se múltiplas casam, pega a 1ª (provavelmente é o que o usuário quer)
  const prova: any = provasMatch[0];

  const { data: resultados } = await sb.from("resultados")
    .select("colocacao, cavaleiro_nome, cavalo_nome, tempo, penalidade, tempo_2, penalidade_2")
    .eq("prova_id", prova.id)
    .order("id", { ascending: true });

  if (!resultados || !resultados.length) {
    return { torneio: t.nome, prova: prova.nome, erro: "Prova sem resultados gravados ainda." };
  }

  // Tempo decisivo (tempo_2 quando houver — desempate/2ª volta; senão tempo) +
  // diferença pro 2º. Pré-calculado aqui pra que o modelo NUNCA omita a margem
  // (princípio #2B): é o dado que o público mais quer junto das penalidades.
  const _primeiro = resultados.find((r: any) => (r.colocacao || "").trim() === "1º") || resultados[0];
  const _segundo = resultados.find((r: any) => (r.colocacao || "").trim() === "2º");
  const _tDecVenc = _primeiro?.tempo_2 ?? _primeiro?.tempo;
  const _tDecSeg = _segundo ? (_segundo.tempo_2 ?? _segundo.tempo) : null;
  const _difSeg = _segundo ? difTempoStr(_tDecVenc, _tDecSeg) : null;

  return {
    torneio: t.nome,
    data: t.data_inicio,
    prova: prova.nome,
    tipo_prova: prova.tipo_prova,
    altura: prova.descricao,
    total_resultados: resultados.length,
    ...(_tDecVenc ? { tempo_decisivo_vencedor: _tDecVenc } : {}),
    ...(_difSeg ? { diferenca_tempo_para_2o: _difSeg } : {}),
    top: resultados.slice(0, Math.min(limit, 20)).map((r: any) => ({
      colocacao: r.colocacao,
      cavaleiro: cleanFirstLine(r.cavaleiro_nome),
      cavalo: cleanFirstLine(r.cavalo_nome),
      penalidade: r.penalidade,
      tempo: r.tempo,
      ...(r.tempo_2 ? { tempo_2: r.tempo_2 } : {}),
      ...(r.penalidade_2 ? { penalidade_2: r.penalidade_2 } : {}),
    })),
    outras_provas_matched: provasMatch.length > 1 ? provasMatch.slice(1).map((p: any) => p.nome) : undefined,
  };
}

async function tool_buscar_em_documentos({ torneio_termo, query, tipo_doc }: any) {
  const matched = await buscarTorneiosSmart(torneio_termo);
  if (!matched.length) return { erro: `Nenhum torneio achado com "${torneio_termo}"` };
  const t: any = matched[0];

  let q = sb.from("torneio_documentos")
    .select("id, tipo, titulo, data_publicacao, url_pdf, texto_extraido")
    .eq("torneio_id", t.id)
    .not("texto_extraido", "is", null);
  if (tipo_doc) q = q.eq("tipo", tipo_doc);
  const { data: docs } = await q;

  if (!docs || !docs.length) {
    return { torneio: t.nome, erro: "Nenhum documento com texto disponível pra esse torneio. Pode estar ainda sem programa publicado, ou o PDF não foi extraído." };
  }

  // Busca o termo no texto (case-insensitive). Retorna snippets contextualizados (200 chars antes/depois)
  const queryNorm = query.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  const snippets: any[] = [];
  for (const d of docs) {
    const texto = d.texto_extraido as string;
    const textoNorm = texto.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
    let idx = 0;
    let matches = 0;
    while ((idx = textoNorm.indexOf(queryNorm, idx)) !== -1 && matches < 5) {
      const start = Math.max(0, idx - 150);
      const end = Math.min(texto.length, idx + queryNorm.length + 250);
      snippets.push({
        documento: `${d.titulo} (${d.tipo})`,
        publicado_em: d.data_publicacao,
        url_pdf: d.url_pdf,
        trecho: "..." + texto.substring(start, end).replace(/\s+/g, " ").trim() + "...",
      });
      idx += queryNorm.length;
      matches++;
    }
  }

  if (!snippets.length) {
    return {
      torneio: t.nome,
      query,
      erro: `Termo "${query}" não encontrado nos documentos. Documentos disponíveis: ${docs.map((d:any)=>d.titulo).join(', ')}`,
    };
  }

  return {
    torneio: t.nome,
    query,
    encontrados: snippets.length,
    snippets: snippets.slice(0, 8),
  };
}

// ─── DOCS ESTRUTURADOS (lê do JSONB conteudo_estruturado) ─────────
async function _buscaDocsEstruturados(torneio_termo: string, tipo: string) {
  const matched = await buscarTorneiosSmart(torneio_termo);
  if (!matched.length) return { erro: `Nenhum torneio achado com "${torneio_termo}"` };
  const t: any = matched[0];
  const { data } = await sb.from("torneio_documentos")
    .select("id, tipo, titulo, data_publicacao, url_pdf, conteudo_estruturado")
    .eq("torneio_id", t.id).eq("tipo", tipo)
    .not("conteudo_estruturado", "is", null)
    .order("data_publicacao", { ascending: false });
  return { torneio: t, docs: data || [] };
}

async function tool_programa_torneio({ torneio_termo }: any) {
  const r = await _buscaDocsEstruturados(torneio_termo, "programa");
  if ((r as any).erro) return r;
  const { torneio, docs } = r as any;
  if (!docs.length) {
    return { torneio: torneio.nome, erro: "Sem programa estruturado disponível pra esse torneio." };
  }
  // Pega o mais recente
  return {
    torneio: torneio.nome,
    fonte: torneio.fonte,
    publicado_em: docs[0].data_publicacao,
    url_pdf: docs[0].url_pdf,
    programa: docs[0].conteudo_estruturado,
  };
}

async function tool_horarios_torneio({ torneio_termo, data }: any) {
  const r = await _buscaDocsEstruturados(torneio_termo, "horarios");
  if ((r as any).erro) return r;
  const { torneio, docs } = r as any;
  if (!docs.length) {
    return { torneio: torneio.nome, erro: "Sem quadro de horários estruturado pra esse torneio." };
  }
  const maisRecente = docs[0];
  const estrut = maisRecente.conteudo_estruturado as any;
  let dias = estrut?.dias || [];

  // Filtra por data se passado
  if (data && Array.isArray(dias)) {
    const norm = data.replace(/\D/g, "");
    dias = dias.filter((d: any) => {
      const dn = String(d.data || "").replace(/\D/g, "");
      return dn.includes(norm) || norm.includes(dn.substring(0, 4));
    });
  }

  return {
    torneio: torneio.nome,
    versao_publicada_em: maisRecente.data_publicacao,
    validade_do_quadro: estrut?.validade,
    url_pdf: maisRecente.url_pdf,
    dias,
  };
}

async function tool_adendos_torneio({ torneio_termo }: any) {
  const r = await _buscaDocsEstruturados(torneio_termo, "adendo");
  if ((r as any).erro) return r;
  const { torneio, docs } = r as any;
  if (!docs.length) {
    return { torneio: torneio.nome, adendos: [], info: "Nenhum adendo publicado pra esse torneio até o momento." };
  }
  return {
    torneio: torneio.nome,
    total_adendos: docs.length,
    adendos: docs.map((d: any) => ({
      titulo: d.titulo,
      publicado_em: d.data_publicacao,
      url_pdf: d.url_pdf,
      ...d.conteudo_estruturado,
    })),
  };
}

async function tool_genealogia_cavalo({ nome }: any) {
  const { data } = await sb.from("genealogia")
    .select("nome,registro,nascimento,sexo,pai,mae,proprietario")
    .ilike("nome", `%${nome}%`).limit(25);
  if (!data || !data.length) return { erro: `"${nome}" não está na genealogia da ABCCH (pode ser importado/sem registro brasileiro).` };
  const nn = normNome(nome);
  const a: any = data.find((x: any) => normNome(x.nome) === nn) || data[0];
  const pref = (a.nome || "").split("(")[0].replace(/[%,]/g, " ").trim();
  const { data: fl } = await sb.from("genealogia")
    .select("nome,sexo,nascimento,pai,mae").or(`pai.ilike.%${pref}%,mae.ilike.%${pref}%`).limit(400);
  const an = normNome(a.nome);
  const filhos = (fl || []).filter((f: any) => normNome(f.pai) === an || normNome(f.mae) === an);
  return {
    nome: a.nome, sexo: a.sexo, nascimento: a.nascimento, registro: a.registro,
    pai: a.pai, mae: a.mae, proprietario: a.proprietario,
    total_filhos: filhos.length,
    filhos_amostra: filhos.slice(0, 20).map((f: any) => ({ nome: cleanFirstLine(f.nome), sexo: f.sexo, nascimento: f.nascimento })),
  };
}

async function tool_rankings_geneticos({ papel = "pai", ano, ordenar = "total" }: any) {
  const { data, error } = await sb.rpc("rankings_geneticos", { papel, ano: ano ?? null });
  if (error || !data) return { erro: error?.message || "sem dados" };
  const key = ({ total: "total_filhos", comp: "comp", pct_comp: "pct_comp", m140: "m140", pct140: "pct140" } as any)[ordenar] || "total_filhos";
  const top = [...(data as any[])].sort((a: any, b: any) => (b[key] || 0) - (a[key] || 0)).slice(0, 12);
  return { papel, ano: ano ?? "todos", ordenado_por: ordenar, top };
}

async function tool_estatisticas_reprodutor({ nome, papel, ano }: any) {
  const nn = normNome(nome);
  const tentar = papel ? [papel] : ["pai", "mae"];
  for (const p of tentar) {
    const { data } = await sb.rpc("rankings_geneticos", { papel: p, ano: ano ?? null });
    const hit: any = (data || []).find((r: any) => normNome(r.reprodutor) === nn);
    if (hit) return {
      reprodutor: hit.reprodutor, papel: p === "pai" ? "garanhão" : "matriz",
      ano: ano ?? "todos os anos",
      total_filhos: hit.total_filhos,
      filhos_mais_4_anos: hit.f4,
      filhos_competindo: hit.comp,
      pct_dos_mais4_que_competem: hit.pct_comp,
      filhos_mais_8_anos: hit.f8,
      filhos_saltando_1_40_ou_mais: hit.m140,
      pct_dos_mais8_em_1_40: hit.pct140,
    };
  }
  return { erro: `"${nome}" não aparece com filhos competindo${ano ? ` em ${ano}` : ""}. Pode não ter filhos no período, ou ser importado/sem registro na ABCCH.` };
}

async function tool_buscar_noticias({ termo, limit = 5 }: any) {
  let q = sb.from("news").select("title,excerpt,date,cat,source_url,created_at")
    .order("created_at", { ascending: false }).limit(termo ? 60 : limit);
  if (termo) q = q.or(`title.ilike.%${termo}%,excerpt.ilike.%${termo}%,body.ilike.%${termo}%`);
  const { data } = await q;
  return (data || []).slice(0, limit).map((n: any) => ({ titulo: n.title, data: n.date, categoria: n.cat, resumo: n.excerpt, link: n.source_url }));
}

async function tool_buscar_podcasts({ termo, programa, limit = 8 }: any) {
  let q = sb.from("media").select("titulo,programa,tipo,url,data_pub")
    .in("tipo", ["podcast", "videocast", "videoaula"])
    .order("data_pub", { ascending: false, nullsFirst: false }).limit((termo || programa) ? 200 : limit);
  if (programa) q = q.ilike("programa", `%${programa}%`);
  if (termo) q = q.or(`titulo.ilike.%${termo}%,programa.ilike.%${termo}%`);
  const { data } = await q;
  return (data || []).slice(0, limit).map((m: any) => ({ titulo: m.titulo, programa: m.programa, tipo: m.tipo, data: m.data_pub, link: m.url }));
}

async function tool_ordem_entrada({ torneio_termo, prova_termo, limit = 40 }: any) {
  const matched = await buscarTorneiosSmart(torneio_termo);
  if (!matched.length) return { erro: `Nenhum torneio com "${torneio_termo}"` };
  const t: any = matched[0];
  const { data: provas } = await sb.from("provas").select("id,nome,numero,descricao").eq("torneio_id", t.id);
  const pn = (prova_termo || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const toks = pn.split(/\s+/).filter((x: string) => x.length >= 2);
  const prova: any = (provas || []).find((p: any) => {
    const n = (p.nome || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
    return toks.every(tk => n.includes(tk));
  }) || (provas || [])[0];
  if (!prova) return { torneio: t.nome, erro: "Torneio sem provas." };
  const { data: oe } = await sb.from("ordem_entrada")
    .select("ordem,cavaleiro_nome,cavalo_nome,categoria").eq("prova_id", prova.id)
    .order("ordem", { ascending: true }).limit(limit);
  if (!oe || !oe.length) return { torneio: t.nome, prova: prova.nome, erro: "Ordem de entrada ainda não publicada pra essa prova." };
  return { torneio: t.nome, prova: prova.nome, total: oe.length,
    ordem: oe.map((o: any) => ({ ordem: o.ordem, cavaleiro: cleanFirstLine(o.cavaleiro_nome), cavalo: cleanFirstLine(o.cavalo_nome), categoria: o.categoria })) };
}

const TOOLS_MAP: Record<string, (input: any) => Promise<any>> = {
  buscar_cavaleiro: tool_buscar_cavaleiro,
  buscar_cavalo: tool_buscar_cavalo,
  estatisticas_cavaleiro: tool_estatisticas_cavaleiro,
  estatisticas_cavalo: tool_estatisticas_cavalo,
  top_zeros_consecutivos: tool_top_zeros_consecutivos,
  proximos_eventos: tool_proximos_eventos,
  buscar_torneio: tool_buscar_torneio,
  resultados_recentes: tool_resultados_recentes,
  vencedor_torneio: tool_vencedor_torneio,
  resultado_prova: tool_resultado_prova,
  buscar_em_documentos: tool_buscar_em_documentos,
  programa_torneio: tool_programa_torneio,
  horarios_torneio: tool_horarios_torneio,
  adendos_torneio: tool_adendos_torneio,
  genealogia_cavalo: tool_genealogia_cavalo,
  rankings_geneticos: tool_rankings_geneticos,
  estatisticas_reprodutor: tool_estatisticas_reprodutor,
  buscar_noticias: tool_buscar_noticias,
  buscar_podcasts: tool_buscar_podcasts,
  ordem_entrada: tool_ordem_entrada,
};

const SYSTEM_PROMPT = `Você é o assistente de hipismo do portal Cavalar.IA. Responde em português brasileiro, com conhecimento técnico do esporte (salto principalmente).

PRINCÍPIO #1: SEMPRE TENTE PRIMEIRO. NUNCA pergunte "qual ano?", "qual torneio?", "pode esclarecer?" sem antes USAR AS FERRAMENTAS pra tentar achar. As tools são tolerantes a variações de nome:
- "csn d maio" casa com "CSN 5* XI D'MAIO 2026"
- "aniversário shc" casa com "CSN2* 78º ANIVERSÁRIO DA SHC 2026"
- "aachen" casa com "CSN 5* CHIO Aachen"

PRINCÍPIO #2: Quando o usuário perguntar "quem venceu o [torneio]", USE A TOOL "vencedor_torneio" — ela já faz toda lógica de achar o torneio + identificar a prova principal (Grande Prêmio ou maior altura) + pegar o 1º lugar. NÃO precisa fazer 3 passos manualmente. Pra prova com NOME específico ("Copa Ouro", "Copa Prata", "PR. 04"), use "resultado_prova".

PRINCÍPIO #2B (TEMPO DO DESEMPATE / 2ª VOLTA É DADO-CHAVE — NUNCA OMITA):
Em provas de DESEMPATE ou DUAS VOLTAS (tipo_prova = "Desempate" / "Duas Voltas"), o resultado DECISIVO é a 2ª passagem: penalidade_2 = faltas no desempate, tempo_2 = tempo do desempate. Em DUAS FASES, o tempo decisivo é o da 2ª fase (tempo_2).
- Ao dizer quem venceu uma prova dessas, SEMPRE informe o tempo decisivo (tempo_2) do campeão E a diferença de tempo pro 2º lugar (use o campo diferenca_tempo_para_2o quando vier; senão calcule pela diferença dos tempo_2). Ex: "Venceu o desempate em 33,83s, 0,64s à frente do 2º (34,47s) — ambos sem faltas".
- O tempo/faltas da 1ª volta são contexto; num desempate o pódio é definido por ZERAR e ser mais RÁPIDO na 2ª. Se o 2º tiver mais faltas no desempate (ex: penalidade_2 = 4), diga isso em vez de só comparar tempo.
- O público quer saber o quão RÁPIDO foi o campeão e a margem pro 2º — trate esse tempo como informação essencial, nunca opcional.

PRINCÍPIO #3: Se sem ano, assume o MAIS RECENTE. A vasta maioria das perguntas é sobre o que aconteceu agora.

PRINCÍPIO #4: Se a tool retornar múltiplos torneios casando, responda com o MAIS RECENTE primeiro e mencione brevemente que há outras edições/anos. Não atrapalhe pedindo escolha.

PRINCÍPIO #5: Pra dados (cavaleiros, cavalos, torneios, rankings, calendário, vencedores), USE AS FERRAMENTAS — nunca invente. Pra regulamentos/opiniões/conhecimento geral do esporte, responda do seu próprio conhecimento mas marque como tal.

PRINCÍPIO #6: ANO ESPECÍFICO. Quando o usuário menciona um ano (ex: "em 2025", "no ano de 2024"), SEMPRE passe esse ano como parâmetro pras estatísticas. Sem ano, default = ano corrente. Pra histórico completo passe ano="todos".

PRINCÍPIO #7 (ABSOLUTAMENTE CRÍTICO - VIOLAR ISSO ARRUINA A EXPERIÊNCIA):
Você está em uma conversa contínua. Use o histórico APENAS pra resolver referências implícitas.
- ❌ PROIBIDO: repetir/recapitular info da resposta anterior antes de responder a nova pergunta.
- ❌ PROIBIDO: começar com "**[Nome do torneio anterior]**:" como cabeçalho.
- ❌ PROIBIDO: usar "---" pra separar "antes" / "agora".
- ❌ PROIBIDO: dizer "Como mencionei antes...", "Continuando..."
- ✅ CORRETO: o usuário VÊ a resposta anterior na tela — não precisa rever.
- ✅ CORRETO: "e o 2º lugar?" → use o contexto da pergunta anterior (torneio X, prova Y) e responda DIRETO o 2º lugar. Sem repetir 1º.
- ✅ CORRETO: "qual o horário dessa prova?" → identifica a prova pelo contexto, responde DIRETO o horário.
- ✅ CORRETO: "e o programa?" → mostra programa do MESMO torneio do contexto, sem recapitular.

Exemplo VIOLAÇÃO (NUNCA faça):
  user: Quem venceu o GP do D'Maio?
  assistant: João da Silva com Penélope, 35.42s, sem faltas.
  user: E o 2º lugar?
  assistant: ❌ "**GP CSN D'Maio 2026:** João venceu. **2º lugar:** Maria..." ← REPETIU!

Exemplo CORRETO:
  user: E o 2º lugar?
  assistant: ✅ "Maria Santos com Trovão, 36.18s, sem faltas."

PRINCÍPIO #8: PROGRAMAS, HORÁRIOS E ADENDOS. Pra perguntas sobre programa oficial, horários, premiações, regulamentos, juízes, mudanças (adendos) de um torneio, USE AS TOOLS ESPECÍFICAS:
- "qual o programa do [torneio]?", "quem é o juiz presidente?", "qual a premiação da prova 1,40m?", "que dia é o GP?" → use programa_torneio
- "que horas começa?", "qual o horário da Copa Ouro?", "agenda do sábado?" → use horarios_torneio (dado mais ATUALIZADO sempre)
- "teve adendo?", "qual a última mudança?", "o programa foi alterado?" → use adendos_torneio
- Pra busca livre em qualquer texto (regulamento detalhado, observações), use buscar_em_documentos como fallback.

IMPORTANTE: Quando responder com base em programa/horários/adendos, SEMPRE mencione a data de publicação do documento (publicado_em) — assim o usuário sabe se a info é recente. Se houver adendos posteriores ao programa, mencione que houve atualização.

PRINCÍPIO #9: VOCÊ TEM ACESSO A TODO O CONTEÚDO DO APP — use as ferramentas certas sem hesitar:
- pai/mãe/filhos/progênie de um cavalo → genealogia_cavalo
- "quantos filhos do [garanhão/matriz] competindo / saltando 1,40m / +4 anos" (UM reprodutor nomeado) → estatisticas_reprodutor (NÃO use buscar_cavalo pra isso — ele só conta nomes parecidos, não filiação). É o número EXATO; responda direto sem estimar.
- top/ranking de garanhões/matrizes → rankings_geneticos
- notícias do portal → buscar_noticias
- podcasts/videocasts/videoaulas/episódios → buscar_podcasts
- ordem de largada de uma prova → ordem_entrada
- resultados, vencedores, estatísticas de cavaleiro/cavalo, calendário → as tools de resultados/torneios
- programa, horários, adendos, regulamento, premiação, juízes → programa_torneio / horarios_torneio / adendos_torneio / buscar_em_documentos
Combine ferramentas quando útil (ex.: genealogia + resultados pra falar de um cavalo e seu desempenho).
Cobertura da genealogia: ABCCH (cavalos brasileiros registrados; importados podem não constar). Se algo não existir no banco, diga com franqueza em vez de inventar.

Estilo: direto, técnico, sem rodeios. Use números, percentuais. Frases curtas. Não cite as ferramentas pelo nome.`;

// ─── HANDLER ────────────────────────────────────────────────────────
Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
  if (req.method !== "POST") return new Response(JSON.stringify({ erro: "use POST" }), { status: 405, headers: { ...CORS, "Content-Type": "application/json" } });

  try {
    // ─── TRAVA PREMIUM (servidor) ─────────────────────────────────
    // O chatbot é exclusivo para assinantes. Valida is_premium() com o JWT do
    // usuário (admin também passa, via is_premium). Sem isso, qualquer um com a
    // anon key chamaria a função direto.
    const authHeader = req.headers.get("Authorization") || "";
    const jwt = authHeader.replace(/^Bearer\s+/i, "").trim();
    const userClient = createClient(SUPABASE_URL, SUPABASE_ANON, {
      global: { headers: { Authorization: `Bearer ${jwt}` } },
    });
    const { data: prem } = await userClient.rpc("is_premium");
    if (!prem) {
      return new Response(JSON.stringify({ erro: "premium_required" }),
        { status: 403, headers: { ...CORS, "Content-Type": "application/json" } });
    }

    const { messages = [] } = await req.json();
    if (!messages.length) return new Response(JSON.stringify({ erro: "messages é obrigatório" }), { status: 400, headers: { ...CORS, "Content-Type": "application/json" } });

    // ─── MEMÓRIA CURTA (sliding window) ───────────────────────────
    // Mantém últimas 6 mensagens (≈ 3 trocas user↔assistant) pra que
    // referências implícitas ("e o cavalo dele?", "quem ficou em 2º?")
    // funcionem, SEM dar ao modelo histórico longo o suficiente pra
    // recapitular. System prompt reforça anti-recap.
    //
    // IMPORTANTE: filtra apenas role user/assistant com content texto
    // (não tool_use/tool_result soltos de turnos antigos — só causa erro
    // se ficarem órfãos sem o par).
    const todas = (messages as any[]).filter((m: any) =>
      (m.role === "user" || m.role === "assistant") &&
      typeof m.content === "string" && m.content.trim().length > 0
    );
    if (!todas.length || todas[todas.length - 1].role !== "user") {
      return new Response(JSON.stringify({ erro: "nenhuma pergunta do usuário encontrada" }),
        { status: 400, headers: { ...CORS, "Content-Type": "application/json" } });
    }
    // Pega últimas 6, garantindo que comece com user
    let convo = todas.slice(-6);
    while (convo.length && convo[0].role !== "user") convo = convo.slice(1);

    const MAX_ITER = 6;
    let lastResp: any;

    for (let i = 0; i < MAX_ITER; i++) {
      const r = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "x-api-key": ANTHROPIC_KEY,
          "anthropic-version": "2023-06-01",
          "content-type": "application/json",
        },
        body: JSON.stringify({
          model: "claude-sonnet-4-5-20250929",
          max_tokens: 1500,
          system: SYSTEM_PROMPT,
          tools: TOOLS,
          messages: convo,
        }),
      });
      if (!r.ok) {
        const text = await r.text();
        return new Response(JSON.stringify({ erro: `Anthropic ${r.status}: ${text.substring(0,300)}` }), { status: 500, headers: { ...CORS, "Content-Type": "application/json" } });
      }
      lastResp = await r.json();

      if (lastResp.stop_reason === "end_turn") break;

      if (lastResp.stop_reason === "tool_use") {
        const toolUses = (lastResp.content || []).filter((c: any) => c.type === "tool_use");
        convo.push({ role: "assistant", content: lastResp.content });
        const toolResults: any[] = [];
        for (const tu of toolUses) {
          const exec = TOOLS_MAP[tu.name];
          if (!exec) {
            toolResults.push({ type: "tool_result", tool_use_id: tu.id, content: JSON.stringify({ erro: "tool desconhecida" }) });
            continue;
          }
          try {
            const result = await exec(tu.input || {});
            toolResults.push({ type: "tool_result", tool_use_id: tu.id, content: JSON.stringify(result) });
          } catch (e: any) {
            toolResults.push({ type: "tool_result", tool_use_id: tu.id, content: JSON.stringify({ erro: e.message }) });
          }
        }
        convo.push({ role: "user", content: toolResults });
        continue;
      }
      break;
    }

    const respostaTexto = (lastResp.content || []).filter((c: any) => c.type === "text").map((c: any) => c.text).join("\n");
    return new Response(JSON.stringify({ resposta: respostaTexto }), { headers: { ...CORS, "Content-Type": "application/json" } });
  } catch (err: any) {
    return new Response(JSON.stringify({ erro: err.message }), { status: 500, headers: { ...CORS, "Content-Type": "application/json" } });
  }
});
