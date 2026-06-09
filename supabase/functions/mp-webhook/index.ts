// ═══════════════════════════════════════════════════════════════════
// MP-WEBHOOK — notificações do Mercado Pago → ativa/cancela o premium
//
// MP chama esta URL quando muda um pagamento (Pix avulso) ou uma assinatura
// recorrente (cartão). Buscamos o recurso na API do MP (isso valida que é real
// e nosso), achamos a assinatura por external_reference e atualizamos o status.
//
// Deploy SEM JWT (o MP não manda token do Supabase):
//   supabase functions deploy mp-webhook --no-verify-jwt
// ═══════════════════════════════════════════════════════════════════
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.0";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? Deno.env.get("SUPABASE_ANON_KEY")!;
const MP_TOKEN = Deno.env.get("MP_ACCESS_TOKEN")!;

const sb = createClient(SUPABASE_URL, SUPABASE_SERVICE);

async function mpGet(path: string) {
  const r = await fetch(`https://api.mercadopago.com${path}`, {
    headers: { Authorization: `Bearer ${MP_TOKEN}` },
  });
  return r.ok ? await r.json() : null;
}

function addMonths(months: number): string {
  const d = new Date();
  d.setMonth(d.getMonth() + months);
  return d.toISOString();
}

Deno.serve(async (req) => {
  try {
    const url = new URL(req.url);
    let type = url.searchParams.get("type") || url.searchParams.get("topic") || "";
    let id = url.searchParams.get("data.id") || url.searchParams.get("id") || "";
    if (req.method === "POST") {
      try {
        const b = await req.json();
        type = type || b.type || b.topic || "";
        id = id || b?.data?.id || b?.id || "";
      } catch (_) { /* corpo vazio */ }
    }
    if (!id) return new Response("ok", { status: 200 }); // ping de teste do MP

    // ── Assinatura recorrente (cartão) ──
    if (type.includes("preapproval")) {
      const pa = await mpGet(`/preapproval/${id}`);
      if (pa?.external_reference) {
        const novo = (pa.status === "authorized")
          ? { status: "ativa", inicio: new Date().toISOString(), fim: null }
          : { status: "cancelada" };
        await sb.from("assinaturas")
          .update({ ...novo, mp_preapproval_id: String(id), atualizado_em: new Date().toISOString() })
          .eq("id", pa.external_reference);
      }
      return new Response("ok", { status: 200 });
    }

    // ── Pagamento (Pix avulso, ou cobrança de um recorrente) ──
    if (type.includes("payment")) {
      const pay = await mpGet(`/v1/payments/${id}`);
      const ref = pay?.external_reference;
      if (pay && ref && pay.status === "approved") {
        const { data: ass } = await sb.from("assinaturas")
          .select("id, plano, metodo").eq("id", ref).single();
        if (ass) {
          const meses = ass.plano === "anual" ? 12 : 1;
          const patch: Record<string, unknown> = {
            status: "ativa", mp_payment_id: String(id),
            inicio: new Date().toISOString(), atualizado_em: new Date().toISOString(),
          };
          // avulso (Pix): vale por 1 período; recorrente: sem expiração (vale enquanto authorized)
          patch.fim = ass.metodo === "avulso" ? addMonths(meses) : null;
          await sb.from("assinaturas").update(patch).eq("id", ass.id);
        }
      }
      return new Response("ok", { status: 200 });
    }

    return new Response("ok", { status: 200 });
  } catch (_) {
    // sempre 200: senão o MP fica reenviando. Erros internos a gente loga.
    return new Response("ok", { status: 200 });
  }
});
