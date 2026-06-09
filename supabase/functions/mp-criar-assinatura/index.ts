// ═══════════════════════════════════════════════════════════════════
// MP-CRIAR-ASSINATURA — cria a cobrança no Mercado Pago e devolve a URL
//
// Body: { plano: 'mensal'|'anual', metodo: 'recorrente'|'avulso' }
//   recorrente (cartão) → preapproval (renova sozinho)
//   avulso     (Pix)    → preference  (paga o período uma vez)
//
// Identifica o usuário pelo JWT, cria uma linha 'pendente' em assinaturas
// (external_reference = id dela) e chama o MP. O webhook ativa o premium.
//
// Deploy:  supabase functions deploy mp-criar-assinatura
// Secret:  supabase secrets set MP_ACCESS_TOKEN=TEST-... (sandbox) ou APP_USR-...
// ═══════════════════════════════════════════════════════════════════
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.0";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_ANON = Deno.env.get("SUPABASE_ANON_KEY")!;
const SUPABASE_SERVICE = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? SUPABASE_ANON;
const MP_TOKEN = Deno.env.get("MP_ACCESS_TOKEN")!;
const MP_TEST = MP_TOKEN.startsWith("TEST-");

// PREÇOS (R$) — fonte de verdade no servidor. Ajuste aqui quando definir os valores.
const PRECOS: Record<string, number> = { mensal: 29.90, anual: 299.00 };

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

const sb = createClient(SUPABASE_URL, SUPABASE_SERVICE);

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
  if (req.method !== "POST") return json({ erro: "use POST" }, 405);

  try {
    // 1) identifica o usuário pelo JWT
    const jwt = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "").trim();
    const userClient = createClient(SUPABASE_URL, SUPABASE_ANON, {
      global: { headers: { Authorization: `Bearer ${jwt}` } },
    });
    const { data: { user } } = await userClient.auth.getUser();
    if (!user) return json({ erro: "precisa estar logado" }, 401);

    const { plano = "mensal", metodo = "recorrente" } = await req.json();
    if (!["mensal", "anual"].includes(plano)) return json({ erro: "plano inválido" }, 400);
    if (!["recorrente", "avulso"].includes(metodo)) return json({ erro: "metodo inválido" }, 400);
    const valor = PRECOS[plano];
    const meses = plano === "anual" ? 12 : 1;
    const email = user.email;

    // 2) cria a assinatura pendente (external_reference = id)
    const { data: ass, error: errIns } = await sb.from("assinaturas").insert({
      profile_id: user.id, status: "pendente", plano, metodo,
      valor, mp_payer_email: email,
    }).select("id").single();
    if (errIns) return json({ erro: "falha ao criar assinatura: " + errIns.message }, 500);
    const extRef = ass.id;

    const webhook = `${SUPABASE_URL}/functions/v1/mp-webhook`;
    const backUrl = req.headers.get("origin") ? `${req.headers.get("origin")}/perfil.html` : `${SUPABASE_URL}`;

    let checkoutUrl = "";
    if (metodo === "recorrente") {
      // ── Cartão: preapproval (assinatura recorrente) ──
      const body = {
        reason: `Cavalar.IA Premium ${plano === "anual" ? "Anual" : "Mensal"}`,
        external_reference: extRef,
        payer_email: email,
        back_url: backUrl,
        status: "pending",
        notification_url: webhook,
        auto_recurring: {
          frequency: meses, frequency_type: "months",
          transaction_amount: valor, currency_id: "BRL",
        },
      };
      const r = await fetch("https://api.mercadopago.com/preapproval", {
        method: "POST",
        headers: { Authorization: `Bearer ${MP_TOKEN}`, "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (!r.ok) return json({ erro: "MP preapproval: " + JSON.stringify(j).slice(0, 300) }, 502);
      await sb.from("assinaturas").update({ mp_preapproval_id: j.id }).eq("id", extRef);
      checkoutUrl = (MP_TEST && j.sandbox_init_point) ? j.sandbox_init_point : j.init_point;
    } else {
      // ── Pix avulso: preference (Checkout Pro) ──
      const body = {
        items: [{
          title: `Cavalar.IA Premium ${plano === "anual" ? "Anual" : "Mensal"}`,
          quantity: 1, unit_price: valor, currency_id: "BRL",
        }],
        payer: { email },
        external_reference: String(extRef),
        notification_url: webhook,
        back_urls: { success: backUrl, pending: backUrl, failure: backUrl },
        auto_return: "approved",
        payment_methods: {
          // avulso = Pix (exclui cartão pra não confundir com o plano recorrente)
          excluded_payment_types: [{ id: "credit_card" }, { id: "debit_card" }],
        },
      };
      const r = await fetch("https://api.mercadopago.com/checkout/preferences", {
        method: "POST",
        headers: { Authorization: `Bearer ${MP_TOKEN}`, "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (!r.ok) return json({ erro: "MP preference: " + JSON.stringify(j).slice(0, 300) }, 502);
      checkoutUrl = (MP_TEST && j.sandbox_init_point) ? j.sandbox_init_point : j.init_point;
    }

    return json({ url: checkoutUrl, assinatura_id: extRef });
  } catch (e: any) {
    return json({ erro: e.message }, 500);
  }
});

function json(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { ...CORS, "Content-Type": "application/json" } });
}
