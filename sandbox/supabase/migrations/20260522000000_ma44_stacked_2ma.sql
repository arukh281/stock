-- Second 44MA paper ledger (stacked_2ma variant). Run in Supabase SQL Editor if not using fresh migrate.

INSERT INTO portfolio_meta (algo_id, cash, equity, starting_capital, config) VALUES
  ('44ma_stacked_2ma', 15000, 15000, 15000, '{"source": "ma44.config.stacked_2ma", "variant": "stacked_2ma"}')
ON CONFLICT (algo_id) DO NOTHING;

-- Optional: tag main 44ma ledger as full_ladder (does not change cash)
UPDATE portfolio_meta
SET config = COALESCE(config, '{}'::jsonb) || '{"variant": "full_ladder", "source": "ma44.config.json"}'::jsonb
WHERE algo_id = '44ma';
