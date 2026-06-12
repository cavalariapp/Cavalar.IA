-- 082 — APELIDOS de cavalo (merge manual, curado por admin).
-- Caso clássico: o proprietário acrescenta iniciais ao nome do cavalo comprado
-- (ex.: "Lika JT" → "Lika JT PB"), e o sistema os trata como dois animais. NÃO dá
-- pra fundir automático (arriscaria juntar homônimos REAIS), então o admin mapeia os
-- casos que vê. A tabela é pequena e consultada na resolução do nome.

create table if not exists public.cavalo_alias (
  alias_norm     text primary key,   -- norm_nome do nome VARIANTE (ex.: 'LIKA JT PB')
  canonico_norm  text not null,      -- norm_nome do nome OFICIAL/canônico (ex.: 'LIKA JT')
  criado_em      timestamptz not null default now()
);
alter table public.cavalo_alias enable row level security;  -- sem policy → só service_role/RPC

-- resolve um norm-name pro canônico (default = ele mesmo). STABLE (lê a tabela).
create or replace function public.canon_cavalo(p_norm text)
returns text language sql stable set search_path = public as $$
  select coalesce((select canonico_norm from public.cavalo_alias where alias_norm = p_norm), p_norm);
$$;

-- RPC ADMIN: cria/atualiza um apelido. Passe os NOMES como aparecem (a função
-- normaliza). Ex.: select public.set_cavalo_alias('Lika JT PB', 'Lika JT');
create or replace function public.set_cavalo_alias(p_alias text, p_canonico text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  insert into public.cavalo_alias (alias_norm, canonico_norm)
  values (norm_nome(p_alias), norm_nome(p_canonico))
  on conflict (alias_norm) do update set canonico_norm = excluded.canonico_norm;
end;
$$;
revoke all on function public.set_cavalo_alias(text, text) from public, anon;
grant execute on function public.set_cavalo_alias(text, text) to authenticated;

-- ── historico_cavalo passa a resolver apelidos (efeito IMEDIATO no perfil do cavalo:
--    clicar em "Lika JT" OU "Lika JT PB" mostra os resultados das duas grafias) ──
create or replace function public.historico_cavalo(p_nome text)
returns table (
  colocacao text, penalidade text, tempo text, cavaleiro_nome text, cavalo_nome text,
  prova_nome text, prova_descricao text, prova_categorias text, data_prova date,
  torneio_nome text, torneio_data date
)
language plpgsql stable security definer set search_path = public as $$
declare v_nasc int; v_key text := public.canon_cavalo(norm_nome(split_part(p_nome, E'\n', 1)));
begin
  if not public.is_premium() then
    raise exception 'premium_required' using errcode = '42501';
  end if;
  select min(extract(year from g.nascimento))::int into v_nasc
  from genealogia g
  where public.canon_cavalo(norm_nome(g.nome)) = v_key;

  return query
    select r.colocacao, r.penalidade, r.tempo, r.cavaleiro_nome, r.cavalo_nome,
           p.nome, p.descricao, p.categorias, p.data_prova,
           t.nome, t.data_inicio::date
    from resultados r
    join provas p   on p.id = r.prova_id
    left join torneios t on t.id = p.torneio_id
    where public.canon_cavalo(norm_nome(split_part(r.cavalo_nome, E'\n', 1))) = v_key
      and (
        v_nasc is null
        or public.altura_m(p.nome, p.descricao, p.categorias) is null
        or extract(year from coalesce(p.data_prova, t.data_inicio)) is null
        or public.altura_m(p.nome, p.descricao, p.categorias)
             <= public.alt_max_para_idade(extract(year from coalesce(p.data_prova, t.data_inicio))::int - v_nasc)
      )
    limit 2000;
end;
$$;
grant execute on function public.historico_cavalo(text) to anon, authenticated;

-- O caso pedido: Lika JT PB é a mesma égua que Lika JT.
insert into public.cavalo_alias (alias_norm, canonico_norm)
values (norm_nome('Lika JT PB'), norm_nome('Lika JT'))
on conflict (alias_norm) do update set canonico_norm = excluded.canonico_norm;
