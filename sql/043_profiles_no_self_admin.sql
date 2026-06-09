-- 043 — FECHA ESCALONAMENTO DE PRIVILÉGIO EM profiles (auto-promoção a admin)
--
-- Estado encontrado na auditoria:
--   • RLS de profiles OK (insert/update/delete só na própria linha: auth.uid()=id).
--   • PII protegida por GRANT de coluna no SELECT (email/celular/idade fora). OK.
--   • PORÉM anon/authenticated tinham GRANT de INSERT e UPDATE em TODAS as colunas,
--     inclusive is_admin. Como a policy de UPDATE libera a própria linha, um usuário
--     logado podia rodar  update profiles set is_admin=true where id=<seu_id>  e
--     se tornar admin (ganhando escrita em news/media/resultados/genealogia...).
--
-- O app NUNCA grava is_admin pelo cliente — quem promove admin é SQL/service_role
-- (vide migration 030). Logo, revogar a escrita dessa coluna dos papéis públicos
-- não quebra nenhum fluxo e elimina o vetor de escalonamento.
--
-- (Não mexemos nas policies nem nas demais colunas: o usuário continua editando
--  o próprio nome/email/bio/etc normalmente.)

revoke insert (is_admin) on public.profiles from anon, authenticated;
revoke update (is_admin) on public.profiles from anon, authenticated;
