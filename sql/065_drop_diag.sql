-- 065 — remove as funções de diagnóstico (liam resultados via definer; não deixar expostas)
drop function if exists public.diag_cavalo(text);
drop function if exists public.diag_torneio(bigint);
drop function if exists public.diag_altura_cavalo(text);
