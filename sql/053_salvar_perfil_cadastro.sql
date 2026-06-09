-- 053 — corrige "permission denied for table profiles" no cadastro
-- Causa: pós-hardening, 'authenticated' só tem SELECT nas colunas não-PII, e o
-- upsert do cadastro (com email/celular/idade) tropeça ao ler de volta (RETURNING)
-- colunas que não pode ver. Solução: criar o próprio perfil via RPC SECURITY
-- DEFINER (roda como owner, ignora os grants de coluna). Só escreve a linha do
-- próprio usuário (id = auth.uid()); nunca seta is_admin.
create or replace function public.salvar_perfil_cadastro(
  p_nome text, p_idade int, p_email text, p_celular text,
  p_estado text, p_pais text, p_tipos text[]
) returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if auth.uid() is null then
    raise exception 'not authenticated';
  end if;
  insert into public.profiles (id, nome_completo, idade, email, celular, estado, pais, tipos, visibilidade)
  values (auth.uid(), p_nome, p_idade, p_email, p_celular, p_estado, p_pais, p_tipos, 'publico')
  on conflict (id) do update set
    nome_completo = excluded.nome_completo,
    idade   = excluded.idade,
    email   = excluded.email,
    celular = excluded.celular,
    estado  = excluded.estado,
    pais    = excluded.pais,
    tipos   = excluded.tipos;
end;
$$;

revoke all on function public.salvar_perfil_cadastro(text,int,text,text,text,text,text[]) from public, anon;
grant execute on function public.salvar_perfil_cadastro(text,int,text,text,text,text,text[]) to authenticated;
