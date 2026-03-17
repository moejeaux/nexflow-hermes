-- NXFX01 Launch Intelligence Schema
-- Migration 001: Core tables for launch analysis pipeline
-- Target: Supabase (PostgreSQL 15+)

-- ============================================================
-- ENUMS
-- ============================================================

CREATE TYPE launch_type AS ENUM (
    'launchpad', 'fair_launch', 'presale', 'stealth', 'unknown'
);

CREATE TYPE action_mode AS ENUM ('FAST', 'WAIT', 'BLOCK');

CREATE TYPE launch_status AS ENUM (
    'pending_initial',    -- detected, awaiting contract + deployer scoring
    'initial_scored',     -- Stage 1 complete (contract, deployer, funding, initial mode)
    'behavior_scored',    -- Stage 2 complete (holder dist, LP, smart money, final mode)
    'outcome_scored'      -- outcome metrics recorded
);

CREATE TYPE wallet_tier AS ENUM (
    'TIER_1_WHALE',
    'TIER_2_SMART_MONEY',
    'TIER_3_RETAIL',
    'TIER_4_FLAGGED',
    'UNKNOWN'
);

CREATE TYPE cluster_tier AS ENUM (
    'TIER_1_WHALE_CLUSTER',
    'TIER_2_SMART_CLUSTER',
    'TIER_3_NEUTRAL',
    'TIER_4_FLAGGED',
    'UNKNOWN'
);

CREATE TYPE participant_role AS ENUM (
    'DEPLOYER', 'FUNDER', 'EARLY_BUYER', 'SELLER', 'LP_PROVIDER'
);

CREATE TYPE outcome_status AS ENUM (
    'ACTIVE', 'RUGGED', 'DEAD', 'GRADUATED'
);

CREATE TYPE suggestion_status AS ENUM (
    'PENDING', 'APPROVED', 'REJECTED'
);

CREATE TYPE market_regime AS ENUM ('HOT', 'NORMAL', 'COLD');

-- ============================================================
-- CORE TABLES
-- ============================================================

-- ----- launches -----
CREATE TABLE launches (
    launch_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_address   TEXT NOT NULL,
    pair_address    TEXT,
    deployer_address TEXT,
    chain           TEXT NOT NULL DEFAULT 'base',
    timestamp       TIMESTAMPTZ NOT NULL,

    -- classification
    launch_type            launch_type DEFAULT 'unknown',
    launch_type_confidence SMALLINT CHECK (launch_type_confidence BETWEEN 0 AND 100),

    -- pipeline status
    status          launch_status NOT NULL DEFAULT 'pending_initial',
    policy_version  TEXT,

    -- latency tracking timestamps
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    initial_scored_at   TIMESTAMPTZ,
    behavior_scored_at  TIMESTAMPTZ,
    first_surfaced_at   TIMESTAMPTZ,

    -- Stage 1 scores (0-100, nullable until computed)
    contract_safety       SMALLINT CHECK (contract_safety BETWEEN 0 AND 100),
    deployer_reputation   SMALLINT CHECK (deployer_reputation BETWEEN 0 AND 100),
    funding_risk          SMALLINT CHECK (funding_risk BETWEEN 0 AND 100),

    -- Stage 2 scores
    holder_distribution        SMALLINT CHECK (holder_distribution BETWEEN 0 AND 100),
    liquidity_stability        SMALLINT CHECK (liquidity_stability BETWEEN 0 AND 100),
    smart_money_participation  SMALLINT CHECK (smart_money_participation BETWEEN 0 AND 100),
    whale_participation        SMALLINT CHECK (whale_participation BETWEEN 0 AND 100),

    -- overall scores & modes
    overall_safety_initial SMALLINT CHECK (overall_safety_initial BETWEEN 0 AND 100),
    overall_safety_final   SMALLINT CHECK (overall_safety_final BETWEEN 0 AND 100),
    action_initial         action_mode,
    action_final           action_mode,

    -- wallet summary
    top_holders_share NUMERIC(5, 2),          -- percentage, e.g. 45.30
    tier1_whales      INT DEFAULT 0,
    tier2_smart_money INT DEFAULT 0,
    tier3_retail      INT DEFAULT 0,
    tier4_flagged     INT DEFAULT 0,

    -- deployer fingerprinting
    bytecode_hash                TEXT,         -- constructor-stripped, normalized
    deployer_launch_velocity_24h INT DEFAULT 0,

    -- behavior versioning
    behavior_version TEXT,

    -- structured notes & raw data
    notes           JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_signals     JSONB NOT NULL DEFAULT '{}'::jsonb,
    notable_participants JSONB,               -- pre-computed TIER_1/2/alpha early buyers

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for common query patterns
CREATE INDEX idx_launches_chain_action_initial ON launches (chain, action_initial);
CREATE INDEX idx_launches_chain_action_final   ON launches (chain, action_final);
CREATE INDEX idx_launches_safety_initial       ON launches (overall_safety_initial DESC NULLS LAST);
CREATE INDEX idx_launches_safety_final         ON launches (overall_safety_final DESC NULLS LAST);
CREATE INDEX idx_launches_token_address        ON launches (token_address);
CREATE INDEX idx_launches_deployer_address     ON launches (deployer_address);
CREATE INDEX idx_launches_created_at           ON launches (created_at DESC);
CREATE INDEX idx_launches_status               ON launches (status);
CREATE INDEX idx_launches_bytecode_hash        ON launches (bytecode_hash) WHERE bytecode_hash IS NOT NULL;
CREATE INDEX idx_launches_detected_at          ON launches (detected_at DESC);

-- ----- wallets -----
CREATE TABLE wallets (
    wallet                  TEXT PRIMARY KEY,   -- lowercase 0x address
    wallet_tier             wallet_tier DEFAULT 'UNKNOWN',
    wallet_value_score      SMALLINT CHECK (wallet_value_score BETWEEN 0 AND 100) DEFAULT 0,
    wallet_performance_score SMALLINT CHECK (wallet_performance_score BETWEEN 0 AND 100) DEFAULT 0,
    cluster_id              TEXT,
    cluster_tier            cluster_tier,
    alpha_cohort_flag       BOOLEAN DEFAULT FALSE,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_wallets_tier           ON wallets (wallet_tier);
CREATE INDEX idx_wallets_cluster_id     ON wallets (cluster_id) WHERE cluster_id IS NOT NULL;
CREATE INDEX idx_wallets_alpha          ON wallets (alpha_cohort_flag) WHERE alpha_cohort_flag = TRUE;
CREATE INDEX idx_wallets_performance    ON wallets (wallet_performance_score DESC);

-- ----- clusters -----
CREATE TABLE clusters (
    cluster_id   TEXT PRIMARY KEY,
    cluster_tier cluster_tier DEFAULT 'UNKNOWN',
    member_count INT DEFAULT 0,
    description  TEXT,
    stats        JSONB NOT NULL DEFAULT '{}'::jsonb,   -- launch_count, avg_outcome, win_rate
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----- launch_outcomes -----
CREATE TABLE launch_outcomes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    launch_id       UUID NOT NULL REFERENCES launches(launch_id) ON DELETE CASCADE,

    -- raw price data
    price_at_launch NUMERIC,
    price_at_1h     NUMERIC,
    price_at_24h    NUMERIC,
    price_at_7d     NUMERIC,
    peak_price      NUMERIC,
    peak_mcap_usd   NUMERIC,

    -- derived metrics
    pnl_1h          NUMERIC,
    pnl_24h         NUMERIC,
    pnl_7d          NUMERIC,
    max_drawdown    NUMERIC,

    -- rug detection
    rugged          BOOLEAN,
    rug_timestamp   TIMESTAMPTZ,

    final_status    outcome_status DEFAULT 'ACTIVE',
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_outcomes_launch_id  ON launch_outcomes (launch_id);
CREATE INDEX idx_outcomes_recorded   ON launch_outcomes (recorded_at DESC);
CREATE INDEX idx_outcomes_status     ON launch_outcomes (final_status);

-- ----- launch_participants -----
CREATE TABLE launch_participants (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    launch_id              UUID NOT NULL REFERENCES launches(launch_id) ON DELETE CASCADE,
    wallet                 TEXT NOT NULL,
    role                   participant_role NOT NULL,
    first_action_block     BIGINT,
    first_action_timestamp TIMESTAMPTZ,
    amount_usd             NUMERIC,
    UNIQUE (launch_id, wallet, role)
);

CREATE INDEX idx_participants_launch  ON launch_participants (launch_id);
CREATE INDEX idx_participants_wallet  ON launch_participants (wallet);

-- ----- policy_suggestions -----
CREATE TABLE policy_suggestions (
    suggestion_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    suggested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    status          suggestion_status DEFAULT 'PENDING',
    patch           JSONB NOT NULL,
    rationale       TEXT NOT NULL,
    evidence_snapshot JSONB,    -- summary stats NXFX01 used for this suggestion
    reviewed_at     TIMESTAMPTZ,
    reviewer_notes  TEXT
);

CREATE INDEX idx_policy_status ON policy_suggestions (status);

-- ----- scan_state -----
CREATE TABLE scan_state (
    scanner_name      TEXT PRIMARY KEY,
    last_scanned_block BIGINT NOT NULL DEFAULT 0,
    last_run_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed initial scanner cursors
INSERT INTO scan_state (scanner_name, last_scanned_block) VALUES
    ('launch_scanner', 0),
    ('behavior_updater', 0);

-- ----- bad_templates (deployer fingerprinting) -----
CREATE TABLE bad_templates (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bytecode_hash  TEXT NOT NULL UNIQUE,
    label          TEXT,        -- human-readable: "honeypot_v3", "stealth_rug_template"
    severity       TEXT DEFAULT 'HIGH',
    first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes          TEXT
);

CREATE INDEX idx_bad_templates_hash ON bad_templates (bytecode_hash);

-- ----- nxfx01_config (ops table for runtime tuning) -----
CREATE TABLE nxfx01_config (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed default config
INSERT INTO nxfx01_config (key, value) VALUES
    ('mode', '"shadow"'),                                          -- shadow | live
    ('shadow_mode', 'true'),                                       -- legacy compat
    ('scan_interval_seconds', '180'),                               -- 3 min default
    ('scan_batch_size', '500'),                                     -- blocks per run
    ('max_launches_per_run', '100'),
    ('fast_threshold', '70'),
    ('block_threshold', '30'),
    ('velocity_block_threshold', '3'),                              -- auto-BLOCK at 3+ deploys/24h
    ('velocity_warn_threshold', '2'),
    ('behavior_window_minutes', '60'),
    ('base_market_regime', '"NORMAL"'),                             -- HOT | NORMAL | COLD
    ('regime_fast_threshold_adjustment', '{"HOT": 0, "NORMAL": 0, "COLD": 10}'),
    ('policy_version', '"v1.0"');

-- ----- base_market_regime_log (hourly snapshots) -----
CREATE TABLE base_market_regime_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    regime          market_regime NOT NULL,
    top10_volume_24h NUMERIC,
    top10_volume_prev_24h NUMERIC,
    volume_change_pct NUMERIC,
    details         JSONB
);

CREATE INDEX idx_regime_log_recorded ON base_market_regime_log (recorded_at DESC);

-- ============================================================
-- UPDATED_AT TRIGGERS
-- ============================================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_launches_updated_at
    BEFORE UPDATE ON launches FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_wallets_updated_at
    BEFORE UPDATE ON wallets FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_clusters_updated_at
    BEFORE UPDATE ON clusters FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_config_updated_at
    BEFORE UPDATE ON nxfx01_config FOR EACH ROW EXECUTE FUNCTION update_updated_at();
