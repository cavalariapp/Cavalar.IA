# 🛡️ Segurança do Cavalar.IA

Guia de operação de segurança. Escrito pra ser entendido **sem ser desenvolvedor**.
Última auditoria completa: **2026-06-13** (resultado: sem vazamentos abertos).

---

## 1. O princípio que sustenta tudo

O app é um site estático (HTML/JS) + Supabase (banco Postgres). O frontend carrega
uma chave chamada **anon key**, que é **PÚBLICA de propósito** — ela aparece no código
e tudo bem, ela sozinha não dá acesso a nada sensível.

👉 **Quem realmente protege os dados é o banco**, através de:
- **RLS (Row Level Security):** regras na própria tabela dizendo quem pode ler/escrever
  cada linha. Com a anon key pública, qualquer pessoa pode chamar a API REST direto —
  então **toda tabela precisa de RLS**, senão vaza.
- **Funções gateadas:** funções `SECURITY DEFINER` que checam `is_admin()` / `is_premium()`
  / `auth.uid()` antes de devolver dado.

**As chaves que NÃO podem vazar nunca** (e onde elas moram):
| Chave | Onde mora (correto) | Nunca pode estar |
|---|---|---|
| `service_role` (chave-mestra) | Secrets do Supabase + Secrets do GitHub | No frontend, no repositório, em e‑mail/chat |
| Token Mercado Pago (`APP_USR-…`) | Edge Functions → Secrets | No frontend, no repositório |
| Chave Anthropic / Unsplash / Spotify | Secrets do GitHub | No frontend, no repositório |
| **anon key** | Pode ficar no frontend ✅ | (é pública, sem problema) |

> Colar **código** em algum lugar é seguro — o código só diz "pegue a chave do cofre",
> nunca contém o valor da chave. O perigo é colar o **valor** de uma chave-mestra.

---

## 2. A REGRA DE OURO

> **Toda tabela nova nasce com RLS ligado. Sem exceção.**

Quando criar uma tabela nova (você ou alguém), rode logo em seguida UM dos modelos:

**Modelo A — dado público (todo mundo lê, só admin escreve)** — ex.: notícias, resultados:
```sql
alter table public.NOVA_TABELA enable row level security;
create policy NOVA_TABELA_read  on public.NOVA_TABELA for select using (true);
create policy NOVA_TABELA_ins   on public.NOVA_TABELA for insert with check (public.is_admin());
create policy NOVA_TABELA_upd   on public.NOVA_TABELA for update using (public.is_admin()) with check (public.is_admin());
create policy NOVA_TABELA_del   on public.NOVA_TABELA for delete using (public.is_admin());
```

**Modelo B — dado interno/sensível (ninguém acessa pela API; só o scraper/admin)** — ex.: cupons, planos, backups:
```sql
alter table public.NOVA_TABELA enable row level security;
-- (não cria policy nenhuma = ninguém lê/escreve via API; service_role continua funcionando)
revoke insert, update, delete on public.NOVA_TABELA from anon, authenticated;
```

**Modelo C — dado do dono (cada um só vê/edita o seu)** — ex.: assinaturas, favoritos:
```sql
alter table public.NOVA_TABELA enable row level security;
create policy NOVA_TABELA_own on public.NOVA_TABELA
  for all using (auth.uid() = profile_id) with check (auth.uid() = profile_id);
```

⚠️ **PII (e‑mail, celular, idade, documentos):** nunca exponha em tabela aberta nem em
view pública. O padrão do projeto: revogar a coluna de `anon` e `authenticated`, e o dono
lê via RPC `meu_perfil()`.

---

## 3. Estado atual (auditado em 2026-06-13)

| Área | Situação |
|---|---|
| **Contatos/PII** (e‑mail, celular, idade) | 🟢 Inacessíveis a qualquer um além do dono. |
| **Cartões de crédito** | 🟢 Nunca tocados/guardados — pagamento 100% no Mercado Pago. |
| **Pagamentos** (`assinaturas`) | 🟢 Cada um vê só o seu; escrita só pelo webhook (service_role). |
| **Auto-promoção a admin** | 🟢 Bloqueada. |
| **Conteúdo** (resultados, notícias, etc.) | 🟢 Leitura pública, escrita só admin. |
| **Cupons / planos / apelidos / backups** | 🟢 RLS deny-all (só service_role/RPC). |
| **Mensagens privadas** | 🟢 Só remetente e destinatário leem. |
| **Chatbot IA** | 🟢 Travado por `is_premium()` no servidor. |
| **XSS** (roubo de sessão) | 🟢 Conteúdo de usuário é escapado. |
| **Notificações push** | 🟢 Protegidas por `WEBHOOK_SECRET`. |
| **Secrets no repositório** | 🟢 Nenhum (só a anon key, pública). `.env` no `.gitignore`. |
| **Login** | Magic link/OTP por e‑mail (sem senha) — rate limit padrão ligado. |

---

## 4. Edge Functions (servidores pequenos) e suas travas

| Função | Trava |
|---|---|
| `mp-criar-assinatura` | Exige usuário logado (JWT). |
| `mp-webhook` | Sem JWT (o MP não manda) — valida re-consultando o pagamento na API do MP; idempotente. |
| `chat` | Exige `is_premium()` (não gasta créditos à toa). |
| `push-fanout` | Exige header `x-webhook-secret` = `WEBHOOK_SECRET`. |

---

## 5. Checklist periódico (rode de vez em quando, ex.: a cada lançamento)

1. Rode o script de auditoria **`sql/089_auditoria_seguranca.sql`** no SQL Editor.
2. No **Bloco 1**, qualquer linha **🔴** = tabela sem proteção → aplique o Modelo A, B ou C da seção 2.
3. No **Bloco 4**, `email/celular/idade/is_admin` devem ser **`false`** pra anon e authenticated.
4. Confira que nenhuma chave-mestra foi colada fora do Supabase/GitHub.

> Se alguma chave-mestra (service_role / token MP) for exposta: **rotacione** em
> Supabase → Settings → API → Reset, e atualize o novo valor nos Secrets do GitHub.

---

## 6. Em caso de dúvida

Não improvise em produção. Rode o `sql/089` (é só leitura, não altera nada), veja o
que aparece em vermelho, e trate cada caso com o modelo certo da seção 2.
