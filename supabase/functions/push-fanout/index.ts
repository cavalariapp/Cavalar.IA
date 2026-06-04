// ═══════════════════════════════════════════════════════════════════
// PUSH-FANOUT — Supabase Edge Function (Deno)
//
// Recebe um Database Webhook (INSERT em direct_messages OU follows) e
// dispara Web Push pros dispositivos do DESTINATÁRIO — assim a notificação
// chega no celular mesmo com o app FECHADO.
//
// Fluxo:
//   1. Webhook do Postgres → POST aqui com { type, table, record }.
//   2. Descobre quem deve ser avisado (destinatario / followed_id).
//   3. Lê as PushSubscriptions desse usuário (service_role, ignora RLS).
//   4. Envia Web Push (VAPID) pra cada uma; remove as expiradas (404/410).
//
// Deploy:
//   supabase functions deploy push-fanout --no-verify-jwt
//
// Secrets necessários (NUNCA no frontend):
//   supabase secrets set VAPID_PUBLIC_KEY=...   (a MESMA chave pública do front)
//   supabase secrets set VAPID_PRIVATE_KEY=...  (SECRETA — só aqui)
//   supabase secrets set VAPID_SUBJECT=mailto:epona.perinatologia@gmail.com
//   supabase secrets set WEBHOOK_SECRET=<um-segredo-forte>  (header x-webhook-secret)
//
// SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY já são injetados automaticamente.
// ═══════════════════════════════════════════════════════════════════

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.0";
import webpush from "npm:web-push@3.6.7";

const SUPABASE_URL   = Deno.env.get("SUPABASE_URL")!;
const SERVICE_ROLE   = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const VAPID_PUBLIC   = Deno.env.get("VAPID_PUBLIC_KEY")!;
const VAPID_PRIVATE  = Deno.env.get("VAPID_PRIVATE_KEY")!;
const VAPID_SUBJECT  = Deno.env.get("VAPID_SUBJECT") || "mailto:epona.perinatologia@gmail.com";
const WEBHOOK_SECRET = Deno.env.get("WEBHOOK_SECRET") || "";

// Só configura o VAPID se os secrets existirem — assim a função não
// quebra no boot caso ainda não tenham sido setados (dá erro claro 503).
const VAPID_OK = !!(VAPID_PUBLIC && VAPID_PRIVATE);
if (VAPID_OK) webpush.setVapidDetails(VAPID_SUBJECT, VAPID_PUBLIC, VAPID_PRIVATE);

// service_role: server-side, ignora RLS de propósito (precisa ler as
// inscrições do destinatário, que não é o "auth.uid()" desta chamada).
const sb = createClient(SUPABASE_URL, SERVICE_ROLE, { auth: { persistSession: false } });

const JSON_HEADERS = { "Content-Type": "application/json" };

async function nomePublico(id: string): Promise<string> {
  try {
    const { data } = await sb.from("profiles_publicos").select("nome_completo").eq("id", id).maybeSingle();
    return (data?.nome_completo as string) || "Alguém";
  } catch (_) { return "Alguém"; }
}

// Devolve a lista de DESTINATÁRIOS + o conteúdo. DM/seguidor = 1 destinatário;
// avisos_torneio = LEQUE pros favoritos daquele torneio (programa/adendo/horário/
// ordem/resultado novos).
type Aviso = { recipients: string[]; title: string; body: string; destino: string };

async function montarAviso(payload: any): Promise<Aviso | null> {
  if (!payload || payload.type !== "INSERT" || !payload.record) return null;
  const r = payload.record;

  if (payload.table === "direct_messages") {
    if (!r.destinatario || r.remetente === r.destinatario) return null;
    const nome = await nomePublico(r.remetente);
    const preview = r.texto || (r.imagem_url ? "📷 Foto" : "Nova mensagem");
    return { recipients: [r.destinatario], title: "💬 " + nome, body: preview, destino: "mensagens" };
  }

  if (payload.table === "follows") {
    if (!r.followed_id || r.follower_id === r.followed_id) return null;
    const nome = await nomePublico(r.follower_id);
    if (r.status === "pendente")
      return { recipients: [r.followed_id], title: "🔔 Nova solicitação", body: nome + " quer te seguir", destino: "perfil" };
    return { recipients: [r.followed_id], title: "👋 Novo seguidor", body: nome + " começou a te seguir", destino: "perfil" };
  }

  if (payload.table === "avisos_torneio") {
    if (!r.torneio_id) return null;
    const { data: fav } = await sb.from("torneios_favoritos").select("user_id").eq("torneio_id", r.torneio_id);
    const recipients = [...new Set((fav || []).map((x: any) => x.user_id).filter(Boolean))];
    if (!recipients.length) return null;
    const { data: t } = await sb.from("torneios").select("nome").eq("id", r.torneio_id).maybeSingle();
    const nomeT = (t?.nome as string) || "Torneio";
    const tipo = String(r.tipo || "").toLowerCase();
    let title: string, body: string;
    if (tipo === "ordem")          { title = "🏁 Ordem de entrada"; body = nomeT + ": ordem de entrada publicada"; }
    else if (tipo === "resultado") { title = "🏆 Resultados"; body = nomeT + ": resultados publicados"; }
    else                           { title = "📋 " + nomeT; body = (r.titulo || "Novo documento") + " — publicado"; }
    return { recipients, title, body, destino: "resultados" };
  }

  return null;
}

Deno.serve(async (req) => {
  if (req.method !== "POST")
    return new Response(JSON.stringify({ erro: "use POST" }), { status: 405, headers: JSON_HEADERS });

  if (!VAPID_OK)
    return new Response(JSON.stringify({ erro: "VAPID não configurado: defina VAPID_PUBLIC_KEY e VAPID_PRIVATE_KEY nos secrets" }), { status: 503, headers: JSON_HEADERS });

  // Segredo compartilhado: o webhook envia 'x-webhook-secret'. Sem ele
  // (se WEBHOOK_SECRET estiver setado), rejeita — evita que terceiros
  // disparem pushes chamando a URL pública da função.
  if (WEBHOOK_SECRET && req.headers.get("x-webhook-secret") !== WEBHOOK_SECRET)
    return new Response(JSON.stringify({ erro: "não autorizado" }), { status: 401, headers: JSON_HEADERS });

  let payload: any;
  try { payload = await req.json(); }
  catch { return new Response(JSON.stringify({ erro: "json inválido" }), { status: 400, headers: JSON_HEADERS }); }

  const aviso = await montarAviso(payload);
  if (!aviso) return new Response(JSON.stringify({ pulado: true }), { headers: JSON_HEADERS });

  const { data: subs } = await sb
    .from("push_subscriptions")
    .select("id, endpoint, p256dh, auth")
    .in("user_id", aviso.recipients);

  if (!subs || !subs.length)
    return new Response(JSON.stringify({ enviados: 0, motivo: "sem inscrições" }), { headers: JSON_HEADERS });

  const notif = JSON.stringify({ title: aviso.title, body: aviso.body, data: { destino: aviso.destino } });

  let enviados = 0, removidos = 0;
  await Promise.all((subs as any[]).map(async (s) => {
    const subscription = { endpoint: s.endpoint, keys: { p256dh: s.p256dh, auth: s.auth } };
    try {
      await webpush.sendNotification(subscription, notif, { TTL: 86400 });
      enviados++;
    } catch (e: any) {
      const code = e?.statusCode;
      // 404/410 = inscrição morta (desinstalou / expirou) → limpa.
      if (code === 404 || code === 410) {
        try { await sb.from("push_subscriptions").delete().eq("id", s.id); removidos++; } catch (_) {}
      } else {
        console.warn("[push] falha", code, e?.body || e?.message);
      }
    }
  }));

  return new Response(JSON.stringify({ enviados, removidos, destinatarios: aviso.recipients.length }), { headers: JSON_HEADERS });
});
