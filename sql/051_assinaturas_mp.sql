-- 051 — campos extras p/ o fluxo Mercado Pago
--   metodo        : 'recorrente' (cartão, renova sozinho) | 'avulso' (Pix, paga o período)
--   mp_payment_id : id do pagamento avulso aprovado (Pix)
-- (mp_preapproval_id já existe, p/ a assinatura recorrente do cartão)
alter table public.assinaturas add column if not exists metodo text;
alter table public.assinaturas add column if not exists mp_payment_id text;

create index if not exists idx_assinaturas_payment on public.assinaturas(mp_payment_id)
  where mp_payment_id is not null;
