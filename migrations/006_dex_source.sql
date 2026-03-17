-- Migration 006: Add dex_source column to track which DEX factory created the pool
-- Values: uniswap_v3, uniswap_v2, aerodrome, unknown

ALTER TABLE launches ADD COLUMN IF NOT EXISTS dex_source TEXT NOT NULL DEFAULT 'unknown';

-- Backfill existing launches (all were from V3 before this migration)
UPDATE launches SET dex_source = 'uniswap_v3' WHERE dex_source = 'unknown';

-- Index for filtering by DEX
CREATE INDEX IF NOT EXISTS idx_launches_dex_source ON launches (dex_source);
