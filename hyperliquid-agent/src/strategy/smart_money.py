"""Smart Money confirmation layer.

Queries Hyperliquid leaderboard data and provides directional bias
as a confirmation signal. NEVER the sole reason to trade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.config import StrategyConfig
from src.market.freshness import FreshnessTracker
from src.market.types import LeaderboardEntry
from src.strategy.base import StrategySignal

logger = logging.getLogger(__name__)


@dataclass
class SmartMoneyBias:
    """Directional bias from smart money analysis."""

    direction: str | None  # "long", "short", or None (no signal)
    confidence_modifier: float  # multiplier: 0.8–1.2
    num_leaders_sampled: int
    rationale: str


class SmartMoneyConfirmation:
    """Analyzes leaderboard data to provide directional confirmation.

    This is a CONFIRMATION LAYER ONLY — never produces standalone trade signals.
    """

    def __init__(self, freshness: FreshnessTracker):
        self._freshness = freshness
        self._leaders: list[LeaderboardEntry] = []

    def update_leaders(self, leaders: list[LeaderboardEntry]) -> None:
        """Update cached leaderboard data."""
        self._leaders = leaders
        self._freshness.record("smart_money")

    def is_available(self, config: StrategyConfig) -> bool:
        """Check if smart money data is fresh enough to use."""
        if not config.smart_money.enabled:
            return False
        max_age = config.smart_money.max_freshness_minutes * 60
        return self._freshness.is_fresh("smart_money", max_age)

    def get_bias(self, config: StrategyConfig) -> SmartMoneyBias:
        """Compute directional bias from top traders.

        Returns a confidence modifier that can be applied to other signals.
        """
        if not self.is_available(config):
            return SmartMoneyBias(
                direction=None,
                confidence_modifier=1.0,
                num_leaders_sampled=0,
                rationale="Smart money data unavailable or stale",
            )

        if not self._leaders:
            return SmartMoneyBias(
                direction=None,
                confidence_modifier=1.0,
                num_leaders_sampled=0,
                rationale="No leaderboard data loaded",
            )

        # Analyze PnL direction of top performers
        top_n = self._leaders[:20]
        positive = sum(1 for l in top_n if l.pnl > 0)
        negative = len(top_n) - positive
        total = len(top_n)

        if total == 0:
            return SmartMoneyBias(
                direction=None,
                confidence_modifier=1.0,
                num_leaders_sampled=0,
                rationale="Empty leaderboard",
            )

        bull_pct = positive / total

        if bull_pct >= 0.7:
            direction = "long"
            modifier = 1.1 + (bull_pct - 0.7) * 0.33
            rationale = f"{positive}/{total} top traders profitable (bullish bias {bull_pct:.0%})"
        elif bull_pct <= 0.3:
            direction = "short"
            modifier = 1.1 + (0.3 - bull_pct) * 0.33
            rationale = f"{negative}/{total} top traders underwater (bearish bias {1-bull_pct:.0%})"
        else:
            direction = None
            modifier = 1.0
            rationale = f"Mixed signals from top traders ({bull_pct:.0%} profitable)"

        return SmartMoneyBias(
            direction=direction,
            confidence_modifier=min(modifier, 1.2),
            num_leaders_sampled=total,
            rationale=rationale,
        )

    def enrich_signal(
        self, signal: StrategySignal, config: StrategyConfig
    ) -> StrategySignal:
        """Apply smart money confirmation to an existing signal.

        Boosts confidence if smart money agrees, reduces if it disagrees.
        """
        bias = self.get_bias(config)

        if bias.direction is None:
            return signal

        if bias.direction == signal.side:
            new_confidence = min(0.95, signal.confidence * bias.confidence_modifier)
            new_rationale = signal.rationale + f" | Smart money confirms: {bias.rationale}"
        else:
            new_confidence = signal.confidence * 0.8
            new_rationale = signal.rationale + f" | Smart money diverges: {bias.rationale}"

        return signal.model_copy(update={
            "confidence": new_confidence,
            "rationale": new_rationale,
            "constraints_checked": signal.constraints_checked + ["smart_money_freshness"],
        })
