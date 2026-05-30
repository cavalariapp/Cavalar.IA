-- ═══════════════════════════════════════════════════════════════════
-- Migração 018 — Habilita Supabase Realtime para mensagens e follows
--
--  • Adiciona direct_messages e follows à publicação supabase_realtime,
--    para o cliente receber INSERT/UPDATE/DELETE em tempo real.
--  • A RLS continua valendo no Realtime: cada usuário só recebe as linhas
--    que já poderia ler via SELECT (destinatário das DMs; dono dos follows).
--  • REPLICA IDENTITY FULL em follows → eventos de UPDATE/DELETE trazem a
--    linha antiga completa (útil pra reconciliação no cliente).
--  • Idempotente: pode rodar várias vezes sem erro.
-- ═══════════════════════════════════════════════════════════════════

-- ──────────────── Publicação realtime ──────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public' AND tablename = 'direct_messages'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.direct_messages;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public' AND tablename = 'follows'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.follows;
  END IF;
END $$;

-- ──────────────── Replica identity (old row em UPDATE/DELETE) ───────
ALTER TABLE public.follows         REPLICA IDENTITY FULL;
ALTER TABLE public.direct_messages REPLICA IDENTITY FULL;

SELECT 'OK' AS resultado;
