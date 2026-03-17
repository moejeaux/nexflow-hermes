-- Migration 008: v2.1 — Mempool Features, Major Interest, enhanced SM/Whale behavior
-- Adds: mempool_features table, mempool columns on launches, major_interest_flag,
--        enhanced SM/whale cohort fields, missing-data strictness columns.

-- ============================================================
-- MEMPOOL FEATURES TABLE (rolling per-token snapshots)
-- ============================================================

CREATE TABLE IF NOT EXISTS mempool_features (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    launch_id                   UUID NOT NULL REFERENCES launches(launch_id) ON DELETE CASCADE,
    token_address               TEXT NOT NULL,
    snapshot_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Smart-money pending flow
    pending_smart_buy_volume    NUMERIC DEFAULT 0,
    pending_smart_sell_volume   NUMERIC DEFAULT 0,
    pending_smart_buy_ratio     NUMERIC(7,6) DEFAULT 0,   -- vs pool liquidity
    pending_smart_sell_ratio    NUMERIC(7,6) DEFAULT 0,
    pending_smart_buy_count     INT DEFAULT 0,
    pending_smart_sell_count    INT DEFAULT 0,
    pending_smart_buy_fee_urgency_max  NUMERIC(5,2) DEFAULT 0,  -- percentile 0-100
    pending_smart_sell_fee_urgency_max NUMERIC(5,2) DEFAULT 0,

    -- Whale pending flow
    pending_whale_buy_volume    NUMERIC DEFAULT 0,
    pending_whale_sell_volume   NUMERIC DEFAULT 0,
    pending_whale_buy_count     INT DEFAULT 0,
    pending_whale_sell_count    INT DEFAULT 0,

    -- Anomaly density
    tiny_swap_count             INT DEFAULT 0,
    total_pending_swap_count    INT DEFAULT 0,
    tiny_swap_density           NUMERIC(5,4) DEFAULT 0,    -- 0.0000 to 1.0000
    new_addr_tiny_swap_count    INT DEFAULT 0,

    -- Derived flags
    has_strong_pending_smart_buy   BOOLEAN DEFAULT FALSE,
    has_strong_pending_smart_sell  BOOLEAN DEFAULT FALSE,
    has_strong_pending_whale_buy   BOOLEAN DEFAULT FALSE,
    has_strong_pending_whale_sell  BOOLEAN DEFAULT FALSE,
    high_tiny_swap_density         BOOLEAN DEFAULT FALSE,

    -- Pool baseline for ratio computation
    pool_liquidity_usd          NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_mempool_launch       ON mempool_features (launch_id);
CREATE INDEX IF NOT EXISTS idx_mempool_token        ON mempool_features (token_address);
CREATE INDEX IF NOT EXISTS idx_mempool_snapshot     ON mempool_features (snapshot_at DESC);
-- Partition-friendly: recent snapshots per token
CREATE INDEX IF NOT EXISTS idx_mempool_token_time   ON mempool_features (token_address, snapshot_at DESC);

-- ============================================================
-- LAUNCHES TABLE — mempool + major interest columns
-- ============================================================

-- Latest mempool feature snapshot (denormalized for fast query)
ALTER TABLE launches ADD COLUMN IF NOT EXISTS mempool_smart_buy_ratio    NUMERIC(7,6);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS mempool_smart_sell_ratio   NUMERIC(7,6);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS mempool_whale_buy_count    INT;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS mempool_whale_sell_count   INT;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS mempool_tiny_swap_density  NUMERIC(5,4);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS mempool_flags              JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS mempool_updated_at         TIMESTAMPTZ;

-- Major interest flag and composite
ALTER TABLE launches ADD COLUMN IF NOT EXISTS major_interest_flag        BOOLEAN DEFAULT FALSE;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS major_interest_score       SMALLINT CHECK (major_interest_score BETWEEN 0 AND 100);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS major_interest_detail      JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS major_interest_updated_at  TIMESTAMPTZ;

-- Enhanced SM cohort fields (v2.1)
ALTER TABLE launches ADD COLUMN IF NOT EXISTS sm_net_position_30m        NUMERIC;  -- net token flow in 30m
ALTER TABLE launches ADD COLUMN IF NOT EXISTS sm_net_position_1h         NUMERIC;  -- net token flow in 1h
ALTER TABLE launches ADD COLUMN IF NOT EXISTS sm_diversity_clusters      INT;      -- distinct cluster IDs in SM cohort
ALTER TABLE launches ADD COLUMN IF NOT EXISTS sm_pending_conviction      NUMERIC(5,4);  -- from mempool

-- Enhanced whale fields (v2.1)
ALTER TABLE launches ADD COLUMN IF NOT EXISTS whale_net_flow_z           NUMERIC(7,4);  -- z-score
ALTER TABLE launches ADD COLUMN IF NOT EXISTS whale_pending_bias         NUMERIC(5,4);  -- pending buy-sell ratio

-- Scoring version bump
-- (scoring_version already exists as TEXT, just use 'v2.1')

-- ============================================================
-- LAUNCH OUTCOMES — major interest tracking for learning
-- ============================================================

ALTER TABLE launch_outcomes ADD COLUMN IF NOT EXISTS major_interest_flag_at_entry BOOLEAN;
ALTER TABLE launch_outcomes ADD COLUMN IF NOT EXISTS major_interest_score_at_entry SMALLINT;
ALTER TABLE launch_outcomes ADD COLUMN IF NOT EXISTS mempool_smart_buy_ratio_at_entry NUMERIC(7,6);
ALTER TABLE launch_outcomes ADD COLUMN IF NOT EXISTS sub_scores_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_launches_major_interest ON launches (major_interest_flag) WHERE major_interest_flag = TRUE;
CREATE INDEX IF NOT EXISTS idx_launches_mempool_updated ON launches (mempool_updated_at) WHERE mempool_updated_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_outcomes_major_interest ON launch_outcomes (major_interest_flag_at_entry) WHERE major_interest_flag_at_entry IS NOT NULL;
