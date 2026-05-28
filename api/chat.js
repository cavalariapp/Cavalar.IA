// ═══════════════════════════════════════════════════════════════════
// CHATBOT — Vercel Function que conecta o usuário ao Claude com tool use.
// Claude tem acesso a ferramentas que consultam o Supabase (resultados,
// rankings, calendário, documentos) e responde em PT-BR.
// ═══════════════════════════════════════════════════════════════════

const Anthropic = require('@anthropic-ai/sdk').default;
const { createClient } = require('@supabase/supabase-js');

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_KEY = process.env.SUPABASE_KEY; // anon, leitura só
const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY;

const sb = createClient(SUPABASE_URL, SUPABASE_KEY);
const anthropic = new Anthropic({ apiKey: ANTHROPIC_API_KEY });

// ─── HELPERS ────────────────────────────────────────────────────────
function cleanFirstLine(s) {
  if (!s) return '';
  return s.split(/\n/)[0].split(/\s*\|\s*/)[0].trim();
}

function isPenZero(p) {
  if (!p) return false;
  return /^0([\s\n(,]|$)/.test(String(p).trim());
}

// ─── DEFINIÇÃO DAS FERRAMENTAS (TOOLS) ──────────────────────────────
const TOOLS = [
  {
    name: 'buscar_cavaleiro',
    description: 'Busca cavaleiros pelo nome (parcial). Retorna lista de cavaleiros encontrados com contagem de participações em 2026. Use quando o usuário menciona um cavaleiro por nome.',
    input_schema: {
      type: 'object',
      properties: {
        termo: { type: 'string', description: 'Parte do nome do cavaleiro (sobrenome ou nome completo)' },
        limit: { type: 'number', description: 'Quantos cavaleiros retornar (default 5)' },
      },
      required: ['termo'],
    },
  },
  {
    name: 'buscar_cavalo',
    description: 'Busca cavalos pelo nome (parcial). Retorna lista de cavalos encontrados. Use quando o usuário menciona um cavalo por nome.',
    input_schema: {
      type: 'object',
      properties: {
        termo: { type: 'string', description: 'Parte do nome do cavalo' },
        limit: { type: 'number', description: 'Quantos retornar (default 5)' },
      },
      required: ['termo'],
    },
  },
  {
    name: 'estatisticas_cavaleiro',
    description: 'Retorna estatísticas detalhadas de UM cavaleiro específico em 2026: total de participações, % de percursos zerados, % de vitórias, % de top 6, número de provas, etc. Use após o usuário identificar UM cavaleiro específico (use buscar_cavaleiro antes se não tiver certeza do nome exato).',
    input_schema: {
      type: 'object',
      properties: {
        nome_exato: { type: 'string', description: 'Nome EXATO do cavaleiro como aparece no banco' },
      },
      required: ['nome_exato'],
    },
  },
  {
    name: 'estatisticas_cavalo',
    description: 'Estatísticas detalhadas de UM cavalo específico em 2026.',
    input_schema: {
      type: 'object',
      properties: {
        nome_exato: { type: 'string', description: 'Nome EXATO do cavalo' },
      },
      required: ['nome_exato'],
    },
  },
  {
    name: 'top_zeros_consecutivos',
    description: 'Retorna o ranking dos top 10 (cavaleiros ou cavalos) com maior sequência de zeros consecutivos em uma altura específica em 2026.',
    input_schema: {
      type: 'object',
      properties: {
        altura: { type: 'string', description: 'Altura no formato "1,40M" / "1,50M" etc. Aceita 1,00 a 1,60.' },
        entidade: { type: 'string', enum: ['cavaleiro', 'cavalo'], description: 'cavaleiro ou cavalo' },
      },
      required: ['altura', 'entidade'],
    },
  },
  {
    name: 'proximos_eventos',
    description: 'Lista os próximos eventos/torneios do calendário (a partir de hoje, até o limite especificado). Combina torneios das federações + calendário CBH.',
    input_schema: {
      type: 'object',
      properties: {
        dias: { type: 'number', description: 'Janela de dias pra frente (default 30)' },
        limit: { type: 'number', description: 'Quantidade máxima (default 10)' },
      },
    },
  },
  {
    name: 'buscar_torneio',
    description: 'Busca torneios pelo nome (parcial). Retorna info do torneio + se tem provas com resultados + documentos disponíveis.',
    input_schema: {
      type: 'object',
      properties: {
        termo: { type: 'string', description: 'Parte do nome do torneio' },
        ano: { type: 'number', description: 'Ano específico (opcional)' },
      },
      required: ['termo'],
    },
  },
  {
    name: 'resultados_recentes',
    description: 'Retorna os resultados mais recentes de um cavaleiro ou cavalo (últimas N provas em que participou).',
    input_schema: {
      type: 'object',
      properties: {
        entidade: { type: 'string', enum: ['cavaleiro', 'cavalo'] },
        nome_exato: { type: 'string' },
        limit: { type: 'number', description: 'Quantos resultados (default 5)' },
      },
      required: ['entidade', 'nome_exato'],
    },
  },
];

// ─── EXECUTORES DAS FERRAMENTAS ─────────────────────────────────────
const HEIGHTS = ['1,00M','1,10M','1,20M','1,30M','1,35M','1,40M','1,45M','1,50M','1,55M','1,60M'];

async function tool_buscar_cavaleiro({ termo, limit = 5 }) {
  const ano = new Date().getFullYear();
  const { data } = await sb
    .from('resultados')
    .select('cavaleiro_nome, provas!inner(torneios!inner(data_inicio))')
    .ilike('cavaleiro_nome', `%${termo}%`)
    .gte('provas.torneios.data_inicio', `${ano}-01-01`)
    .limit(2000);
  const counts = {};
  for (const r of (data || [])) {
    const n = cleanFirstLine(r.cavaleiro_nome);
    if (!n) continue;
    counts[n] = (counts[n] || 0) + 1;
  }
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([nome, participacoes]) => ({ nome, participacoes }));
}

async function tool_buscar_cavalo({ termo, limit = 5 }) {
  const ano = new Date().getFullYear();
  const { data } = await sb
    .from('resultados')
    .select('cavalo_nome, provas!inner(torneios!inner(data_inicio))')
    .ilike('cavalo_nome', `%${termo}%`)
    .gte('provas.torneios.data_inicio', `${ano}-01-01`)
    .limit(2000);
  const counts = {};
  for (const r of (data || [])) {
    const n = cleanFirstLine(r.cavalo_nome);
    if (!n) continue;
    counts[n] = (counts[n] || 0) + 1;
  }
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([nome, participacoes]) => ({ nome, participacoes }));
}

async function _statsPorEntidade(nome, campo) {
  const ano = new Date().getFullYear();
  const { data } = await sb
    .from('resultados')
    .select(`id, colocacao, penalidade, prova_id, ${campo === 'cavaleiro' ? 'cavaleiro_nome' : 'cavalo_nome'}, provas!inner(torneios!inner(data_inicio))`)
    .ilike(campo === 'cavaleiro' ? 'cavaleiro_nome' : 'cavalo_nome', `%${nome}%`)
    .gte('provas.torneios.data_inicio', `${ano}-01-01`)
    .limit(3000);

  const filtrados = (data || []).filter(r => {
    const n = cleanFirstLine(campo === 'cavaleiro' ? r.cavaleiro_nome : r.cavalo_nome);
    return n.toLowerCase() === nome.toLowerCase();
  });

  const total = filtrados.length;
  const provas = new Set(filtrados.map(r => r.prova_id)).size;
  const zerados = filtrados.filter(r => isPenZero(r.penalidade)).length;
  const vitorias = filtrados.filter(r => (r.colocacao || '').trim() === '1º').length;
  const top6 = filtrados.filter(r => /^[1-6]º$/.test((r.colocacao || '').trim())).length;
  const pct = n => total > 0 ? Math.round((n / total) * 100) : 0;
  return {
    total_participacoes: total,
    total_provas: provas,
    percursos_zero: zerados,
    pct_percursos_zero: pct(zerados),
    vitorias,
    pct_vitorias: pct(vitorias),
    top6,
    pct_top6: pct(top6),
  };
}

async function tool_estatisticas_cavaleiro({ nome_exato }) {
  return _statsPorEntidade(nome_exato, 'cavaleiro');
}
async function tool_estatisticas_cavalo({ nome_exato }) {
  return _statsPorEntidade(nome_exato, 'cavalo');
}

async function tool_top_zeros_consecutivos({ altura, entidade }) {
  if (!HEIGHTS.includes(altura)) {
    return { erro: `altura inválida; use uma de: ${HEIGHTS.join(', ')}` };
  }
  const ano = new Date().getFullYear();
  const { data } = await sb
    .from('resultados')
    .select(`id, cavaleiro_nome, cavalo_nome, penalidade, provas!inner(id, numero, descricao, torneios!inner(data_inicio))`)
    .eq('provas.descricao', altura)
    .gte('provas.torneios.data_inicio', `${ano}-01-01`)
    .limit(5000);

  // Ordena cronológico, agrupa por entidade, computa streak (regra B)
  const arr = (data || []).map(r => ({
    nome: entidade === 'cavaleiro' ? cleanFirstLine(r.cavaleiro_nome) : cleanFirstLine(r.cavalo_nome),
    penalidade: r.penalidade,
    sortKey: `${r.provas?.torneios?.data_inicio || ''}|${String(r.provas?.numero || 0).padStart(4, '0')}|${String(r.id).padStart(8, '0')}`,
  }));
  arr.sort((a, b) => a.sortKey.localeCompare(b.sortKey));

  const porNome = {};
  for (const r of arr) {
    if (!r.nome) continue;
    if (!porNome[r.nome]) porNome[r.nome] = [];
    porNome[r.nome].push(r);
  }

  const ranked = [];
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
  ranked.sort((a, b) => {
    if (b.length !== a.length) return b.length - a.length;
    return b.ativo - a.ativo;
  });
  return ranked.slice(0, 10);
}

async function tool_proximos_eventos({ dias = 30, limit = 10 }) {
  const hoje = new Date().toISOString().substring(0, 10);
  const fim = new Date(); fim.setDate(fim.getDate() + dias);
  const fimIso = fim.toISOString().substring(0, 10);
  const [tor, cbh] = await Promise.all([
    sb.from('torneios').select('id, nome, fonte, data_inicio, data_fim, fingerprint')
      .gte('data_inicio', hoje).lte('data_inicio', fimIso),
    sb.from('eventos_cbh').select('id, evento, federacao, data_inicio, data_fim, local, estado, fingerprint')
      .gte('data_inicio', hoje).lte('data_inicio', fimIso),
  ]);
  const torFps = new Set((tor.data || []).map(t => t.fingerprint).filter(Boolean));
  const cbhFiltrado = (cbh.data || []).filter(c => !torFps.has(c.fingerprint));
  const all = [
    ...(tor.data || []).map(t => ({
      nome: (t.nome || '').split('\n')[0],
      fonte: t.fonte,
      data_inicio: t.data_inicio, data_fim: t.data_fim,
    })),
    ...cbhFiltrado.map(c => ({
      nome: (c.evento || '').split('\n')[0],
      fonte: `CBH (${c.federacao})`,
      local: c.local, estado: c.estado,
      data_inicio: c.data_inicio, data_fim: c.data_fim,
    })),
  ];
  all.sort((a, b) => (a.data_inicio || '').localeCompare(b.data_inicio || ''));
  return all.slice(0, limit);
}

async function tool_buscar_torneio({ termo, ano }) {
  let q = sb.from('torneios').select('id, nome, fonte, data_inicio, data_fim')
    .ilike('nome', `%${termo}%`).limit(10);
  if (ano) { q = q.gte('data_inicio', `${ano}-01-01`).lte('data_inicio', `${ano}-12-31`); }
  const { data } = await q;
  const result = [];
  for (const t of (data || [])) {
    const { count: provasCount } = await sb.from('provas').select('id', { count: 'exact', head: true }).eq('torneio_id', t.id);
    const { count: docsCount } = await sb.from('torneio_documentos').select('id', { count: 'exact', head: true }).eq('torneio_id', t.id);
    result.push({
      nome: t.nome, fonte: t.fonte,
      data_inicio: t.data_inicio, data_fim: t.data_fim,
      provas: provasCount, documentos: docsCount,
    });
  }
  return result;
}

async function tool_resultados_recentes({ entidade, nome_exato, limit = 5 }) {
  const campo = entidade === 'cavaleiro' ? 'cavaleiro_nome' : 'cavalo_nome';
  const { data } = await sb
    .from('resultados')
    .select(`id, colocacao, cavaleiro_nome, cavalo_nome, penalidade, tempo, prova_id, provas!inner(nome, descricao, torneios!inner(nome, data_inicio))`)
    .ilike(campo, `%${nome_exato}%`)
    .order('id', { ascending: false })
    .limit(200);
  const filtrados = (data || [])
    .filter(r => cleanFirstLine(r[campo]).toLowerCase() === nome_exato.toLowerCase())
    .slice(0, limit)
    .map(r => ({
      torneio: r.provas?.torneios?.nome,
      data: r.provas?.torneios?.data_inicio,
      prova: r.provas?.nome,
      altura: r.provas?.descricao,
      cavaleiro: cleanFirstLine(r.cavaleiro_nome),
      cavalo: cleanFirstLine(r.cavalo_nome),
      colocacao: r.colocacao,
      penalidade: r.penalidade,
      tempo: r.tempo,
    }));
  return filtrados;
}

const TOOL_EXECUTORS = {
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

Estilo: direto, técnico, sem rodeios. Use números, percentuais e contexto. Quando relevante, faça conexões (ex: "esse cavalo é filho de X, conhecido por...").

Quando o usuário pesquisar um nome ambíguo (ex: "Raphael"), use buscar_cavaleiro primeiro pra ver opções, depois use estatisticas_cavaleiro com o nome COMPLETO mais provável OU pergunte qual deles.

Não cite suas ferramentas pelo nome — só use os dados que elas retornam.`;

// ─── HANDLER ────────────────────────────────────────────────────────
module.exports = async (req, res) => {
  // CORS pra chamadas locais durante desenvolvimento
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') { res.status(200).end(); return; }
  if (req.method !== 'POST')    { res.status(405).json({ erro: 'use POST' }); return; }

  try {
    const { messages = [] } = req.body || {};
    if (!messages.length) {
      res.status(400).json({ erro: 'messages é obrigatório' });
      return;
    }

    let convo = [...messages];
    const MAX_ITER = 6;
    let lastResp;

    for (let i = 0; i < MAX_ITER; i++) {
      lastResp = await anthropic.messages.create({
        model: 'claude-haiku-4-5-20251001',
        max_tokens: 1500,
        system: SYSTEM_PROMPT,
        tools: TOOLS,
        messages: convo,
      });

      if (lastResp.stop_reason === 'end_turn') break;

      if (lastResp.stop_reason === 'tool_use') {
        const toolUses = lastResp.content.filter(c => c.type === 'tool_use');
        convo.push({ role: 'assistant', content: lastResp.content });

        const toolResults = [];
        for (const tu of toolUses) {
          const exec = TOOL_EXECUTORS[tu.name];
          if (!exec) {
            toolResults.push({
              type: 'tool_result',
              tool_use_id: tu.id,
              content: JSON.stringify({ erro: 'tool desconhecida' }),
            });
            continue;
          }
          try {
            const result = await exec(tu.input || {});
            toolResults.push({
              type: 'tool_result',
              tool_use_id: tu.id,
              content: JSON.stringify(result),
            });
          } catch (e) {
            toolResults.push({
              type: 'tool_result',
              tool_use_id: tu.id,
              content: JSON.stringify({ erro: e.message }),
            });
          }
        }
        convo.push({ role: 'user', content: toolResults });
        continue;
      }

      break;
    }

    const respostaTexto = (lastResp.content || [])
      .filter(c => c.type === 'text')
      .map(c => c.text)
      .join('\n');

    res.status(200).json({
      resposta: respostaTexto,
      messages: convo, // pro frontend manter contexto
    });

  } catch (err) {
    console.error('CHAT ERROR:', err);
    res.status(500).json({ erro: err.message || 'erro interno' });
  }
};
