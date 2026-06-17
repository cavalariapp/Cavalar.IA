-- 107 — PODCAST PRÓPRIO: áudio hospedado no app (sem Spotify), exclusivo p/ premium.
--
-- Guarda o MP3 num bucket PRIVADO do Supabase Storage. O acesso é por URL ASSINADA
-- (temporária), que só é gerada para quem é PREMIUM (RLS de leitura). Upload/edição/
-- remoção só para ADMIN. Assim o link não pode ser compartilhado nem aberto por não-
-- assinantes (a URL expira e a leitura é barrada por RLS).

-- (1) coluna na tabela media: caminho do arquivo no Storage (quando preenchido, é um
--     episódio PRÓPRIO → o app toca num player nativo, não no embed do Spotify/YouTube).
alter table public.media add column if not exists audio_path text;

-- (2) bucket privado 'podcasts' (limite 100MB por arquivo).
insert into storage.buckets (id, name, public, file_size_limit)
values ('podcasts', 'podcasts', false, 104857600)
on conflict (id) do update set public = false, file_size_limit = 104857600;

-- (3) RLS no storage.objects (já vem habilitado no Supabase): policies do bucket.
-- LEITURA (gerar URL assinada / baixar) = só PREMIUM.
drop policy if exists "podcasts_premium_select" on storage.objects;
create policy "podcasts_premium_select" on storage.objects
  for select to authenticated
  using (bucket_id = 'podcasts' and public.is_premium());

-- UPLOAD = só ADMIN.
drop policy if exists "podcasts_admin_insert" on storage.objects;
create policy "podcasts_admin_insert" on storage.objects
  for insert to authenticated
  with check (bucket_id = 'podcasts' and public.is_admin());

-- TROCAR arquivo = só ADMIN.
drop policy if exists "podcasts_admin_update" on storage.objects;
create policy "podcasts_admin_update" on storage.objects
  for update to authenticated
  using (bucket_id = 'podcasts' and public.is_admin())
  with check (bucket_id = 'podcasts' and public.is_admin());

-- REMOVER arquivo = só ADMIN.
drop policy if exists "podcasts_admin_delete" on storage.objects;
create policy "podcasts_admin_delete" on storage.objects
  for delete to authenticated
  using (bucket_id = 'podcasts' and public.is_admin());
