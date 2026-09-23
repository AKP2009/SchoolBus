-- =====================================================================
-- 002_storage.sql — Storage buckets and object policies (docs/supabase.md §7)
-- The backend uses the service-role key and bypasses these policies.
-- =====================================================================

insert into storage.buckets (id, name, public) values
  ('incident-media',   'incident-media',   false),  -- {site_id}/{client_id}/{n}.jpg
  ('training-content', 'training-content', true),   -- {module_id}/…
  ('documents',        'documents',        false)   -- {doc_type}/{file}
on conflict do nothing;

-- incident-media: any signed-in user can upload and view incident photos
create policy "incident media upload" on storage.objects for insert to authenticated
  with check (bucket_id = 'incident-media');
create policy "incident media read" on storage.objects for select to authenticated
  using (bucket_id = 'incident-media');

-- training-content: public bucket, so reads go through public URLs; managers manage the files
create policy "training content manage insert" on storage.objects for insert to authenticated
  with check (bucket_id = 'training-content' and public.is_manager());
create policy "training content manage update" on storage.objects for update to authenticated
  using (bucket_id = 'training-content' and public.is_manager());
create policy "training content manage delete" on storage.objects for delete to authenticated
  using (bucket_id = 'training-content' and public.is_manager());

-- documents: RAG source files, managers only (ingest runs with the service role)
create policy "documents manager read" on storage.objects for select to authenticated
  using (bucket_id = 'documents' and public.is_manager());
create policy "documents manager insert" on storage.objects for insert to authenticated
  with check (bucket_id = 'documents' and public.is_manager());
create policy "documents manager update" on storage.objects for update to authenticated
  using (bucket_id = 'documents' and public.is_manager());
create policy "documents manager delete" on storage.objects for delete to authenticated
  using (bucket_id = 'documents' and public.is_manager());
