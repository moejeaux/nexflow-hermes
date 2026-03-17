"""Contract A: NXFX01 → NXFX02 launch intelligence payload.

This is the canonical interface that NXFX01 (Launch & Regime Intelligence)
emits per-launch for NXFX02 (Position & Execution Engine) to consume.
NXFX01 owns scoring and classification — no sizing, no execution.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from enums import ActionMode, MarketRegime


# ---- Nested sub-models ----


class LaunchScoresPayload(BaseModel):
    """All scoring dimensions from NXFX01 v2.1 pipeline."""
    contract_safety: int = 0
    deployer_reputation: int = 0
    funding_risk: int = 0
    smart_money_alignment: int = 0
    whale_behavior: int = 0
    graph_risk: int = 0          # 0-100, higher = riskier
    rug_risk: int = 0            # 0-100, higher = riskier
    liquidity_quality: int = 0
    social_quality: int = 0
    data_confidence: int = 0
    modulated_score: int = 0     # final score after confidence modulation


class LaunchFlagsPayload(BaseModel):
    """Action mode and key boolean flags."""
    action_final: ActionMode = ActionMode.WAIT
    major_interest: bool = False
    is_cex_funded: bool = False


class MarketContextPayload(BaseModel):
    """Market regime context at time of evaluation."""
    regime: MarketRegime = MarketRegime.NORMAL
    cycle_score: float = 0.0


class LiquiditySnapshotPayload(BaseModel):
    """Real-time liquidity conditions for the token's pool."""
    lp_usd: float = 0.0
    volume_1h_usd: float = 0.0
    spread_bp: float = 0.0
    max_trade_usd_at_1pct_slippage: float = 0.0


class MempoolSmartFlowPayload(BaseModel):
    """Aggregated pending smart-money swap features from NXFX03."""
    pending_smart_buy_volume: float = 0.0
    pending_smart_sell_volume: float = 0.0
    pending_smart_buy_ratio: float = 0.0
    pending_smart_sell_ratio: float = 0.0
    pending_smart_buy_count: int = 0
    pending_smart_sell_count: int = 0
    pending_smart_buy_fee_urgency_max: float = 0.0
    pending_smart_sell_fee_urgency_max: float = 0.0


class MempoolAnomaliesPayload(BaseModel):
    """Anomaly detection features from pending mempool swaps."""
    tiny_swap_count: int = 0
    total_pending_swap_count: int = 0
    tiny_swap_density: float = 0.0
    new_addr_tiny_swap_count: int = 0


class MempoolDerivedFlagsPayload(BaseModel):
    """Boolean flags derived from mempool feature thresholds."""
    has_strong_pending_smart_buy: bool = False
    has_strong_pending_smart_sell: bool = False
    high_tiny_swap_density: bool = False


class MempoolFeaturesPayload(BaseModel):
    """Complete mempool features block, embedded in launch payload."""
    mempool_smart_flow: MempoolSmartFlowPayload = Field(
        default_factory=MempoolSmartFlowPayload
    )
    mempool_anomalies: MempoolAnomaliesPayload = Field(
        default_factory=MempoolAnomaliesPayload
    )
    derived_flags: MempoolDerivedFlagsPayload = Field(
        default_factory=MempoolDerivedFlagsPayload
    )


# ---- Top-level contract ----


class NXFX01LaunchPayload(BaseModel):
    """Contract A: Full launch intelligence payload from NXFX01 → NXFX02.

    NXFX01 emits one of these per launch that reaches FAST or WAIT status.
    NXFX02 consumes it alongside NXFX05 risk limits to decide sizing/execution.
    """
    launch_id: str
    token_address: str
    chain: str = "base"
    timestamp: datetime

    scores: LaunchScoresPayload = Field(default_factory=LaunchScoresPayload)
    flags: LaunchFlagsPayload = Field(default_factory=LaunchFlagsPayload)
    market_context: MarketContextPayload = Field(default_factory=MarketContextPayload)
    liquidity_snapshot: LiquiditySnapshotPayload = Field(
        default_factory=LiquiditySnapshotPayload
    )
    mempool_features: MempoolFeaturesPayload = Field(
        default_factory=MempoolFeaturesPayload
    )
