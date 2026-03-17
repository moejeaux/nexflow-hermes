-- Migration 007: V2 Scoring Upgrade — sub-scores, CEX labels, social, graph risk, sell triggers
-- Adds columns for SmartMoneyAlignmentScore, WhaleBehaviorScore, GraphRiskScore,
-- RugRiskScore, LiquidityQualityScore, SocialQualityScore, DataConfidenceScore,
-- CEX wallet labels, sell/de-risk state, and missing-data tracking.

-- ============================================================
-- NEW ENUMS
-- ============================================================

CREATE TYPE position_action AS ENUM (
    'HOLD',              -- no action needed
    'SOFT_DERISK',       -- reduce position size
    'HARD_EXIT',         -- close position, disable buys
    'NO_ENTRY'           -- never opened — do not enter
);

-- ============================================================
-- LAUNCHES TABLE — new sub-score columns
-- ============================================================

-- Sub-scores (0-100, nullable until computed)
ALTER TABLE launches ADD COLUMN IF NOT EXISTS smart_money_alignment   SMALLINT CHECK (smart_money_alignment BETWEEN 0 AND 100);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS whale_behavior_score    SMALLINT CHECK (whale_behavior_score BETWEEN 0 AND 100);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS graph_risk_score        SMALLINT CHECK (graph_risk_score BETWEEN 0 AND 100);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS rug_risk_score          SMALLINT CHECK (rug_risk_score BETWEEN 0 AND 100);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS liquidity_quality_score SMALLINT CHECK (liquidity_quality_score BETWEEN 0 AND 100);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS social_quality_score    SMALLINT CHECK (social_quality_score BETWEEN 0 AND 100);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS data_confidence_score   SMALLINT CHECK (data_confidence_score BETWEEN 0 AND 100);

-- Sell/de-risk state
ALTER TABLE launches ADD COLUMN IF NOT EXISTS position_action       position_action DEFAULT 'NO_ENTRY';
ALTER TABLE launches ADD COLUMN IF NOT EXISTS derisk_triggers       JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS derisk_updated_at     TIMESTAMPTZ;

-- Data completeness flags (JSONB object with bool per group)
ALTER TABLE launches ADD COLUMN IF NOT EXISTS data_completeness     JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Liquidity detail fields
ALTER TABLE launches ADD COLUMN IF NOT EXISTS lp_usd               NUMERIC;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS lp_depth_2pct_usd    NUMERIC;     -- max trade at 2% slippage
ALTER TABLE launches ADD COLUMN IF NOT EXISTS rolling_volume_1h_usd NUMERIC;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS rolling_volume_4h_usd NUMERIC;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS effective_spread_bp   NUMERIC;     -- basis points

-- Smart money detail fields
ALTER TABLE launches ADD COLUMN IF NOT EXISTS founding_cohort_size      INT;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS smart_money_count         INT;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS smart_money_share         NUMERIC(5,4);  -- 0.0000 to 1.0000
ALTER TABLE launches ADD COLUMN IF NOT EXISTS accumulation_ratio_30m    NUMERIC(5,4);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS sm_cohort_exit_pct        NUMERIC(5,4);  -- fraction sold >50%
ALTER TABLE launches ADD COLUMN IF NOT EXISTS median_sm_hold_minutes    INT;

-- Whale behavior detail fields
ALTER TABLE launches ADD COLUMN IF NOT EXISTS whale_net_flow_tokens     NUMERIC;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS whale_accumulation_trend  NUMERIC;       -- slope
ALTER TABLE launches ADD COLUMN IF NOT EXISTS whale_buys_on_dips_ratio  NUMERIC(5,4);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS whale_sells_in_rips_ratio NUMERIC(5,4);

-- Graph risk detail fields
ALTER TABLE launches ADD COLUMN IF NOT EXISTS degree_centralization     NUMERIC(5,4);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS loop_fraction             NUMERIC(5,4);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS lp_owner_concentration    NUMERIC(5,4);
ALTER TABLE launches ADD COLUMN IF NOT EXISTS lp_change_rate            NUMERIC;

-- Social detail fields
ALTER TABLE launches ADD COLUMN IF NOT EXISTS social_mentions_total         INT;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS social_mentions_trusted       INT;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS social_sentiment_score        NUMERIC(5,2);  -- -1.00 to 1.00
ALTER TABLE launches ADD COLUMN IF NOT EXISTS negative_reports_count        INT;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS creator_social_presence       TEXT;           -- none/neutral/positive/negative

-- Scoring version v2
ALTER TABLE launches ADD COLUMN IF NOT EXISTS scoring_version TEXT DEFAULT 'v1';

-- ============================================================
-- WALLETS TABLE — CEX funding labels
-- ============================================================

ALTER TABLE wallets ADD COLUMN IF NOT EXISTS is_cex_funded         BOOLEAN DEFAULT FALSE;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS cex_funding_share     NUMERIC(5,4);    -- 0.0000 to 1.0000
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS funding_cex_list      TEXT[];           -- array of CEX names
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS cex_funding_detail    JSONB NOT NULL DEFAULT '{}'::jsonb; -- per-exchange USD amounts

-- ============================================================
-- CEX HOT WALLETS REFERENCE TABLE
-- ============================================================

CREATE TABLE IF NOT EXISTS cex_hot_wallets (
    address       TEXT PRIMARY KEY,         -- lowercase 0x address
    exchange_name TEXT NOT NULL,            -- Binance, Coinbase, MEXC, OKX, Bybit, etc.
    label         TEXT,                     -- hot_wallet, deposit_relay, withdrawal, etc.
    chain         TEXT NOT NULL DEFAULT 'base',
    confidence    NUMERIC(3,2) DEFAULT 1.0,  -- how sure we are (1.0 = confirmed, 0.5 = suspected)
    source        TEXT,                     -- where the label came from (etherscan, arkham, manual)
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cex_wallets_exchange ON cex_hot_wallets (exchange_name);

-- Seed known Base CEX addresses (partial — expand over time)
INSERT INTO cex_hot_wallets (address, exchange_name, label, source) VALUES
    -- Coinbase
    ('0xcdac0d6c6c59727a65f871236188350531885c43', 'Coinbase', 'hot_wallet', 'etherscan_label'),
    ('0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43', 'Coinbase', 'hot_wallet_2', 'etherscan_label'),
    -- Binance (bridged to Base)
    ('0x28c6c06298d514db089934071355e5743bf21d60', 'Binance', 'hot_wallet_14', 'etherscan_label'),
    ('0x21a31ee1afc51d94c2efccaa2092ad1028285549', 'Binance', 'hot_wallet_15', 'etherscan_label'),
    -- OKX
    ('0x6cc5f688a315f3dc28a7781717a9a798a59fda7b', 'OKX', 'hot_wallet', 'etherscan_label'),
    -- Bybit
    ('0xf89d7b9c864f589bbf53a82105107622b35eaa40', 'Bybit', 'hot_wallet', 'etherscan_label'),
    -- MEXC
    ('0x75e89d5979e4f6fba9f97c104c2f0afb3f1dcb88', 'MEXC', 'hot_wallet', 'etherscan_label'),
    -- Kraken
    ('0x2910543af39aba0cd09dbb2d50200b3e800a63d2', 'Kraken', 'hot_wallet', 'etherscan_label'),
    -- Base Bridge (not a CEX but relevant funding source)
    ('0x3154cf16ccdb4c6d922629664174b904d80f2c35', 'Base_Bridge', 'bridge', 'protocol'),
    ('0x49048044d57e1c92a77f79988d21fa8faf74e97e', 'Base_Portal', 'portal', 'protocol')
ON CONFLICT (address) DO NOTHING;

-- ============================================================
-- SELL / DE-RISK TRIGGERS LOG
-- ============================================================

CREATE TABLE IF NOT EXISTS derisk_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    launch_id       UUID NOT NULL REFERENCES launches(launch_id) ON DELETE CASCADE,
    trigger_type    TEXT NOT NULL,       -- sm_cohort_exit, whale_flip, rug_spike, lp_drain, etc.
    severity        TEXT NOT NULL,       -- SOFT_DERISK, HARD_EXIT
    details         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_derisk_launch ON derisk_events (launch_id);
CREATE INDEX IF NOT EXISTS idx_derisk_created ON derisk_events (created_at DESC);

-- ============================================================
-- INDEXES FOR NEW COLUMNS
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_launches_rug_risk     ON launches (rug_risk_score) WHERE rug_risk_score IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_launches_liq_quality  ON launches (liquidity_quality_score) WHERE liquidity_quality_score IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_launches_data_conf    ON launches (data_confidence_score) WHERE data_confidence_score IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_launches_position     ON launches (position_action) WHERE position_action != 'NO_ENTRY';
CREATE INDEX IF NOT EXISTS idx_wallets_cex           ON wallets (is_cex_funded) WHERE is_cex_funded = TRUE;
