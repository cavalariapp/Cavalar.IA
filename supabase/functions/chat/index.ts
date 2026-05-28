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

// ─── DEFINIÇÃO DE TOOLS ─────────────────────────────────────────────
const TOOLS = [
  { name: "buscar_cavaleiro", description: "Busca cavaleiros pelo nome (parcial). Retorna lista com contagem de participações em 2026.",
    input_schema: { type: "object", properties: { termo: { type: "string" }, limit: { type: "number" } }, required: ["termo"] } },
  { name: "buscar_cavalo", description: "Busca cavalos pelo nome.",
    input_schema: { type: "object", properties: { termo: { type: "string" }, limit: { type: "number" } }, required: ["termo"] } },
  { name: "estatisticas_cavaleiro", description: "Estatísticas de UM cavaleiro: % zeros, vitórias, top 6 em 2026. Use o nome EXATO (rode buscar_cavaleiro antes se preciso).",
    input_schema: { type: "object", properties: { nome_exato: { type: "string" } }, required: ["nome_exato"] } },
  { name: "estatisticas_cavalo", description: "Estatísticas de UM cavalo.",
    input_schema: { type: "object", properties: { nome_exato: { type: "string" } }, required: ["nome_exato"] } },
  { name: "top_zeros_consecutivos", description: "Top 10 do ranking de zeros consecutivos por altura.",
    input_schema: { type: "object", properties: { altura: { type: "string" }, entidade: { type: "string", enum: ["cavaleiro", "cavalo"] } }, required: ["altura", "entidade"] } },
  { name: "proximos_eventos", description: "Próximos eventos/torneios (federações + CBH).",
    input_schema: { type: "object", properties: { dias: { type: "number" }, limit: { type: "number" } } } },
  { name: "buscar_torneio", description: "Busca torneios por nome.",
    input_schema: { type: "object", properties: { termo: { type: "string" }, ano: { type: "number" } }, required: ["termo"] } },
  { name: "resultados_recentes", description: "Últimos resultados de um cavaleiro ou cavalo.",
    input_schema: { type: "object", properties: { entidade: { type: "string", enum: ["cavaleiro", "cavalo"] }, nome_exato: { type: "string" }, limit: { type: "number" } }, required: ["entidade", "nome_exato"] } },
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

async function _stats(nome: string, campo: "cavaleiro" | "cavalo") {
  const ano = new Date().getFullYear();
  const col = campo === "cavaleiro" ? "cavaleiro_nome" : "cavalo_nome";
  const { data } = await sb.from("resultados")
    .select(`id, colocacao, penalidade, prova_id, ${col}, provas!inner(torneios!inner(data_inicio))`)
    .ilike(col, `%${nome}%`)
    .gte("provas.torneios.data_inicio", `${ano}-01-01`).limit(3000);
  const filt = (data || []).filter((r:any)=>cleanFirstLine(r[col]).toLowerCase()===nome.toLowerCase());
  const total = filt.length;
  const provas = new Set(filt.map((r:any)=>r.prova_id)).size;
  const zerados = filt.filter((r:any)=>isPenZero(r.penalidade)).length;
  const vitorias = filt.filter((r:any)=>(r.colocacao||"").trim()==="1º").length;
  const top6 = filt.filter((r:any)=>/^[1-6]º$/.test((r.colocacao||"").trim())).length;
  const pct = (n: number) => total>0 ? Math.round((n/total)*100) : 0;
  return { total_participacoes: total, total_provas: provas, percursos_zero: zerados, pct_percursos_zero: pct(zerados),
           vitorias, pct_vitorias: pct(vitorias), top6, pct_top6: pct(top6) };
}
async function tool_estatisticas_cavaleiro({ nome_exato }: any) { return _stats(nome_exato, "cavaleiro"); }
async function tool_estatisticas_cavalo({ nome_exato }: any) { return _stats(nome_exato, "cavalo"); }

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

async function tool_buscar_torneio({ termo, ano }: any) {
  let q = sb.from("torneios").select("id, nome, fonte, data_inicio, data_fim").ilike("nome", `%${termo}%`).limit(10);
  if (ano) q = q.gte("data_inicio", `${ano}-01-01`).lte("data_inicio", `${ano}-12-31`);
  const { data } = await q;
  const result: any[] = [];
  for (const t of (data || [])) {
    const { count: pCount } = await sb.from("provas").select("id", { count: "exact", head: true }).eq("torneio_id", (t as any).id);
    const { count: dCount } = await sb.from("torneio_documentos").select("id", { count: "exact", head: true }).eq("torneio_id", (t as any).id);
    result.push({ nome: (t as any).nome, fonte: (t as any).fonte, data_inicio: (t as any).data_inicio, data_fim: (t as any).data_fim, provas: pCount, documentos: dCount });
  }
  return result;
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

const TOOLS_MAP: Record<string, (input: any) => Promise<any>> = {
  buscar_cavaleiro: tool_buscar_cavaleiro,
  buscar_cavalo: tool_buscar_cavalo,
  estatisticas_cavaleiro: tool_estatisticas_cavaleiro,
  estatisticas_cavalo: tool_estatisticas_cavalo,
  top_zeros_consecutivos: tool_top_zeros_consecutivos,
  proximos_eventos: tool_proximos_eventos,
  buscar_torneio: tool_buscar_torneio,
  resultados_recentes: tool_resultados_recentes,
};

const SYSTEM_PROMPT = `Você é o assistente de hipismo do portal Cavalar.IA. Responde em português brasileiro, com conhecimento técnico do esporte (salto principalmente).

Pra dados de cavaleiros, cavalos, torneios, rankings ou calendário, USE AS FERRAMENTAS — não invente. Se o usuário pedir algo que não dá pra responder com as ferramentas (ex: regulamento técnico, opinião), responda do seu conhecimento mas deixe claro quando não tem certeza.

Estilo: direto, técnico, sem rodeios. Use números, percentuais e contexto. Quando relevante, faça conexões.

Quando o usuário pesquisar um nome ambíguo (ex: "Raphael"), use buscar_cavaleiro primeiro pra ver opções, depois use estatisticas_cavaleiro com o nome COMPLETO mais provável OU pergunte qual deles.

Não cite suas ferramentas pelo nome — só use os dados que elas retornam.`;

// ─── HANDLER ────────────────────────────────────────────────────────
Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
  if (req.method !== "POST") return new Response(JSON.stringify({ erro: "use POST" }), { status: 405, headers: { ...CORS, "Content-Type": "application/json" } });

  try {
    const { messages = [] } = await req.json();
    if (!messages.length) return new Response(JSON.stringify({ erro: "messages é obrigatório" }), { status: 400, headers: { ...CORS, "Content-Type": "application/json" } });

    const convo = [...messages];
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
          model: "claude-haiku-4-5-20251001",
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
