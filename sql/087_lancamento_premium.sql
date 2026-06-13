-- 087 — LANÇAMENTO: (1) premium VITALÍCIO para todos os cadastrados até agora;
--                    (2) cupom pode conceder premium vitalício (além de por dias).
--
-- Política do lançamento: quem JÁ tem conta vira fundador (premium pra sempre);
-- novos usuários passam a pagar no Mercado Pago. is_premium() já trata fim=null
-- como "sem expiração", então basta uma assinatura 'ativa' com fim=null.

-- ── (1) CONCESSÃO VITALÍCIA aos cadastrados de hoje ─────────────────────────────
-- ⚠️ RODE ISSO NO MOMENTO DO LANÇAMENTO (captura exatamente quem já está cadastrado).
-- Idempotente: não duplica (só insere pra quem ainda não tem a cortesia 'fundador').
insert into public.assinaturas (profile_id, status, plano, metodo, inicio, fim, valor)
select p.id, 'ativa', 'vitalicio', 'fundador', now(), null, 0
from public.profiles p
where not exists (
  select 1 from public.assinaturas a
  where a.profile_id = p.id and a.metodo = 'fundador'
);

-- ── (2) CUPOM com opção VITALÍCIA ───────────────────────────────────────────────
alter table public.cupons add column if not exists vitalicio boolean not null default false;
alter table public.cupons alter column dias drop not null;   -- vitalício não usa dias

create or replace function public.resgatar_cupom(p_codigo text)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare v public.cupons; v_uid uuid := auth.uid(); v_fim timestamptz;
begin
  if v_uid is null then return json_build_object('ok', false, 'erro', 'Entre na sua conta para resgatar.'); end if;
  select * into v from public.cupons where codigo = upper(trim(p_codigo));
  if not found then return json_build_object('ok', false, 'erro', 'Cupom inválido.'); end if;
  if not v.ativo then return json_build_object('ok', false, 'erro', 'Cupom desativado.'); end if;
  if v.expira_em is not null and v.expira_em < current_date then return json_build_object('ok', false, 'erro', 'Cupom expirado.'); end if;
  if v.max_usos is not null and v.usos >= v.max_usos then return json_build_object('ok', false, 'erro', 'Cupom esgotado.'); end if;
  if exists (select 1 from public.assinaturas a where a.profile_id = v_uid and a.status = 'ativa' and (a.fim is null or a.fim > now()))
    then return json_build_object('ok', false, 'erro', 'Você já tem acesso premium ativo.'); end if;

  v_fim := case when v.vitalicio then null else now() + (coalesce(v.dias, 30) || ' days')::interval end;
  insert into public.profiles (id, visibilidade) values (v_uid, 'publico') on conflict (id) do nothing;
  insert into public.assinaturas (profile_id, status, plano, metodo, inicio, fim, valor)
  values (v_uid, 'ativa', case when v.vitalicio then 'vitalicio' else 'cupom' end, 'cupom', now(), v_fim, 0);
  update public.cupons set usos = usos + 1 where codigo = v.codigo;
  return json_build_object('ok', true, 'vitalicio', v.vitalicio, 'dias', v.dias);
end;
$$;
revoke all on function public.resgatar_cupom(text) from public, anon;
grant execute on function public.resgatar_cupom(text) to authenticated;

-- ── (3) EXEMPLO: crie o cupom de cortesia que você vai distribuir ────────────────
-- VITALÍCIO (premium pra sempre), até 50 usos:
insert into public.cupons (codigo, vitalicio, max_usos, descricao)
values ('CAVALARIA', true, 50, 'Cortesia de lançamento — premium vitalício')
on conflict (codigo) do nothing;
-- (ou um por TEMPO, ex.: 365 dias:)
-- insert into public.cupons (codigo, dias, max_usos, descricao)
-- values ('ANO2026', 365, 100, 'Cortesia — 1 ano premium') on conflict (codigo) do nothing;
