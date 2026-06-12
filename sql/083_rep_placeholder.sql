-- 083 — PLACEHOLDERS de reprodutor curáveis por admin.
-- A ABCCH usa nomes-placeholder quando o registro não tem o nome do pai/mãe (ex.:
-- BIBIBEG). Não são matrizes/garanhões de verdade → fora do ranking. Antes só não
-- apareciam por acaso (INNER JOIN escondia os sem prole competindo); com o LEFT JOIN
-- voltaram. Agora há uma LISTA CURADA: admin adiciona os que for vendo.

create table if not exists public.rep_placeholder (
  nome_norm  text primary key,        -- norm_nome do nome-placeholder (ex.: 'BIBIBEG')
  criado_em  timestamptz not null default now()
);
alter table public.rep_placeholder enable row level security;  -- sem policy → só service_role/RPC

-- _rep_placeholder: lista FIXA + a tabela curada. (Agora STABLE — lê a tabela.)
create or replace function public._rep_placeholder(n text)
returns boolean language sql stable set search_path = public as $$
  select n is null
      or n in ('NAO CADASTRADA','NAO CADASTRADO','DESCONHECIDO','DESCONHECIDA',
               'SEM ORIGEM','IMPORTADO','IMPORTADA','SEM REGISTRO')
      or exists (select 1 from public.rep_placeholder where nome_norm = n);
$$;

-- RPC ADMIN: marca um nome como placeholder (sai do ranking). Ex.:
--   select public.add_rep_placeholder('BIBIBEG');
create or replace function public.add_rep_placeholder(p_nome text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  insert into public.rep_placeholder (nome_norm) values (norm_nome(p_nome))
  on conflict (nome_norm) do nothing;
end;
$$;
revoke all on function public.add_rep_placeholder(text) from public, anon;
grant execute on function public.add_rep_placeholder(text) to authenticated;

-- o caso citado
insert into public.rep_placeholder (nome_norm) values (norm_nome('BIBIBEG'))
on conflict (nome_norm) do nothing;
