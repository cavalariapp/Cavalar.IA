-- 078 — TOKEN do pai e da mãe na genealogia (desambiguação EXATA de reprodutor).
-- A ABCCH guarda pai/mãe por NOME no /pesquisa/, mas o detalhe /animais/<token>
-- traz CdTokenSire e CdTokenDam = o TOKEN do animal pai/mãe (vínculo direto, único).
-- Com isso, duas "Olanda" diferentes (tokens diferentes) NUNCA mais se fundem — sem
-- heurística de idade. As colunas são preenchidas pelo scraper (--abcch-detalhe), que
-- busca o detalhe de cada animal e grava o token do pai/mãe.

alter table public.genealogia add column if not exists pai_token text;
alter table public.genealogia add column if not exists mae_token text;

create index if not exists genealogia_pai_token_idx on public.genealogia (pai_token);
create index if not exists genealogia_mae_token_idx on public.genealogia (mae_token);
