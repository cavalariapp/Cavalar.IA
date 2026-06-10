-- 059 — ÍNDICES de performance (acelera perfil de cavalo/cavaleiro/reprodutor)
-- historico_cavalo/historico_cavaleiro faziam SEQ SCAN em ~143k resultados a cada
-- abertura de perfil; progenie escaneava 46k da genealogia. Índices funcionais em
-- norm_nome(...) (IMMUTABLE) tornam tudo busca por índice = instantâneo.
create index if not exists idx_res_cavalo_norm
  on public.resultados (norm_nome(split_part(cavalo_nome, E'\n', 1)));
create index if not exists idx_res_cavaleiro_norm
  on public.resultados (norm_nome(split_part(cavaleiro_nome, E'\n', 1)));
create index if not exists idx_gen_pai_norm
  on public.genealogia (norm_nome(pai));
create index if not exists idx_gen_mae_norm
  on public.genealogia (norm_nome(mae));
