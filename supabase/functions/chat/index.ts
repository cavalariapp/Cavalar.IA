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
const ANTHROPIC_KEY = Deno.env.get("ANTHROPIC_API_KEY")!;

const sb = createClient(SUPABASE_URL, SUPABASE_ANON);

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
function isPenZero(p: any): boolean {
  if (!p) return false;
  return /^0([\s\n(,]|$)/.test(String(p).trim());
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
  { name: "vencedor_torneio", description: "Retorna o vencedor da prova principal (Grande Prêmio / maior altura) de um torneio. Aceita nome PARCIAL ou com variação (ex: 'CSN d maio', 'aniversário SHC', 'aachen'). Se não passar ano, pega o mais recente.",
    input_schema: { type: "object", properties: { termo: { type: "string", description: "Nome ou parte do nome do torneio" }, ano: { type: "number", description: "Ano específico (opcional)" } }, required: ["termo"] } },
  { name: "resultado_prova", description: "Retorna resultados (top N) de UMA prova ESPECÍFICA dentro de um torneio. Use quando o usuário menciona o nome da prova: 'Copa Ouro', 'Copa Prata', 'Grande Prêmio', 'PR. 04', etc. Match por keyword no nome da prova.",
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

  // Busca o 1º lugar
  const { data: resultados } = await sb.from("resultados")
    .select("colocacao, cavaleiro_nome, cavalo_nome, tempo, penalidade")
    .eq("prova_id", provaPrincipal.id)
    .order("id", { ascending: true });

  if (!resultados || resultados.length === 0) {
    return { torneio: t.nome, data: t.data_inicio, prova_principal: provaPrincipal.nome, erro: "Prova sem resultados ainda." };
  }

  const primeiro = resultados.find((r: any) => (r.colocacao || "").trim() === "1º") || resultados[0];

  return {
    torneio: t.nome,
    data: t.data_inicio,
    fonte: t.fonte,
    prova_principal: provaPrincipal.nome,
    altura: provaPrincipal.descricao,
    vencedor: {
      colocacao: primeiro.colocacao,
      cavaleiro: cleanFirstLine(primeiro.cavaleiro_nome),
      cavalo: cleanFirstLine(primeiro.cavalo_nome),
      tempo: primeiro.tempo,
      penalidade: primeiro.penalidade,
    },
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

  return {
    torneio: t.nome,
    data: t.data_inicio,
    prova: prova.nome,
    tipo_prova: prova.tipo_prova,
    altura: prova.descricao,
    total_resultados: resultados.length,
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
};

const SYSTEM_PROMPT = `Você é o assistente de hipismo do portal Cavalar.IA. Responde em português brasileiro, com conhecimento técnico do esporte (salto principalmente).

PRINCÍPIO #1: SEMPRE TENTE PRIMEIRO. NUNCA pergunte "qual ano?", "qual torneio?", "pode esclarecer?" sem antes USAR AS FERRAMENTAS pra tentar achar. As tools são tolerantes a variações de nome:
- "csn d maio" casa com "CSN 5* XI D'MAIO 2026"
- "aniversário shc" casa com "CSN2* 78º ANIVERSÁRIO DA SHC 2026"
- "aachen" casa com "CSN 5* CHIO Aachen"

PRINCÍPIO #2: Quando o usuário perguntar "quem venceu o [torneio]", USE A TOOL "vencedor_torneio" — ela já faz toda lógica de achar o torneio + identificar a prova principal (Grande Prêmio ou maior altura) + pegar o 1º lugar. NÃO precisa fazer 3 passos manualmente.

PRINCÍPIO #3: Se sem ano, assume o MAIS RECENTE. A vasta maioria das perguntas é sobre o que aconteceu agora.

PRINCÍPIO #4: Se a tool retornar múltiplos torneios casando, responda com o MAIS RECENTE primeiro e mencione brevemente que há outras edições/anos. Não atrapalhe pedindo escolha.

PRINCÍPIO #5: Pra dados (cavaleiros, cavalos, torneios, rankings, calendário, vencedores), USE AS FERRAMENTAS — nunca invente. Pra regulamentos/opiniões/conhecimento geral do esporte, responda do seu próprio conhecimento mas marque como tal.

PRINCÍPIO #6: ANO ESPECÍFICO. Quando o usuário menciona um ano (ex: "em 2025", "no ano de 2024"), SEMPRE passe esse ano como parâmetro pras estatísticas. Sem ano, default = ano corrente. Pra histórico completo passe ano="todos".

PRINCÍPIO #7 (CRÍTICO - SEMPRE OBEDEÇA): RESPONDA APENAS A PERGUNTA ATUAL.
- NUNCA comece a resposta com cabeçalho/recap da pergunta anterior (ex: NÃO faça "**GP CSN D'Maio 2026:** ..." antes de responder outra coisa).
- NUNCA use separador "---" pra dividir "pergunta anterior" e "pergunta nova".
- NUNCA repita info que já deu na resposta anterior.
- Use o histórico SOMENTE pra entender pronomes/referências implícitas (ex: "e o cavalo dele?" → você sabe quem é "dele" pelo contexto).
- Cada resposta começa DIRETAMENTE com a info da pergunta atual.

Estilo: direto, técnico, sem rodeios. Use números, percentuais. Frases curtas. Não cite as ferramentas pelo nome.`;

// ─── HANDLER ────────────────────────────────────────────────────────
Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
  if (req.method !== "POST") return new Response(JSON.stringify({ erro: "use POST" }), { status: 405, headers: { ...CORS, "Content-Type": "application/json" } });

  try {
    const { messages = [] } = await req.json();
    if (!messages.length) return new Response(JSON.stringify({ erro: "messages é obrigatório" }), { status: 400, headers: { ...CORS, "Content-Type": "application/json" } });

    // ─── SINGLE-TURN ──────────────────────────────────────────────
    // Cada pergunta é independente. Histórico anterior é IGNORADO
    // pra eliminar o comportamento de recap. Se o usuário quiser
    // referenciar info anterior, precisa repetir o contexto.
    const lastUserMsg = (messages as any[]).filter(m => m.role === "user").pop();
    if (!lastUserMsg) {
      return new Response(JSON.stringify({ erro: "nenhuma pergunta do usuário encontrada" }),
        { status: 400, headers: { ...CORS, "Content-Type": "application/json" } });
    }
    let convo = [lastUserMsg];

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
