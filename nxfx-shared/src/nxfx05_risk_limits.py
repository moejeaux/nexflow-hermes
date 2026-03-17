"""Contract B: NXFX05 → NXFX02 global risk limits.

NXFX05 (Risk Supervisor & Governance) produces these portfolio-level risk
constraints. NXFX02 must respect them when sizing and deciding execution.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from enums import MarketRegime


class DrawdownState(BaseModel):
    """Current drawdown metrics for the portfolio."""
    current_dd_pct: float = 0.0
    dd_soft_limit: float = 0.10    # 10% equity
    dd_hard_limit: float = 0.20    # 20% equity


class RegimeOverride(BaseModel):
    """Per-regime risk adjustments."""
    risk_multiplier: float = 1.0
    allow_fast: bool = True


class NXFX05RiskLimits(BaseModel):
    """Contract B: Global risk limits from NXFX05 → NXFX02.

    Emitted periodically (every few seconds or on state change) by NXFX05.
    NXFX02 must never exceed these limits when producing execution plans.
    """
    timestamp: datetime

    # Per-trade risk as fraction of equity
    risk_per_trade_base: float = 0.005       # 0.5% equity

    # Maximum exposure to any single token as fraction of equity
    max_per_token_exposure: float = 0.03     # 3% equity

    # Concurrency and daily caps
    max_concurrent_fast_positions: int = 5
    max_daily_trades: int = 30

    # Master kill switch
    allow_new_entries: bool = True

    # Drawdown state
    drawdown_state: DrawdownState = Field(default_factory=DrawdownState)

    # Regime-specific overrides
    regime_overrides: dict[MarketRegime, RegimeOverride] = Field(
        default_factory=lambda: {
            MarketRegime.HOT: RegimeOverride(risk_multiplier=1.0),
            MarketRegime.NORMAL: RegimeOverride(risk_multiplier=0.7),
            MarketRegime.COLD: RegimeOverride(
                risk_multiplier=0.25, allow_fast=False
            ),
        }
    )
