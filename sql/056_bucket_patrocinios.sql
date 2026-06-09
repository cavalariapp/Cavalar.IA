-- 056 — bucket de Storage para os banners de patrocínio (upload pelo admin)
insert into storage.buckets (id, name, public)
values ('patrocinios', 'patrocinios', true)
on conflict (id) do nothing;

-- leitura pública (bucket é público; policy garante o SELECT)
drop policy if exists patro_obj_read on storage.objects;
create policy patro_obj_read on storage.objects
  for select using (bucket_id = 'patrocinios');

-- enviar/alterar/apagar só admin
drop policy if exists patro_obj_ins on storage.objects;
create policy patro_obj_ins on storage.objects
  for insert with check (bucket_id = 'patrocinios' and public.is_admin());

drop policy if exists patro_obj_upd on storage.objects;
create policy patro_obj_upd on storage.objects
  for update using (bucket_id = 'patrocinios' and public.is_admin());

drop policy if exists patro_obj_del on storage.objects;
create policy patro_obj_del on storage.objects
  for delete using (bucket_id = 'patrocinios' and public.is_admin());
