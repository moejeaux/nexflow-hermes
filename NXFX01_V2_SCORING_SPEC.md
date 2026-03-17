# NXFX01 v2 Scoring Engine — Structured Specification

> **Version**: 2.0  
> **Status**: Implementation complete — shadow mode calibration  
> **Policy file**: `nxfx01-api/config/scoring_policy.yaml` (v2.0)  
> **Migration**: `migrations/007_v2_scoring_upgrade.sql`

---

## 1. Overview

v2 replaces the coarse Stage 2 safety score with **7 purpose-built sub-scores**, strict **FAST hard gating**, a **sell/de-risk trigger framework**, **CEX wallet labeling**, **social signal integration**, and a **missing-data = uncertainty** policy. Every change is wired through `scoring_policy.yaml` so NXFX01's self-learning loop can tune it under human approval.

### Architecture

```
 T+0s ─── Stage 1 (initial_scorer) ──────────────────────────────────────
  │        contract_safety + deployer_reputation + funding_risk
  │        → overall_safety_initial, action_initial
  │
 T+1–30m ── Stage 2 (final_scorer v2) ───────────────────────────────────
  │        ┌─ SmartMoneyAlignmentScore ──┐
  │        ├─ WhaleBehaviorScore ────────┤
  │        ├─ GraphRiskScore ────────────┤
  │        ├─ RugRiskScore ──────────────┼── weighted → overall_safety_final
  │        ├─ LiquidityQualityScore ─────┤     × DataConfidence factor
  │        ├─ SocialQualityScore ────────┤     → action_final
  │        ├─ DataConfidenceScore ───────┘     → position_action
  │        └─ DeRiskEngine
  │
 Continuous ── DeRisk evaluation loop (each behavior_updater tick) ──────
```

---

## 2. Feature Groups

### 2.1 Smart Money Intelligence

**Purpose**: Distinguish launches where genuinely profitable wallets participate from those with only retail/bots.

**Data sources**: `wallets.wallet_tier`, `wallets.alpha_cohort_flag`, `wallet_interactions` (inferred from token transfers in founding cohort window).

**Sub-score**: `SmartMoneyAlignmentScore` (0–100)

| Component | Weight | Description |
|---|---|---|
| `smart_money_share` | 0.30 | % of founding cohort supply held by TIER_2_SMART_MONEY or alpha wallets |
| `accumulation_ratio_30m` | 0.20 | net buys / total buys in first 30min by SM wallets |
| `sm_diversity` | 0.20 | distinct clusters represented (prevents 1-entity gaming) |
| `sm_hold_duration` | 0.15 | median hold time of SM wallets (longer = more conviction) |
| `sm_cohort_exit_pct` | 0.15 | % of founding SM cohort that already sold (inverted — lower = better) |

**Key rule**: If fewer than `min_smart_money_for_signal` (default: 2) TIER_2/alpha wallets in cohort, score is capped at 40.

**Module**: `nxfx01-api/src/scoring/smart_money_scorer.py`

---

### 2.2 Whale Behavior Analysis

**Purpose**: Track whether whale wallets are accumulating, distributing, or manipulating.

**Sub-score**: `WhaleBehaviorScore` (0–100)

| Component | Weight | Description |
|---|---|---|
| Net flow | 0.35 | Positive = accumulating (good), negative = distributing (bad) |
| Accumulation trend | 0.25 | Rate of change in whale holdings over 5-min windows |
| Buys on dips | 0.25 | Fraction of whale buys that occur during price dips (conviction signal) |
| Sells in rips | 0.15 | Fraction of whale sells during price spikes (inverted — lower = better) |

**Data source**: TIER_1_WHALE wallets × token transfer + price snapshot data.

**Module**: `nxfx01-api/src/scoring/whale_behavior_scorer.py`

---

### 2.3 Graph Risk (Transfer Graph Analysis)

**Purpose**: Detect wash trading, sybil networks, and LP manipulation through transfer graph structure.

**Sub-score**: `GraphRiskScore` (0–100, **higher = riskier**)

| Component | Weight | Description |
|---|---|---|
| Degree centralization | 0.35 | Freeman-style centralization of transfer graph; high = few wallets dominate flow |
| Loop fraction | 0.35 | Fraction of volume in A→B→A loops (wash trading indicator) |
| LP owner concentration | 0.30 | Share of LP owned by deployer or top-3 insiders |

**Hard block**: If **all three** exceed their individual thresholds simultaneously (`degree_centralization > 0.70`, `loop_fraction > 0.40`, `lp_owner_concentration > 0.60`), a `graph_hard_block` flag is raised → action = BLOCK.

**Module**: `nxfx01-api/src/scoring/graph_risk_scorer.py`

---

### 2.4 Rug Risk (Composite Heuristics)

**Purpose**: Single composite risk signal combining code, graph, behavioral, and funding patterns.

**Sub-score**: `RugRiskScore` (0–100, **higher = more dangerous**)

| Component | Weight | Description |
|---|---|---|
| Code risk | 0.35 | Derived from contract_safety (inverted) + extra penalties for mint/blacklist/selfdestruct |
| Graph risk | 0.25 | Raw GraphRiskScore passthrough |
| Behavioral risk | 0.25 | Tax hikes (>5% increase), deployer dumps (>20% sold), volume collapse (>80% drop) |
| Funding risk | 0.15 | Derived from funding_risk score (inverted) |

**Hard block patterns**: Any of these → rug_risk = 100 automatically:
- `confirmed_honeypot` + `owner_can_change_balance`
- `is_proxy` + `owner_can_change_balance`
- `hidden_owner` + `can_take_back_ownership`
- `deployer_lp_drain_gt_50pct`
- `stealth_tax_increase`

**Extra penalties**: +10 for `selfdestruct`, +8 for `is_mintable`, +5 for `is_blacklisted`.

**Module**: `nxfx01-api/src/scoring/rug_risk_scorer.py`

---

### 2.5 CEX Wallet Labeling

**Purpose**: Identify wallets funded from centralized exchanges (Coinbase, Binance, etc.) via funding trace analysis.

**Logic**:
1. Load known CEX hot wallet addresses from `cex_hot_wallets` table (seeded with 6 major exchanges).
2. For each target wallet, trace inbound ETH transfers up to 2 hops.
3. Hop-2 attribution is discounted at 50%.
4. If CEX share ≥ 30% → `is_cex_funded = true`.
5. Results persisted on `wallets` table: `is_cex_funded`, `cex_funding_share`, `funding_cex_list`, `cex_funding_detail`.

**Use in scoring**: CEX-funded deployers get their `funding_risk` reduced (less suspicious than mixer-funded). Used as signal in smart money identification (CEX-backed SM wallets are more trustworthy).

**Worker**: `nxfx01-api/src/workers/cex_labeler.py` — processes deployers from recent launches (last 7 days).

---

### 2.6 Social / X Scan

**Purpose**: Incorporate external social signals — trusted mentions, sentiment, negative reports, creator presence.

**Sub-score**: `SocialQualityScore` (0–100)

| Component | Weight | Description |
|---|---|---|
| Trusted mentions | 0.35 | Count of mentions from accounts on verified/trusted list |
| Sentiment | 0.25 | Aggregate sentiment from social mentions (0–1 scale, normalized to 0–100) |
| Negative reports | 0.25 | Inverted: credible scam/rug reports (0 = safe, many = dangerous) |
| Creator presence | 0.15 | Whether token creator has verified social accounts (binary → 0 or 100) |

**Shill bot penalty**: If `shill_bot_detected` flag is true, score is halved.

**Rug risk bump**: If `negative_reports ≥ 2` from credible sources → adds 10–20 points to the token's rug_risk score.

**Module**: `nxfx01-api/src/scoring/social_quality_scorer.py`

---

### 2.7 Liquidity Quality & Hard Gating

**Purpose**: Ensure FAST-mode trades only happen in pools with sufficient depth and volume.

**Sub-score**: `LiquidityQualityScore` (0–100)

Tier-based scoring on three dimensions:

| Metric | Excellent | Good | Adequate | Minimum | <Minimum |
|---|---|---|---|---|---|
| LP (USD) | $50K+ → 100 | $20K → 80 | $10K → 60 | $5K → 40 | <$5K → 0 |
| Volume 1h (USD) | $10K+ → 100 | $5K → 80 | $2K → 60 | $1K → 40 | <$1K → 0 |
| Spread (bp) | <50bp → 100 | 100bp → 80 | 200bp → 60 | 500bp → 40 | >500bp → 0 |

**FAST hard gates** (failure of ANY gate blocks FAST, caps liquidity score at 25):

| Gate | Threshold |
|---|---|
| `min_lp_usd` | $5,000 |
| `min_volume_1h_usd` | $1,000 |
| `max_effective_spread_bp` | 500 bp |

**Module**: `nxfx01-api/src/scoring/liquidity_quality_scorer.py`

---

### 2.8 Data Confidence (Missing-Data Policy)

**Core principle**: **Missing data = uncertainty, never neutral.** You don't get a free pass for data we couldn't retrieve.

**Sub-score**: `DataConfidenceScore` (0–100)

8 feature groups tracked:

| Group | Weight | Critical? | What's needed |
|---|---|---|---|
| Contract | 0.20 | Yes | GoPlus results or bytecode analysis |
| Deployer | 0.15 | Yes | At least 1 resolved deployment history |
| Funding | 0.10 | No | Funding source trace |
| Smart money | 0.15 | No | Founding cohort with wallet tiers |
| Whale | 0.10 | No | TIER_1_WHALE presence identified |
| Graph | 0.10 | No | Transfer graph data (≥10 transfers) |
| Liquidity | 0.12 | Yes | LP, volume, spread available |
| Social | 0.08 | No | Social scan results available |

**Penalties**:
- **Critical group missing** → score immediately capped at 40, FAST blocked
- **Non-critical missing** → 8% penalty per missing group

**Application to final score**: `effective_score = raw_score × (0.5 + DataConfidence / 200)`
- DataConfidence = 0 → score scaled to 50%
- DataConfidence = 100 → score at 100%

**Module**: `nxfx01-api/src/scoring/data_confidence_scorer.py`

---

## 3. Scoring Formula

### Stage 1 (Initial)

```
overall_safety_initial = Σ (initial_weights[dim] × score[dim]) × 100
```

Dimensions: `contract_safety (0.30)`, `deployer_reputation (0.25)`, `funding_risk (0.20)`.

Remaining 0.25 is fingerprint/velocity adjustments folded into deployer_reputation.

### Stage 2 (Final — v2)

```python
# 1. Compute raw weighted score
raw_score = Σ (final_weights[dim] × normalized_score[dim])

# For risk scores (graph_risk, rug_risk):
#   normalized = 100 - raw_risk
# For all others:
#   normalized = raw_score (already 0-100, higher = better)

# 2. Apply data confidence modulation
confidence_factor = 0.5 + (data_confidence_score / 200)
modulated_score = raw_score × confidence_factor

# 3. Apply critical red flag override
if any critical_red_flag present:
    modulated_score = min(modulated_score, 30)
    action = BLOCK

# 4. Threshold to action mode
if modulated_score >= fast_threshold (70):
    action_final = FAST  (pending hard gate checks)
elif modulated_score >= wait_threshold (40):
    action_final = WAIT
else:
    action_final = BLOCK
```

### Final Weights (sum = 1.0)

| Dimension | Weight | Direction |
|---|---|---|
| `contract_safety` | 0.12 | Higher = safer |
| `deployer_reputation` | 0.10 | Higher = safer |
| `funding_risk` | 0.08 | Higher = safer (inverted: low risk = high score) |
| `smart_money_alignment` | 0.15 | Higher = better |
| `whale_behavior` | 0.08 | Higher = better |
| `graph_risk` | 0.10 | **Stored as risk** → formula uses `100 - graph_risk` |
| `rug_risk` | 0.15 | **Stored as risk** → formula uses `100 - rug_risk` |
| `liquidity_quality` | 0.12 | Higher = better |
| `social_quality` | 0.05 | Higher = better |
| `holder_distribution` | 0.05 | Higher = better |

---

## 4. FAST Hard Gating

A launch must pass **ALL** hard gates to achieve FAST. Failing any gate downgrades to WAIT (or BLOCK).

| Gate | Rule | On Fail |
|---|---|---|
| LP depth | `lp_usd ≥ 5000` | → WAIT |
| Volume | `rolling_volume_1h_usd ≥ 1000` | → WAIT |
| Spread | `effective_spread_bp ≤ 500` | → WAIT |
| Rug risk | `rug_risk_score ≤ 45` | → BLOCK |
| Data confidence | `data_confidence_score ≥ 60` | → WAIT |
| Graph risk | `graph_risk_score ≤ 60` | → WAIT |
| Critical data | All critical groups (contract, deployer, liquidity) present | → WAIT |
| Liquidity gates | `liquidity_quality.passes_hard_gates = true` | → WAIT |

These are enforced in `final_scorer.py` after sub-score computation.

---

## 5. Sell / De-Risk Trigger Framework

### Trigger Types

| Trigger | Severity | Condition |
|---|---|---|
| `sm_cohort_exit` | SOFT_DERISK | `sm_cohort_exit_pct > 40%` |
| `founding_cohort_exit` | HARD_EXIT | `sm_cohort_exit_pct > 70%` |
| `whale_distribution_flip` | SOFT_DERISK | `whale_net_flow < 0` AND `whale_accumulation_trend < -0.3` |
| `rug_risk_spike` | HARD_EXIT | `rug_risk_score > 70` |
| `lp_drain` | HARD_EXIT | `lp_change_rate < -0.3` (30%+ LP removal) |
| `volume_collapse` | SOFT_DERISK | `volume_1h_usd < 500` |
| `spread_explosion` | SOFT_DERISK | `effective_spread_bp > 800` |
| `graph_risk_spike` | SOFT_DERISK | `graph_risk_score > 70` |

### Escalation Logic

```
if count(HARD_EXIT triggers) ≥ 1 → position_action = HARD_EXIT
elif count(SOFT_DERISK triggers) ≥ 3 → position_action = HARD_EXIT
elif count(SOFT_DERISK triggers) ≥ 1 → position_action = SOFT_DERISK
else → position_action = HOLD
```

### Persistence

Each trigger fires → row in `derisk_events` table with:
- `trigger_type`, `severity`, `detail` (JSONB with snapshot values)
- `resolved` / `resolved_at` for future un-trigger tracking

The `launches.position_action` column is updated to the highest severity action.

**Module**: `nxfx01-api/src/scoring/derisk_engine.py`

---

## 6. Database Schema Changes

**Migration**: `migrations/007_v2_scoring_upgrade.sql`

### New enum
```sql
CREATE TYPE position_action AS ENUM ('HOLD','SOFT_DERISK','HARD_EXIT','NO_ENTRY');
```

### New columns on `launches`

| Column | Type | Purpose |
|---|---|---|
| `scoring_version` | TEXT | "v1" or "v2" |
| `smart_money_alignment` | SMALLINT | Sub-score 0–100 |
| `whale_behavior_score` | SMALLINT | Sub-score 0–100 |
| `graph_risk_score` | SMALLINT | Sub-score 0–100 (higher = riskier) |
| `rug_risk_score` | SMALLINT | Sub-score 0–100 (higher = riskier) |
| `liquidity_quality_score` | SMALLINT | Sub-score 0–100 |
| `social_quality_score` | SMALLINT | Sub-score 0–100 |
| `data_confidence_score` | SMALLINT | Sub-score 0–100 |
| `sm_detail` | JSONB | SmartMoneyDetail breakdown |
| `whale_detail` | JSONB | WhaleBehaviorDetail breakdown |
| `graph_detail` | JSONB | GraphRiskDetail breakdown |
| `social_detail` | JSONB | SocialDetail breakdown |
| `lp_usd` | NUMERIC(18,2) | Pool LP in USD |
| `rolling_volume_1h_usd` | NUMERIC(18,2) | 1-hour rolling volume |
| `effective_spread_bp` | NUMERIC(8,2) | Effective spread in basis points |
| `position_action` | position_action | Current de-risk state |
| `derisk_triggers` | JSONB | Active trigger snapshots |
| `data_completeness` | JSONB | Feature group presence flags |

### New columns on `wallets`

| Column | Type | Purpose |
|---|---|---|
| `is_cex_funded` | BOOLEAN | Whether wallet is CEX-funded |
| `cex_funding_share` | NUMERIC(5,4) | Fraction of funding from CEX |
| `funding_cex_list` | JSONB | List of CEX names |
| `cex_funding_detail` | JSONB | Per-hop trace detail |

### New tables

**`cex_hot_wallets`** — Reference table of known CEX addresses (seeded with Coinbase, Binance, OKX, Bybit, MEXC, Kraken).

**`derisk_events`** — Event log for fired de-risk triggers with resolution tracking.

---

## 7. API Changes

### Updated models

- `LaunchView` — added `sub_scores`, `smart_money_detail`, `whale_behavior_detail`, `graph_risk_detail`, `liquidity_detail`, `social_detail`, `position_action`, `derisk_triggers`, `data_completeness`, `scoring_version`
- `LaunchSummary` — added `position_action`, `rug_risk`, `data_confidence`, `scoring_version`
- `WalletView` — added `is_cex_funded`, `cex_funding_share`, `funding_cex_list`
- `LaunchOutcomeView` — added `sub_scores`, `position_action`, `scoring_version`

### New models

- `SubScores` — 7 sub-score fields
- `SmartMoneyDetail`, `WhaleBehaviorDetail`, `GraphRiskDetail`, `LiquidityDetail`, `SocialDetail` — breakdown fields
- `DataCompleteness` — 8 boolean group flags
- `DeRiskTrigger`, `DeRiskEventView` — trigger and event models
- `PositionAction` enum — HOLD / SOFT_DERISK / HARD_EXIT / NO_ENTRY

### New endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/launches/{id}/derisk-events` | De-risk trigger event history for a launch |

### Updated queries

- `_LAUNCH_COLUMNS` expanded with all v2 columns
- `_SUMMARY_COLUMNS` expanded with `position_action`, `rug_risk_score`, `data_confidence_score`, `scoring_version`
- Outcomes query includes v2 sub-scores
- Wallet query includes CEX fields

---

## 8. Module Map

All new modules in `nxfx01-api/src/scoring/`:

| Module | Exports | Score Range | Direction |
|---|---|---|---|
| `smart_money_scorer.py` | `SmartMoneyScorer.score()` | 0–100 | Higher = better |
| `whale_behavior_scorer.py` | `WhaleBehaviorScorer.score()` | 0–100 | Higher = better |
| `graph_risk_scorer.py` | `GraphRiskScorer.score()` | 0–100 | Higher = riskier |
| `rug_risk_scorer.py` | `RugRiskScorer.score()` | 0–100 | Higher = riskier |
| `liquidity_quality_scorer.py` | `LiquidityQualityScorer.score()` | 0–100 | Higher = better |
| `social_quality_scorer.py` | `SocialQualityScorer.score()` | 0–100 | Higher = better |
| `data_confidence_scorer.py` | `DataConfidenceScorer.score()`, `apply_confidence_to_score()` | 0–100 | Higher = more confident |
| `derisk_engine.py` | `evaluate_triggers()`, `determine_position_action()`, `persist_triggers()` | — | — |

Workers:

| Module | Purpose | Schedule |
|---|---|---|
| `workers/cex_labeler.py` | Label CEX-funded wallets | After deployer_profiler, 7-day lookback |

---

## 9. Design Rationale

### Why sub-scores instead of a single formula adjustment?

**Transparency**: Each dimension is independently auditable. When a launch is scored 62, you can see it's because rug_risk=45 and data_confidence=55, not just "the formula said so."

**Tuning**: The learning loop can adjust individual sub-score weights without re-deriving the entire model. A policy patch like `{"final_weights.smart_money_alignment": 0.18}` is surgical.

**Hard gating**: Some signals are pass/fail regardless of total score. Liquidity depth below $5K should never get FAST, even if SM alignment is 95. Sub-scores enable this cleanly.

### Why missing data ≠ neutral?

In previous versions, a token with no graph data would get graph_risk=0 (neutral). This is wrong — we don't know the graph risk, so it should contribute uncertainty. DataConfidenceScore penalizes unknowns proportionally. A launch with 3/8 feature groups missing gets its final score scaled down by ~25%, which naturally pushes it toward WAIT instead of FAST.

### Why store risk scores as "higher = riskier" in DB?

Human readability. When you see `rug_risk_score = 78`, you immediately know it's dangerous. The formula inverts these (`100 - 78 = 22`) for the weighted sum where "higher = safer" — but that's internal math, not display logic.

### Why hard gates exist separately from the formula?

The weighted formula is continuous — you could theoretically brute-force a high score with incredible social and SM metrics while LP is dangerously thin. Hard gates are circuit breakers: no matter how good everything else looks, if LP < $5K or rug_risk > 45, you can't FAST. This is a defense-in-depth pattern.

### Why 3 SOFT → HARD escalation?

A single soft trigger (e.g., volume_collapse) could be noise or a normal lull. Two might be coincidence. Three concurrent soft triggers indicates a systemic problem — the launch is deteriorating across multiple dimensions simultaneously, warranting a full exit recommendation.

---

## 10. Calibration & Shadow Mode

All v2 scoring runs in **shadow mode** during initial deployment (configurable via `scoring_policy.yaml: shadow.enabled`). Shadow mode means:
- All scores are computed and persisted normally
- All alerts fire with `shadow = true`
- No real trade recommendations are surfaced to the agent for action
- After calibration period (default: 14 days), human review enables live mode

The outcome_tracker continues to record PnL/rug outcomes against v2 scores, building the dataset for NXFX01's first policy tuning cycle.

---

## 11. Files Changed / Created

### New files
- `migrations/007_v2_scoring_upgrade.sql`
- `nxfx01-api/src/scoring/smart_money_scorer.py`
- `nxfx01-api/src/scoring/whale_behavior_scorer.py`
- `nxfx01-api/src/scoring/graph_risk_scorer.py`
- `nxfx01-api/src/scoring/rug_risk_scorer.py`
- `nxfx01-api/src/scoring/liquidity_quality_scorer.py`
- `nxfx01-api/src/scoring/social_quality_scorer.py`
- `nxfx01-api/src/scoring/data_confidence_scorer.py`
- `nxfx01-api/src/scoring/derisk_engine.py`
- `nxfx01-api/src/workers/cex_labeler.py`
- `NXFX01_V2_SCORING_SPEC.md` (this document)

### Modified files
- `nxfx01-api/config/scoring_policy.yaml` — v1.1 → v2.0
- `nxfx01-api/src/scoring/final_scorer.py` — rewritten for v2 sub-scores
- `nxfx01-api/src/scoring/__init__.py` — exports all new modules
- `nxfx01-api/src/api/models.py` — v2 Pydantic models
- `nxfx01-api/src/api/main.py` — v2 routes, queries, and de-risk endpoint
