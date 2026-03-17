"""Pydantic models for NXFX01 API — mirrors agents.md Launch View + Wallet View schemas.

v2.1: Adds mempool features layer, major interest detection, enhanced SM/whale
cohort fields, and updated data completeness tracking.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ---- Enums ----

class LaunchType(str, Enum):
    launchpad = "launchpad"
    fair_launch = "fair_launch"
    presale = "presale"
    stealth = "stealth"
    unknown = "unknown"


class ActionMode(str, Enum):
    FAST = "FAST"
    WAIT = "WAIT"
    BLOCK = "BLOCK"


class PositionAction(str, Enum):
    HOLD = "HOLD"
    SOFT_DERISK = "SOFT_DERISK"
    HARD_EXIT = "HARD_EXIT"
    NO_ENTRY = "NO_ENTRY"


class LaunchStatus(str, Enum):
    pending_initial = "pending_initial"
    initial_scored = "initial_scored"
    behavior_scored = "behavior_scored"
    outcome_scored = "outcome_scored"


class WalletTier(str, Enum):
    TIER_1_WHALE = "TIER_1_WHALE"
    TIER_2_SMART_MONEY = "TIER_2_SMART_MONEY"
    TIER_3_RETAIL = "TIER_3_RETAIL"
    TIER_4_FLAGGED = "TIER_4_FLAGGED"
    UNKNOWN = "UNKNOWN"


class ClusterTier(str, Enum):
    TIER_1_WHALE_CLUSTER = "TIER_1_WHALE_CLUSTER"
    TIER_2_SMART_CLUSTER = "TIER_2_SMART_CLUSTER"
    TIER_3_NEUTRAL = "TIER_3_NEUTRAL"
    TIER_4_FLAGGED = "TIER_4_FLAGGED"
    UNKNOWN = "UNKNOWN"


class OutcomeStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RUGGED = "RUGGED"
    DEAD = "DEAD"
    GRADUATED = "GRADUATED"


class MarketRegime(str, Enum):
    HOT = "HOT"
    NORMAL = "NORMAL"
    COLD = "COLD"


class LaunchpadTrustLevel(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# ---- Nested models ----

class LaunchScores(BaseModel):
    contract_safety: int | None = None
    deployer_reputation: int | None = None
    funding_risk: int | None = None
    holder_distribution: int | None = None
    liquidity_stability: int | None = None
    smart_money_participation: int | None = None
    whale_participation: int | None = None
    overall_safety_initial: int | None = None
    overall_safety_final: int | None = None


class SubScores(BaseModel):
    """v2 sub-scores — computed during Stage 2 final scoring."""
    smart_money_alignment: int | None = None
    whale_behavior: int | None = None
    graph_risk: int | None = None         # 0-100, higher = riskier
    rug_risk: int | None = None           # 0-100, higher = riskier
    liquidity_quality: int | None = None
    social_quality: int | None = None
    data_confidence: int | None = None


class SmartMoneyDetail(BaseModel):
    """Detail fields for SmartMoneyAlignmentScore."""
    founding_cohort_size: int | None = None
    smart_money_count: int | None = None
    smart_money_share: float | None = None
    accumulation_ratio_30m: float | None = None
    sm_cohort_exit_pct: float | None = None
    median_sm_hold_minutes: int | None = None


class WhaleBehaviorDetail(BaseModel):
    """Detail fields for WhaleBehaviorScore."""
    whale_net_flow_tokens: float | None = None
    whale_accumulation_trend: float | None = None
    whale_buys_on_dips_ratio: float | None = None
    whale_sells_in_rips_ratio: float | None = None


class GraphRiskDetail(BaseModel):
    """Detail fields for GraphRiskScore."""
    degree_centralization: float | None = None
    loop_fraction: float | None = None
    lp_owner_concentration: float | None = None
    lp_change_rate: float | None = None


class LiquidityDetail(BaseModel):
    """Detail fields for LiquidityQualityScore."""
    lp_usd: float | None = None
    lp_depth_2pct_usd: float | None = None
    rolling_volume_1h_usd: float | None = None
    rolling_volume_4h_usd: float | None = None
    effective_spread_bp: float | None = None
    passes_hard_gates: bool | None = None


class SocialDetail(BaseModel):
    """Detail fields for SocialQualityScore."""
    social_mentions_total: int | None = None
    social_mentions_trusted: int | None = None
    social_sentiment_score: float | None = None
    negative_reports_count: int | None = None
    creator_social_presence: str | None = None


class DeRiskTrigger(BaseModel):
    """A single de-risk trigger event."""
    trigger_type: str
    severity: str  # SOFT_DERISK or HARD_EXIT
    details: dict[str, Any] = Field(default_factory=dict)


class LaunchModes(BaseModel):
    action_initial: ActionMode | None = None
    action_final: ActionMode | None = None


class WalletTierCounts(BaseModel):
    tier1_whales: int = 0
    tier2_smart_money: int = 0
    tier3_retail: int = 0
    tier4_flagged: int = 0


class WalletSummary(BaseModel):
    top_holders_share: float | None = None
    tiers: WalletTierCounts = Field(default_factory=WalletTierCounts)


class LaunchNotes(BaseModel):
    contract_red_flags: list[str] = Field(default_factory=list)
    deployer_history_summary: str = ""
    deployer_red_flags: list[str] = Field(default_factory=list)
    funding_sources_summary: str = ""
    funding_red_flags: list[str] = Field(default_factory=list)
    holder_distribution_red_flags: list[str] = Field(default_factory=list)
    liquidity_red_flags: list[str] = Field(default_factory=list)
    safety_explanation_initial: str = ""
    safety_explanation_final: str | None = None
    fast_gate_failures: list[str] = Field(default_factory=list)


class NotableParticipant(BaseModel):
    address: str
    wallet_tier: WalletTier
    cluster_id: str | None = None
    cluster_tier: ClusterTier | None = None
    alpha_cohort_flag: bool = False
    win_rate: float | None = None


class DataCompleteness(BaseModel):
    """Tracks which feature groups have data available."""
    contract: bool = False
    deployer: bool = False
    funding: bool = False
    smart_money: bool = False
    whale: bool = False
    graph: bool = False
    liquidity: bool = False
    social: bool = False
    mempool: bool = False  # v2.1


class MempoolSnapshot(BaseModel):
    """v2.1: Latest mempool features snapshot for a token."""
    sm_pending_buy_usd: float = 0
    sm_pending_sell_usd: float = 0
    whale_pending_buy_usd: float = 0
    whale_pending_sell_usd: float = 0
    tiny_swap_density: float = 0
    anomaly_density: float = 0
    fee_urgency_score: float = 0
    derived_sm_conviction: float = 0
    derived_whale_bias: float = 0
    snapshot_ts: datetime | None = None


class MajorInterestDetail(BaseModel):
    """v2.1: Major interest composite evaluation result."""
    major_interest_flag: bool = False
    major_interest_score: int = 0
    detail: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)


# ---- Top-level views ----

class LaunchView(BaseModel):
    """Full launch view as consumed by NXFX01 / Hermes. v2.1."""
    launch_id: UUID
    token_address: str
    pair_address: str | None = None
    deployer_address: str | None = None
    chain: str = "base"
    dex_source: str = "unknown"
    timestamp: datetime

    launch_type: LaunchType = LaunchType.unknown
    launch_type_confidence: int = 0
    launchpad_trust_level: LaunchpadTrustLevel = LaunchpadTrustLevel.NONE

    status: LaunchStatus = LaunchStatus.pending_initial
    policy_version: str | None = None
    scoring_version: str = "v1"

    # Legacy scores
    scores: LaunchScores = Field(default_factory=LaunchScores)
    # v2 sub-scores
    sub_scores: SubScores = Field(default_factory=SubScores)
    # v2 detail breakdowns
    smart_money_detail: SmartMoneyDetail = Field(default_factory=SmartMoneyDetail)
    whale_behavior_detail: WhaleBehaviorDetail = Field(default_factory=WhaleBehaviorDetail)
    graph_risk_detail: GraphRiskDetail = Field(default_factory=GraphRiskDetail)
    liquidity_detail: LiquidityDetail = Field(default_factory=LiquidityDetail)
    social_detail: SocialDetail = Field(default_factory=SocialDetail)

    modes: LaunchModes = Field(default_factory=LaunchModes)
    wallet_summary: WalletSummary = Field(default_factory=WalletSummary)
    notes: LaunchNotes = Field(default_factory=LaunchNotes)
    notable_participants: list[NotableParticipant] = Field(default_factory=list)

    # Sell / de-risk state
    position_action: PositionAction = PositionAction.NO_ENTRY
    derisk_triggers: list[DeRiskTrigger] = Field(default_factory=list)

    # v2.1: Mempool features & major interest
    mempool_snapshot: MempoolSnapshot | None = None
    major_interest: MajorInterestDetail = Field(default_factory=MajorInterestDetail)

    # Data completeness
    data_completeness: DataCompleteness = Field(default_factory=DataCompleteness)

    # fingerprinting
    bytecode_hash: str | None = None
    deployer_launch_velocity_24h: int = 0

    # shadow mode flag
    shadow: bool = False

    # latency timestamps
    detected_at: datetime | None = None
    initial_scored_at: datetime | None = None
    behavior_scored_at: datetime | None = None
    first_surfaced_at: datetime | None = None


class LaunchSummary(BaseModel):
    """Lightweight launch record for list endpoints. v2.1."""
    launch_id: UUID
    token_address: str
    chain: str = "base"
    timestamp: datetime
    status: LaunchStatus
    action: ActionMode | None = None  # final if available, else initial
    overall_safety: int | None = None  # final if available, else initial
    smart_money_participation: int | None = None
    deployer_launch_velocity_24h: int = 0
    shadow: bool = False
    # v2 key fields
    position_action: PositionAction = PositionAction.NO_ENTRY
    rug_risk: int | None = None
    data_confidence: int | None = None
    scoring_version: str = "v1"
    # v2.1
    major_interest_flag: bool = False


class WalletView(BaseModel):
    """Wallet profile as consumed by NXFX01 / Hermes. v2."""
    wallet: str
    wallet_tier: WalletTier = WalletTier.UNKNOWN
    wallet_value_score: int = 0
    wallet_performance_score: int = 0
    cluster_id: str | None = None
    cluster_tier: ClusterTier | None = None
    alpha_cohort_flag: bool = False
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    # v2 CEX fields
    is_cex_funded: bool | None = None
    cex_funding_share: float | None = None
    funding_cex_list: list[str] = Field(default_factory=list)


class LaunchOutcomeView(BaseModel):
    """Launch + realized outcome metrics for self-learning. v2.1."""
    launch_id: UUID
    token_address: str
    timestamp: datetime

    # scores at time of launch
    overall_safety_initial: int | None = None
    overall_safety_final: int | None = None
    action_initial: ActionMode | None = None
    action_final: ActionMode | None = None
    policy_version: str | None = None
    scoring_version: str = "v1"

    # v2 sub-scores snapshot
    sub_scores: SubScores = Field(default_factory=SubScores)
    position_action: PositionAction = PositionAction.NO_ENTRY

    # v2.1 major interest at entry
    major_interest_flag_at_entry: bool = False
    major_interest_score_at_entry: int | None = None

    # outcome
    pnl_1h: float | None = None
    pnl_24h: float | None = None
    pnl_7d: float | None = None
    max_drawdown: float | None = None
    rugged: bool | None = None
    final_status: OutcomeStatus | None = None
    peak_mcap_usd: float | None = None


class DeRiskEventView(BaseModel):
    """A persisted de-risk trigger event."""
    id: UUID
    launch_id: UUID
    trigger_type: str
    severity: str
    detail: dict[str, Any] = Field(default_factory=dict)
    resolved: bool = False
    created_at: datetime
    resolved_at: datetime | None = None


class PolicySuggestionIn(BaseModel):
    """Input from NXFX01 proposing a policy change."""
    patch: dict[str, Any]
    rationale: str
    evidence_snapshot: dict[str, Any] | None = None


class PolicySuggestionOut(BaseModel):
    suggestion_id: UUID
    suggested_at: datetime
    status: str = "PENDING"
    patch: dict[str, Any]
    rationale: str
    evidence_snapshot: dict[str, Any] | None = None


class LatencyStats(BaseModel):
    """Pipeline latency metrics from /ops/latency."""
    stage: str
    p50_seconds: float | None = None
    p95_seconds: float | None = None
    sample_count: int = 0
