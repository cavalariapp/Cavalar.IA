-- 057 — site_settings: leitura pública, escrita só admin (capa da home etc.)
create unique index if not exists uq_site_settings_key on public.site_settings(key);

alter table public.site_settings enable row level security;

drop policy if exists site_settings_read on public.site_settings;
create policy site_settings_read on public.site_settings for select using (true);

drop policy if exists site_settings_admin_ins on public.site_settings;
create policy site_settings_admin_ins on public.site_settings for insert with check (public.is_admin());

drop policy if exists site_settings_admin_upd on public.site_settings;
create policy site_settings_admin_upd on public.site_settings for update using (public.is_admin()) with check (public.is_admin());

grant select on public.site_settings to anon, authenticated;
grant insert, update on public.site_settings to authenticated;
