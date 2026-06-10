-- 066 — CUPONS de premium (p/ testadores acessarem premium sem pagar)
-- Um cupom concede N dias de premium. Resgatado por uma RPC (cria uma assinatura
-- 'ativa' com metodo='cupom'). Admin cria cupons por SQL (exemplo no fim).
create table if not exists public.cupons (
  codigo     text primary key,                -- ex.: 'BETA2026'
  dias       int  not null default 30,        -- dias de premium concedidos
  max_usos   int,                              -- null = ilimitado
  usos       int  not null default 0,
  ativo      boolean not null default true,
  expira_em  date,                             -- validade do cupom (null = sem validade)
  descricao  text,
  criado_em  timestamptz not null default now()
);
alter table public.cupons enable row level security;   -- sem policy → só service_role/RPC

create or replace function public.resgatar_cupom(p_codigo text)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare v public.cupons; v_uid uuid := auth.uid();
begin
  if v_uid is null then return json_build_object('ok', false, 'erro', 'Entre na sua conta para resgatar.'); end if;
  select * into v from public.cupons where codigo = upper(trim(p_codigo));
  if not found then return json_build_object('ok', false, 'erro', 'Cupom inválido.'); end if;
  if not v.ativo then return json_build_object('ok', false, 'erro', 'Cupom desativado.'); end if;
  if v.expira_em is not null and v.expira_em < current_date then return json_build_object('ok', false, 'erro', 'Cupom expirado.'); end if;
  if v.max_usos is not null and v.usos >= v.max_usos then return json_build_object('ok', false, 'erro', 'Cupom esgotado.'); end if;
  if exists (select 1 from public.assinaturas a where a.profile_id = v_uid and a.status = 'ativa' and (a.fim is null or a.fim > now()))
    then return json_build_object('ok', false, 'erro', 'Você já tem acesso premium ativo.'); end if;
  insert into public.profiles (id, visibilidade) values (v_uid, 'publico') on conflict (id) do nothing;
  insert into public.assinaturas (profile_id, status, plano, metodo, inicio, fim, valor)
  values (v_uid, 'ativa', 'cupom', 'cupom', now(), now() + (v.dias || ' days')::interval, 0);
  update public.cupons set usos = usos + 1 where codigo = v.codigo;
  return json_build_object('ok', true, 'dias', v.dias);
end;
$$;
revoke all on function public.resgatar_cupom(text) from public, anon;
grant execute on function public.resgatar_cupom(text) to authenticated;

-- EXEMPLO de criação de cupom (rode quando quiser; ajuste código/dias/usos):
-- insert into public.cupons (codigo, dias, max_usos, descricao)
-- values ('BETA2026', 120, 20, 'Testadores beta — 120 dias premium');
