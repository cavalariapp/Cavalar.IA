-- 033 — GENEALOGIA (studbook ABCCH)
-- Espelha o studbook da ABCCH (api.abcch.com.br) pra ligar os resultados
-- esportivos à filiação (pai/mãe) e gerar estatísticas genéticas. Preenchida
-- pelo scraper (--abcch), que usa a service_role (ignora RLS). Público lê.

create table if not exists public.genealogia (
  cd_token      text primary key,         -- id do animal no ABCCH (UUID)
  nome          text,
  nome_completo text,
  registro      text,
  nascimento    date,
  sexo          text,                      -- 'M' / 'F'
  pai           text,
  mae           text,
  proprietario  text,
  atualizado_em timestamptz default now()
);

-- índices p/ casar por nome e agregar por pai/mãe (normalizado em maiúsculas)
create index if not exists genealogia_nome_idx on public.genealogia (upper(nome));
create index if not exists genealogia_pai_idx  on public.genealogia (upper(pai));
create index if not exists genealogia_mae_idx  on public.genealogia (upper(mae));

alter table public.genealogia enable row level security;
drop policy if exists genealogia_public_read on public.genealogia;
create policy genealogia_public_read on public.genealogia
  for select using (true);
-- escrita só pelo scraper (service_role, que ignora RLS) — sem policy pública.
