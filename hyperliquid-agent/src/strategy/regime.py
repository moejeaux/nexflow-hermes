"""BTC macro regime detector — 4H EMA crossover gate."""

from __future__ import annotations

import logging
from enum import Enum

from src.config import BtcRegimeConfig
from src.market.types import Candle

logger = logging.getLogger(__name__)


class BtcRegime(str, Enum):
    BULLISH = "bullish"     # EMA fast > EMA slow, price above both
    NEUTRAL = "neutral"     # Mixed signals
    BEARISH = "bearish"     # EMA fast < EMA slow, price below both


def _ema(values: list[float], period: int) -> list[float]:
    """Compute EMA over a list of float values."""
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * multiplier + result[-1] * (1 - multiplier))
    return result


def detect_regime(candles_4h: list[Candle], config: BtcRegimeConfig | None = None) -> BtcRegime:
    """Determine BTC macro regime from 4H candles.

    Uses EMA crossover:
        - BULLISH: fast EMA > slow EMA and current price > both EMAs
        - BEARISH: fast EMA < slow EMA and current price < both EMAs
        - NEUTRAL: everything else
    """
    config = config or BtcRegimeConfig()

    if len(candles_4h) < config.trend_ema_slow + 5:
        logger.warning(
            "Not enough candles for regime detection (%d < %d), defaulting to NEUTRAL",
            len(candles_4h),
            config.trend_ema_slow + 5,
        )
        return BtcRegime.NEUTRAL

    closes = [c.close for c in candles_4h]
    ema_fast = _ema(closes, config.trend_ema_fast)
    ema_slow = _ema(closes, config.trend_ema_slow)

    current_price = closes[-1]
    fast_val = ema_fast[-1]
    slow_val = ema_slow[-1]

    if fast_val > slow_val and current_price > fast_val and current_price > slow_val:
        regime = BtcRegime.BULLISH
    elif fast_val < slow_val and current_price < fast_val and current_price < slow_val:
        regime = BtcRegime.BEARISH
    else:
        regime = BtcRegime.NEUTRAL

    logger.info(
        "BTC regime: %s (price=%.1f, EMA%d=%.1f, EMA%d=%.1f)",
        regime.value,
        current_price,
        config.trend_ema_fast,
        fast_val,
        config.trend_ema_slow,
        slow_val,
    )
    return regime
