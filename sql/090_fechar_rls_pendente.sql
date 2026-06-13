-- 090 — FECHA os achados da auditoria 089.
-- 6 tabelas estavam SEM RLS (anon lia E escrevia). Nenhuma é usada pelo front;
-- quem as alimenta é o scraper (service_role, que IGNORA RLS). Logo, a trava
-- máxima (RLS ligado, SEM policy = ninguém acessa pela API anon/authenticated)
-- é segura e não quebra nada. Funções de manutenção saem do alcance público.

-- ── 1) Tabelas internas/legado → RLS deny-all (só service_role/RPC definer) ──
alter table public.categorias          enable row level security;
alter table public.competition_classes enable row level security;
alter table public.competitions        enable row level security;
alter table public.torneios_ignorados  enable row level security;
alter table public.resultados_bkp_027  enable row level security;
alter table public.resultados_bkp_028  enable row level security;

-- (defesa extra: tira o GRANT amplo de escrita que o anon tinha nelas)
revoke insert, update, delete on public.categorias          from anon, authenticated;
revoke insert, update, delete on public.competition_classes from anon, authenticated;
revoke insert, update, delete on public.competitions        from anon, authenticated;
revoke insert, update, delete on public.torneios_ignorados  from anon, authenticated;
revoke insert, update, delete on public.resultados_bkp_027  from anon, authenticated;
revoke insert, update, delete on public.resultados_bkp_028  from anon, authenticated;

-- ── 2) Funções de manutenção fora do alcance público (anti-abuso de recurso) ──
-- refresh_genetica() é pesada; só o scraper (service_role) precisa dela.
revoke execute on function public.refresh_genetica()  from anon, authenticated;
-- _tg_provas_count() é função de TRIGGER (roda sozinha); ninguém chama direto.
revoke execute on function public._tg_provas_count()  from anon, authenticated;

-- ── 3) VERIFICAÇÃO (leitura): colunas que a view profiles_publicos expõe.
--      NÃO pode aparecer email, celular, idade nem is_admin.
select string_agg(a.attname, ', ' order by a.attnum) as colunas_da_view
from pg_attribute a
join pg_class c on c.oid = a.attrelid
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname = 'profiles_publicos'
  and a.attnum > 0 and not a.attisdropped;
