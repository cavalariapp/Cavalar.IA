-- 044 — FUNDAÇÃO DO FREEMIUM: assinaturas + is_premium() + media.premium
-- Premium = existe assinatura 'ativa' não expirada para o usuário.
-- Quem ATIVA/renova é o webhook do Mercado Pago (service_role, ignora RLS).
-- O cliente nunca escreve aqui — só lê a própria assinatura.

create table if not exists public.assinaturas (
  id                uuid primary key default gen_random_uuid(),
  profile_id        uuid not null references public.profiles(id) on delete cascade,
  status            text not null default 'pendente',  -- pendente|ativa|cancelada|expirada
  plano             text not null default 'mensal',     -- mensal|anual
  inicio            timestamptz,
  fim               timestamptz,                         -- até quando o premium vale
  mp_preapproval_id text,                                -- id da assinatura no Mercado Pago
  mp_payer_email    text,
  valor             numeric,
  criado_em         timestamptz not null default now(),
  atualizado_em     timestamptz not null default now()
);

create index if not exists idx_assinaturas_profile on public.assinaturas(profile_id);
create index if not exists idx_assinaturas_status  on public.assinaturas(status, fim);
create unique index if not exists uq_assinaturas_mp on public.assinaturas(mp_preapproval_id)
  where mp_preapproval_id is not null;

-- Helper central: usado tanto pelo cliente (sb.rpc('is_premium')) quanto pelas
-- policies/RPCs de conteúdo premium. SECURITY DEFINER p/ ler assinaturas sem
-- depender da RLS de quem chama.
create or replace function public.is_premium(uid uuid default auth.uid())
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.assinaturas a
    where a.profile_id = uid
      and a.status = 'ativa'
      and (a.fim is null or a.fim > now())
  );
$$;

grant execute on function public.is_premium(uuid) to anon, authenticated;

-- RLS: o dono lê a própria assinatura; escrita só pelo service_role (webhook).
alter table public.assinaturas enable row level security;

drop policy if exists assinaturas_select_own on public.assinaturas;
create policy assinaturas_select_own on public.assinaturas
  for select using (profile_id = auth.uid());
-- (sem policy de insert/update/delete → bloqueado p/ anon e authenticated)

-- Mídia premium (podcast/videocast/videoaula marcados): free vê o cadeado,
-- não toca/assiste. 'videos' já tem flag premium; padronizamos em 'media'.
alter table public.media add column if not exists premium boolean not null default false;
