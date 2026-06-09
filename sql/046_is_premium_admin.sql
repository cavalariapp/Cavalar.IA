-- 046 — admin conta como premium (pra você acessar tudo sem assinar) e o
-- premium nunca depende de assinatura quando o usuário é admin.
create or replace function public.is_premium(uid uuid default auth.uid())
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select
    coalesce((select is_admin from public.profiles where id = uid), false)
    or exists (
      select 1 from public.assinaturas a
      where a.profile_id = uid
        and a.status = 'ativa'
        and (a.fim is null or a.fim > now())
    );
$$;

grant execute on function public.is_premium(uuid) to anon, authenticated;
