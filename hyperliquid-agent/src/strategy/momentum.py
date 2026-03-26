"""Directional Momentum / Trend Following strategy.

Uses EMA crossover with BTC regime gating and asymmetric R:R.
- Entry on pullbacks into trend (not blind breakouts)
- BTC 4H regime must be non-bearish for long entries
- Minimum reward:risk ratio enforced
"""

from __future__ import annotations

import logging

from src.config import StrategyConfig
from src.market.types import Candle
from src.strategy.base import MarketSnapshot, Strategy, StrategySignal
from src.strategy.regime import BtcRegime, detect_regime

logger = logging.getLogger(__name__)


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * multiplier + result[-1] * (1 - multiplier))
    return result


def _atr(candles: list[Candle], period: int = 14) -> float:
    """Compute Average True Range."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        high_low = candles[i].high - candles[i].low
        high_prev_close = abs(candles[i].high - candles[i - 1].close)
        low_prev_close = abs(candles[i].low - candles[i - 1].close)
        trs.append(max(high_low, high_prev_close, low_prev_close))
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0.0
    return sum(trs[-period:]) / period


class MomentumStrategy(Strategy):

    @property
    def name(self) -> str:
        return "momentum"

    def is_enabled(self, config: StrategyConfig) -> bool:
        return config.momentum.enabled

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        config: StrategyConfig,
    ) -> list[StrategySignal]:
        signals: list[StrategySignal] = []
        mc = config.momentum
        allowed = config.allowed_markets.perps

        # Determine BTC regime for gating
        btc_regime = BtcRegime.NEUTRAL
        btc_candles = snapshot.candles.get("BTC_4h", [])
        if btc_candles:
            btc_regime = detect_regime(btc_candles, config.btc_regime)

        for coin in allowed:
            candle_key = f"{coin}_4h"
            candles = snapshot.candles.get(candle_key, [])
            if len(candles) < 55:
                continue

            closes = [c.close for c in candles]
            ema_fast = _ema(closes, 20)
            ema_slow = _ema(closes, 50)

            current_price = closes[-1]
            fast_val = ema_fast[-1]
            slow_val = ema_slow[-1]

            atr = _atr(candles)
            if atr <= 0:
                continue

            # Determine trend direction
            if fast_val > slow_val:
                trend = "bullish"
            elif fast_val < slow_val:
                trend = "bearish"
            else:
                continue

            # Check for pullback into trend (price near fast EMA)
            if mc.entry_on_pullback and not mc.allow_breakout_entry:
                pullback_tolerance = atr * 1.5
                if trend == "bullish" and current_price > fast_val + pullback_tolerance:
                    continue  # price too far above — wait for pullback
                if trend == "bearish" and current_price < fast_val - pullback_tolerance:
                    continue

            # ATR-based stops
            stop_distance = atr * 2
            take_profit_distance = stop_distance * mc.min_rr_ratio
            stop_loss_pct = stop_distance / current_price
            take_profit_pct = take_profit_distance / current_price

            if trend == "bullish":
                # BTC regime gate for longs
                if mc.btc_regime_gate and btc_regime == BtcRegime.BEARISH:
                    logger.debug(
                        "Momentum: skipping LONG %s — BTC regime is BEARISH", coin
                    )
                    continue

                side = "long"
                rationale = (
                    f"Momentum LONG on {coin}: EMA20 ({fast_val:.2f}) > EMA50 ({slow_val:.2f}), "
                    f"price {current_price:.2f} pulled back toward trend. "
                    f"BTC regime={btc_regime.value}. "
                    f"R:R={mc.min_rr_ratio}:1, ATR-based SL={stop_loss_pct:.2%}."
                )
            else:
                side = "short"
                rationale = (
                    f"Momentum SHORT on {coin}: EMA20 ({fast_val:.2f}) < EMA50 ({slow_val:.2f}), "
                    f"price {current_price:.2f} pulled back toward trend. "
                    f"BTC regime={btc_regime.value}. "
                    f"R:R={mc.min_rr_ratio}:1, ATR-based SL={stop_loss_pct:.2%}."
                )

            # Confidence based on trend strength
            ema_separation = abs(fast_val - slow_val) / slow_val
            confidence = min(0.85, 0.4 + ema_separation * 10)

            signals.append(StrategySignal(
                strategy_name=self.name,
                coin=coin,
                side=side,
                confidence=confidence,
                recommended_size_pct=config.risk.risk_per_trade_pct,
                leverage=min(config.risk.max_leverage_per_asset, 5.0),
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
                rationale=rationale,
                constraints_checked=[
                    "btc_regime_long_block",
                    "allowed_markets_check",
                    "max_leverage_check",
                ],
            ))

        signals.sort(key=lambda s: s.confidence, reverse=True)
        return signals
