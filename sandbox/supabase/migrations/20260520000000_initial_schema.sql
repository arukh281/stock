-- Multi-algo paper trading ledger (v1)

CREATE TABLE IF NOT EXISTS portfolio_meta (
  algo_id TEXT PRIMARY KEY,
  cash DOUBLE PRECISION NOT NULL DEFAULT 0,
  equity DOUBLE PRECISION NOT NULL DEFAULT 0,
  starting_capital DOUBLE PRECISION NOT NULL,
  config JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS positions (
  id BIGSERIAL PRIMARY KEY,
  algo_id TEXT NOT NULL REFERENCES portfolio_meta(algo_id) ON DELETE CASCADE,
  symbol TEXT NOT NULL,
  qty DOUBLE PRECISION NOT NULL,
  entry_px DOUBLE PRECISION NOT NULL,
  stop_px DOUBLE PRECISION,
  target_px DOUBLE PRECISION,
  opened_at TIMESTAMPTZ NOT NULL,
  extra JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (algo_id, symbol)
);

CREATE TABLE IF NOT EXISTS pending_orders (
  id BIGSERIAL PRIMARY KEY,
  algo_id TEXT NOT NULL REFERENCES portfolio_meta(algo_id) ON DELETE CASCADE,
  symbol TEXT NOT NULL,
  signal_date DATE,
  trigger_px DOUBLE PRECISION,
  stop_px DOUBLE PRECISION,
  target_px DOUBLE PRECISION,
  deadline_ts TIMESTAMPTZ,
  qty DOUBLE PRECISION,
  status TEXT NOT NULL DEFAULT 'open',
  fill_model TEXT,
  extra JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS closed_trades (
  id BIGSERIAL PRIMARY KEY,
  algo_id TEXT NOT NULL REFERENCES portfolio_meta(algo_id) ON DELETE CASCADE,
  symbol TEXT NOT NULL,
  qty DOUBLE PRECISION NOT NULL,
  entry_date DATE NOT NULL,
  exit_date DATE NOT NULL,
  entry_px DOUBLE PRECISION NOT NULL,
  exit_px DOUBLE PRECISION NOT NULL,
  return_pct DOUBLE PRECISION,
  exit_reason TEXT,
  fill_model TEXT,
  extra JSONB NOT NULL DEFAULT '{}'::jsonb,
  closed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS journal (
  id BIGSERIAL PRIMARY KEY,
  algo_id TEXT NOT NULL REFERENCES portfolio_meta(algo_id) ON DELETE CASCADE,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  symbol TEXT,
  kind TEXT NOT NULL,
  message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
  id BIGSERIAL PRIMARY KEY,
  algo_id TEXT NOT NULL REFERENCES portfolio_meta(algo_id) ON DELETE CASCADE,
  as_of_date DATE NOT NULL,
  cash DOUBLE PRECISION NOT NULL,
  equity DOUBLE PRECISION NOT NULL,
  benchmark JSONB,
  UNIQUE (algo_id, as_of_date)
);

CREATE TABLE IF NOT EXISTS run_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  algo_id TEXT NOT NULL REFERENCES portfolio_meta(algo_id) ON DELETE CASCADE,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'running',
  error_message TEXT,
  summary JSONB,
  raw_stdout TEXT
);

CREATE INDEX IF NOT EXISTS idx_positions_algo ON positions(algo_id);
CREATE INDEX IF NOT EXISTS idx_pending_algo_status ON pending_orders(algo_id, status);
CREATE INDEX IF NOT EXISTS idx_journal_algo_ts ON journal(algo_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_closed_algo ON closed_trades(algo_id, closed_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_logs_algo ON run_logs(algo_id, started_at DESC);

ALTER TABLE portfolio_meta ENABLE ROW LEVEL SECURITY;
ALTER TABLE positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE pending_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE closed_trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal ENABLE ROW LEVEL SECURITY;
ALTER TABLE equity_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_logs ENABLE ROW LEVEL SECURITY;

-- Seed isolated paper portfolios (₹15k each; 44ma = full_ladder main config)
INSERT INTO portfolio_meta (algo_id, cash, equity, starting_capital, config) VALUES
  ('44ma', 15000, 15000, 15000, '{"source": "ma44.config.json", "variant": "full_ladder"}'),
  ('44ma_stacked_2ma', 15000, 15000, 15000, '{"source": "ma44.config.stacked_2ma", "variant": "stacked_2ma"}'),
  ('financially_free', 15000, 15000, 15000, '{"universe": "midcap150"}'),
  ('kali', 15000, 15000, 15000, '{"default_portfolio_inr": 15000}')
ON CONFLICT (algo_id) DO NOTHING;
