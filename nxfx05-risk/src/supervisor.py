"""NXFX05 Risk Supervisor.

Produces portfolio-level risk limits consumed by NXFX02. Monitors drawdown,
tracks open positions / daily trades, and applies regime-based overrides.

In production this reads from DB / portfolio state. This implementation
provides a config-driven supervisor with live state tracking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "nxfx-shared" / "src"))

from enums import MarketRegime
from nxfx05_risk_limits import (
    DrawdownState,
    NXFX05RiskLimits,
    RegimeOverride,
)

logger = logging.getLogger("nxfx05")


@dataclass
class RiskConfig:
    """Static configuration for the risk supervisor."""

    # Base risk parameters
    risk_per_trade_base: float = 0.005       # 0.5% equity per trade
    max_per_token_exposure: float = 0.03     # 3% equity per token
    max_concurrent_fast_positions: int = 5
    max_daily_trades: int = 30

    # Drawdown limits
    dd_soft_limit: float = 0.10              # 10% equity
    dd_hard_limit: float = 0.20              # 20% equity

    # Regime multipliers
    regime_hot_multiplier: float = 1.0
    regime_normal_multiplier: float = 0.7
    regime_cold_multiplier: float = 0.25
    regime_cold_allow_fast: bool = False

    # Drawdown-based throttle: below this DD%, reduce daily trade cap
    dd_throttle_start: float = 0.05          # start throttling at 5% DD
    dd_throttle_trade_cap_min: int = 10      # floor for daily trade cap


@dataclass
class LivePortfolioMetrics:
    """Live portfolio state fed into the supervisor."""

    equity_usd: float = 10_000.0
    peak_equity_usd: float = 10_000.0
    current_fast_positions: int = 0
    trades_today: int = 0
    current_regime: MarketRegime = MarketRegime.NORMAL


class NXFX05Supervisor:
    """Risk Supervisor — produces NXFX05RiskLimits snapshots.

    Call `produce_limits()` to get the current risk limits object.
    Updates should be pushed to this supervisor via `update_metrics()`.
    """

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        self._metrics = LivePortfolioMetrics()
        self._force_halt = False

    def update_metrics(self, metrics: LivePortfolioMetrics) -> None:
        """Update live portfolio metrics (called by portfolio tracker)."""
        self._metrics = metrics

    def halt_trading(self) -> None:
        """Emergency kill switch — disables all new entries."""
        self._force_halt = True
        logger.warning("NXFX05: Trading halted by emergency kill switch")

    def resume_trading(self) -> None:
        """Resume trading after halt."""
        self._force_halt = False
        logger.info("NXFX05: Trading resumed")

    def produce_limits(self) -> NXFX05RiskLimits:
        """Produce a risk limits snapshot based on current state."""

        cfg = self.config
        m = self._metrics

        # Compute drawdown
        dd_pct = 0.0
        if m.peak_equity_usd > 0:
            dd_pct = max(0.0, (m.peak_equity_usd - m.equity_usd) / m.peak_equity_usd)

        # Allow new entries?
        allow = not self._force_halt and dd_pct < cfg.dd_hard_limit

        # Drawdown-based daily trade cap throttle
        daily_cap = cfg.max_daily_trades
        if dd_pct >= cfg.dd_throttle_start and cfg.dd_soft_limit > cfg.dd_throttle_start:
            throttle_frac = (dd_pct - cfg.dd_throttle_start) / (
                cfg.dd_soft_limit - cfg.dd_throttle_start
            )
            throttle_frac = min(throttle_frac, 1.0)
            daily_cap = max(
                cfg.dd_throttle_trade_cap_min,
                int(cfg.max_daily_trades * (1.0 - throttle_frac * 0.66)),
            )

        # Build regime overrides
        regime_overrides = {
            MarketRegime.HOT: RegimeOverride(
                risk_multiplier=cfg.regime_hot_multiplier
            ),
            MarketRegime.NORMAL: RegimeOverride(
                risk_multiplier=cfg.regime_normal_multiplier
            ),
            MarketRegime.COLD: RegimeOverride(
                risk_multiplier=cfg.regime_cold_multiplier,
                allow_fast=cfg.regime_cold_allow_fast,
            ),
        }

        limits = NXFX05RiskLimits(
            timestamp=datetime.now(timezone.utc),
            risk_per_trade_base=cfg.risk_per_trade_base,
            max_per_token_exposure=cfg.max_per_token_exposure,
            max_concurrent_fast_positions=cfg.max_concurrent_fast_positions,
            max_daily_trades=daily_cap,
            allow_new_entries=allow,
            drawdown_state=DrawdownState(
                current_dd_pct=round(dd_pct, 6),
                dd_soft_limit=cfg.dd_soft_limit,
                dd_hard_limit=cfg.dd_hard_limit,
            ),
            regime_overrides=regime_overrides,
        )

        if not allow:
            logger.warning(
                "NXFX05: New entries BLOCKED (dd=%.2f%%, halt=%s)",
                dd_pct * 100,
                self._force_halt,
            )

        return limits
