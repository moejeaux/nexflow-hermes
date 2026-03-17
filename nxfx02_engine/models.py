"""
Data models for NXFX01 → NXFX02, NXFX05 → NXFX02, NXFX02 → Execution Worker, NXFX03 mempool_features.
All models are strictly typed and match JSON contracts in the spec.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime

@dataclass
class NXFX01Scores:
    """Sub-scores for launch analysis."""
    contract_safety: int
    deployer_reputation: int
    funding_risk: int
    smart_money_alignment: int
    whale_behavior: int
    graph_risk: int
    rug_risk: int
    liquidity_quality: int
    social_quality: int
    data_confidence: int
    modulated_score: int

@dataclass
class NXFX01Flags:
    """Final action and flags for launch."""
    action_final: str  # "FAST" | "WAIT" | "BLOCK"
    major_interest: bool
    is_cex_funded: bool

@dataclass
class NXFX01MarketContext:
    """Market regime and cycle score."""
    regime: str  # "HOT" | "NORMAL" | "COLD"
    cycle_score: float

@dataclass
class NXFX01LiquiditySnapshot:
    """Liquidity and trading snapshot."""
    lp_usd: float
    volume_1h_usd: float
    spread_bp: float
    max_trade_usd_at_1pct_slippage: float

@dataclass
class MempoolSmartFlow:
    """Pending smart-money/whale flows."""
    pending_smart_buy_volume: float
    pending_smart_sell_volume: float
    pending_smart_buy_ratio: float
    pending_smart_sell_ratio: float
    pending_smart_buy_count: int
    pending_smart_sell_count: int
    pending_smart_buy_fee_urgency_max: float
    pending_smart_sell_fee_urgency_max: float

@dataclass
class MempoolAnomalies:
    """Anomaly metrics for mempool swaps."""
    tiny_swap_count: int
    total_pending_swap_count: int
    tiny_swap_density: float
    new_addr_tiny_swap_count: int

@dataclass
class MempoolDerivedFlags:
    """Flags derived from mempool features."""
    has_strong_pending_smart_buy: bool
    has_strong_pending_smart_sell: bool
    high_tiny_swap_density: bool

@dataclass
class MempoolFeatures:
    """Full mempool features for a launch."""
    mempool_smart_flow: MempoolSmartFlow
    mempool_anomalies: MempoolAnomalies
    derived_flags: MempoolDerivedFlags

@dataclass
class NXFX01LaunchPayload:
    """Payload from NXFX01 to NXFX02 for launch decision."""
    launch_id: str
    token_address: str
    chain: str
    timestamp: datetime
    scores: NXFX01Scores
    flags: NXFX01Flags
    market_context: NXFX01MarketContext
    liquidity_snapshot: NXFX01LiquiditySnapshot
    mempool_features: Optional[MempoolFeatures] = None

@dataclass
class DrawdownState:
    """Drawdown tracking for risk limits."""
    current_dd_pct: float
    dd_soft_limit: float
    dd_hard_limit: float

@dataclass
class RegimeOverride:
    """Risk regime overrides."""
    risk_multiplier: float
    allow_fast: Optional[bool] = True

@dataclass
class NXFX05RiskLimits:
    """Global risk configuration from NXFX05 to NXFX02."""
    timestamp: datetime
    risk_per_trade_base: float
    max_per_token_exposure: float
    max_concurrent_fast_positions: int
    max_daily_trades: int
    allow_new_entries: bool
    drawdown_state: DrawdownState
    regime_overrides: Dict[str, RegimeOverride]

@dataclass
class ExecutionDecision:
    """Decision to execute or skip a trade."""
    execute: bool
    reason: str

@dataclass
class ExecutionSizing:
    """Sizing for execution plan."""
    target_position_notional_usd: float
    max_additional_notional_usd: float
    expected_risk_pct_of_equity: float

@dataclass
class ExecutionParams:
    """Execution parameters for trade."""
    side: str  # "BUY" | "SELL"
    entry_style: str  # "single" | "sliced"
    slice_count: int
    max_slippage_pct: float
    order_type: str  # "market" | "limit"
    time_in_force: str  # "ioc" | "gtc"
    deadline_ts: datetime

@dataclass
class NXFX02ExecutionPlan:
    """Execution plan from NXFX02 to Execution Worker."""
    launch_id: str
    token_address: str
    chain: str
    decision: ExecutionDecision
    sizing: ExecutionSizing
    execution: ExecutionParams

@dataclass
class NXFX03MempoolFeatures:
    """Periodic mempool features output from NXFX03."""
    token_address: str
    chain: str
    window_seconds: int
    timestamp: datetime
    mempool_smart_flow: MempoolSmartFlow
    mempool_anomalies: MempoolAnomalies
    derived_flags: MempoolDerivedFlags

@dataclass
class TradeExecutionResult:
    """Result of trade execution attempt."""
    trade_id: str
    status: str  # "EXECUTED" | "PARTIAL" | "ABORTED"
    filled_notional_usd: float
    avg_price: float
    reason: str
    tx_hashes: List[str]
    timestamp: datetime
