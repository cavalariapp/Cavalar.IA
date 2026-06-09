-- 054 — PATROCINADORES (vitrine de parceiros & ofertas, visível a TODOS)
-- Leilões, criadores e produtos equestres com promoções. Público lê; admin gere.
create table if not exists public.patrocinadores (
  id         uuid primary key default gen_random_uuid(),
  nome       text not null,
  imagem_url text,                       -- banner/logo
  link       text,                       -- destino ao clicar
  tipo       text,                        -- 'leilao' | 'criador' | 'produto'
  descricao  text,                        -- ex.: "20% OFF até domingo"
  posicao    text not null default 'home',
  ordem      int  not null default 0,
  ativo      boolean not null default true,
  criado_em  timestamptz not null default now()
);
create index if not exists idx_patrocinadores_ativo on public.patrocinadores(ativo, posicao, ordem);

alter table public.patrocinadores enable row level security;

drop policy if exists patrocinadores_public_read on public.patrocinadores;
create policy patrocinadores_public_read on public.patrocinadores for select using (true);

drop policy if exists patrocinadores_admin_ins on public.patrocinadores;
create policy patrocinadores_admin_ins on public.patrocinadores for insert with check (public.is_admin());
drop policy if exists patrocinadores_admin_upd on public.patrocinadores;
create policy patrocinadores_admin_upd on public.patrocinadores for update using (public.is_admin()) with check (public.is_admin());
drop policy if exists patrocinadores_admin_del on public.patrocinadores;
create policy patrocinadores_admin_del on public.patrocinadores for delete using (public.is_admin());

grant select on public.patrocinadores to anon, authenticated;
grant insert, update, delete on public.patrocinadores to authenticated;
