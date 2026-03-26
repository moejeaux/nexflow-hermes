"""Tests for BTC regime detection."""

import pytest

from src.config import BtcRegimeConfig
from src.market.types import Candle
from src.strategy.regime import BtcRegime, detect_regime, _ema
from datetime import datetime, timezone


def _make_candles(closes: list[float]) -> list[Candle]:
    """Create candle list from close prices."""
    return [
        Candle(
            timestamp=datetime(2024, 1, 1, i * 4, tzinfo=timezone.utc),
            open=c * 0.999, high=c * 1.01,
            low=c * 0.99, close=c, volume=100.0,
        )
        for i, c in enumerate(closes)
    ]


class TestEma:
    def test_single_value(self):
        assert _ema([100.0], 20) == [100.0]

    def test_constant_series(self):
        result = _ema([50.0] * 30, 10)
        assert all(abs(v - 50.0) < 0.01 for v in result)

    def test_trending_up(self):
        values = list(range(1, 31))
        result = _ema([float(v) for v in values], 10)
        # EMA should be below last close in uptrend
        assert result[-1] < values[-1]
        assert result[-1] > values[0]


class TestDetectRegime:
    def test_not_enough_candles(self):
        candles = _make_candles([100.0] * 10)
        regime = detect_regime(candles)
        assert regime == BtcRegime.NEUTRAL

    def test_bullish_uptrend(self):
        # Create a clear uptrend: price monotonically increasing
        closes = [50000.0 + i * 500.0 for i in range(60)]
        candles = _make_candles(closes)
        regime = detect_regime(candles)
        assert regime == BtcRegime.BULLISH

    def test_bearish_downtrend(self):
        # Create a clear downtrend
        closes = [70000.0 - i * 500.0 for i in range(60)]
        candles = _make_candles(closes)
        regime = detect_regime(candles)
        assert regime == BtcRegime.BEARISH

    def test_neutral_sideways(self):
        # Sideways oscillation
        closes = [60000.0 + (100.0 if i % 2 == 0 else -100.0) for i in range(60)]
        candles = _make_candles(closes)
        regime = detect_regime(candles)
        assert regime == BtcRegime.NEUTRAL

    def test_custom_config(self):
        config = BtcRegimeConfig(trend_ema_fast=10, trend_ema_slow=30)
        closes = [50000.0 + i * 500.0 for i in range(60)]
        candles = _make_candles(closes)
        regime = detect_regime(candles, config)
        assert regime == BtcRegime.BULLISH
