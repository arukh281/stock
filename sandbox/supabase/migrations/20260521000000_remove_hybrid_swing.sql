-- Remove hybrid_swing algo from sandbox (cascade deletes ledger rows)
DELETE FROM portfolio_meta WHERE algo_id = 'hybrid_swing';
