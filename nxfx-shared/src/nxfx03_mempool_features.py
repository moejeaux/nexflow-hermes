"""Contract D: NXFX03 mempool features output (per token, per window).

NXFX03 (Mempool Intelligence) produces these feature snapshots. They flow
into NXFX01's scoring pipeline (SM/Whale scorers, de-risk triggers) and
are also embedded in the NXFX01 → NXFX02 launch payload.

NXFX03 produces features only — no trading decisions.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MempoolSmartFlow(BaseModel):
    """Aggregated pending smart-money swap metrics."""
    pending_smart_buy_volume: float = 0.0
    pending_smart_sell_volume: float = 0.0
    pending_smart_buy_ratio: float = 0.0
    pending_smart_sell_ratio: float = 0.0
    pending_smart_buy_count: int = 0
    pending_smart_sell_count: int = 0
    pending_smart_buy_fee_urgency_max: float = 0.0
    pending_smart_sell_fee_urgency_max: float = 0.0


class MempoolAnomalies(BaseModel):
    """Anomaly detection over pending swap population."""
    tiny_swap_count: int = 0
    total_pending_swap_count: int = 0
    tiny_swap_density: float = 0.0
    new_addr_tiny_swap_count: int = 0


class MempoolDerivedFlags(BaseModel):
    """Boolean flags derived by applying thresholds to raw features."""
    has_strong_pending_smart_buy: bool = False
    has_strong_pending_smart_sell: bool = False
    high_tiny_swap_density: bool = False


class NXFX03MempoolFeatures(BaseModel):
    """Contract D: Per-token mempool feature snapshot from NXFX03.

    Produced every `window_seconds` for each tracked token. Consumed by
    NXFX01 scoring pipeline and embedded in NXFX01 → NXFX02 payloads.
    """
    token_address: str
    chain: str = "base"
    window_seconds: int = 10
    timestamp: datetime

    mempool_smart_flow: MempoolSmartFlow = Field(
        default_factory=MempoolSmartFlow
    )
    mempool_anomalies: MempoolAnomalies = Field(
        default_factory=MempoolAnomalies
    )
    derived_flags: MempoolDerivedFlags = Field(
        default_factory=MempoolDerivedFlags
    )
