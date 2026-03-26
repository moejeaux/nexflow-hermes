"""Funding Rate Carry strategy.

Harvests funding payments by taking the opposite side when rates are extreme.
- Positive funding (longs pay shorts) → go SHORT to collect
- Negative funding (shorts pay longs) → go LONG to collect
"""

from __future__ import annotations

import logging

from src.config import StrategyConfig
from src.market.types import FundingRate
from src.strategy.base import MarketSnapshot, Strategy, StrategySignal

logger = logging.getLogger(__name__)


class FundingCarryStrategy(Strategy):

    @property
    def name(self) -> str:
        return "funding_carry"

    def is_enabled(self, config: StrategyConfig) -> bool:
        return config.funding_carry.enabled

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        config: StrategyConfig,
    ) -> list[StrategySignal]:
        signals: list[StrategySignal] = []
        fc = config.funding_carry
        allowed = config.allowed_markets.perps

        for rate in snapshot.funding_rates:
            if not isinstance(rate, FundingRate):
                continue
            if rate.coin not in allowed:
                continue

            hourly = rate.hourly
            if abs(hourly) < fc.min_funding_rate_hourly:
                continue

            # Positive funding → longs pay shorts → go SHORT to collect
            # Negative funding → shorts pay longs → go LONG to collect
            if hourly > 0:
                side = "short"
                rationale = (
                    f"Funding carry SHORT on {rate.coin}: hourly rate "
                    f"{hourly:.6f} > threshold {fc.min_funding_rate_hourly:.6f}. "
                    f"Longs pay shorts — collect funding by going short."
                )
            else:
                side = "long"
                rationale = (
                    f"Funding carry LONG on {rate.coin}: hourly rate "
                    f"{abs(hourly):.6f} > threshold {fc.min_funding_rate_hourly:.6f}. "
                    f"Shorts pay longs — collect funding by going long."
                )

            # Size based on confidence from rate magnitude
            # Higher |rate| → higher confidence → larger position
            rate_multiple = abs(hourly) / fc.min_funding_rate_hourly
            confidence = min(0.9, 0.5 + (rate_multiple - 1) * 0.1)

            # Stop-loss: 2x the expected 8h funding payment
            # If funding is 0.01% per 8h, stop at 0.02% adverse move
            eight_h_rate = abs(rate.rate)
            stop_loss_pct = max(0.005, eight_h_rate * 2)  # floor at 0.5%
            take_profit_pct = eight_h_rate * 4  # 4x the funding payment

            signals.append(StrategySignal(
                strategy_name=self.name,
                coin=rate.coin,
                side=side,
                confidence=confidence,
                recommended_size_pct=config.risk.risk_per_trade_pct,
                leverage=min(3.0, config.risk.max_leverage_per_asset),  # conservative for carry
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
                rationale=rationale,
                constraints_checked=["funding_rate_minimum", "allowed_markets_check"],
            ))

        # Sort by confidence (highest rate magnitude first)
        signals.sort(key=lambda s: s.confidence, reverse=True)

        if signals:
            logger.info(
                "Funding carry: %d opportunities found (top: %s %s %.4f confidence)",
                len(signals),
                signals[0].coin,
                signals[0].side,
                signals[0].confidence,
            )

        return signals
