-- 052 — cache dos preapproval_plan do Mercado Pago (cria 1x, reusa sempre)
-- A edge (service_role) cria o plano no MP na 1ª assinatura de cada tipo e
-- guarda o id aqui. Só o service_role acessa (RLS sem policy = bloqueado).
create table if not exists public.mp_planos (
  plano       text primary key,          -- 'mensal' | 'anual'
  plan_id     text not null,             -- id do preapproval_plan no MP
  init_point  text,
  criado_em   timestamptz not null default now()
);

alter table public.mp_planos enable row level security;
