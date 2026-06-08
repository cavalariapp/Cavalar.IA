-- 030 — ADMIN de NOTÍCIAS
-- Modelo: usa o login que o app já tem (Supabase Auth + tabela profiles).
-- Um admin é um profile com is_admin = true. O público continua só LENDO
-- notícias; apenas admins logados podem inserir/editar/excluir. O scraper
-- continua gravando normalmente (usa a service_role, que ignora RLS).

-- 1) flag de admin no perfil
alter table public.profiles
  add column if not exists is_admin boolean not null default false;

-- 2) função SECURITY DEFINER (lê profiles ignorando a RLS de profiles, de forma
--    segura e estável) — evita recursão/permissão nas policies de news.
create or replace function public.is_admin()
returns boolean
language sql
security definer
stable
set search_path = public
as $$
  select coalesce((select is_admin from public.profiles where id = auth.uid()), false);
$$;

-- 3) RLS de news: público lê; admin escreve.
alter table public.news enable row level security;

drop policy if exists news_public_read on public.news;
create policy news_public_read on public.news
  for select using (true);

drop policy if exists news_admin_insert on public.news;
create policy news_admin_insert on public.news
  for insert with check (public.is_admin());

drop policy if exists news_admin_update on public.news;
create policy news_admin_update on public.news
  for update using (public.is_admin()) with check (public.is_admin());

drop policy if exists news_admin_delete on public.news;
create policy news_admin_delete on public.news
  for delete using (public.is_admin());

-- 4) TORNE-SE ADMIN — rode com o seu e-mail (precisa já ter feito login 1x no app
--    pra existir o profile):
--    update public.profiles set is_admin = true
--      where email = 'epona.perinatologia@gmail.com';
