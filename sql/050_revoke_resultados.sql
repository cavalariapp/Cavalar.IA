-- 050 — FECHA O GARGALO: revoga o SELECT direto em `resultados`
-- A partir daqui, NINGUÉM (anon/authenticated) lê a tabela direto. Todo acesso
-- passa por RPCs SECURITY DEFINER:
--   FREE   : resultados_prova / resultados_torneio (browse), ranking_zeros,
--            buscar_nomes_cavalos / buscar_nomes_cavaleiros, estatisticas_app
--   PREMIUM: historico_cavalo / historico_cavaleiro / buscar_resultados,
--            stats_cavalo / stats_cavaleiro_filtrado, rankings_geneticos
-- O chatbot (edge) usa service_role (redeploy). Funções já existentes que leem
-- resultados (estatisticas_app, stats_*, buscar_cavalos) já são SECURITY DEFINER.

-- match_cavaleiro (criado fora das migrations, usado no cadastro): garante definer
-- pra sobreviver ao revoke. Se não existir com essa assinatura, ignora.
do $$
begin
  begin
    alter function public.match_cavaleiro(text) security definer;
  exception when undefined_function then null;
  end;
end $$;

revoke select on public.resultados from anon, authenticated, public;
