"""Tests for funding carry strategy."""

import pytest
from datetime import datetime, timezone

from src.config import StrategyConfig
from src.market.types import AccountState, FundingRate
from src.strategy.base import MarketSnapshot
from src.strategy.funding_carry import FundingCarryStrategy


@pytest.fixture
def config():
    return StrategyConfig()


@pytest.fixture
def strategy():
    return FundingCarryStrategy()


def _make_snapshot(
    funding_rates: list[FundingRate],
    equity: float = 10_000.0,
) -> MarketSnapshot:
    mids = {r.coin: 50000.0 if r.coin == "BTC" else 3000.0 for r in funding_rates}
    return MarketSnapshot(
        mids=mids,
        candles={},
        funding_rates=funding_rates,
        account=AccountState(
            equity=equity, available_margin=equity * 0.9,
            total_margin_used=0, positions=[],
        ),
    )


class TestFundingCarryStrategy:
    def test_is_enabled(self, strategy, config):
        assert strategy.is_enabled(config) is True
        config.funding_carry.enabled = False
        assert strategy.is_enabled(config) is False

    def test_name(self, strategy):
        assert strategy.name == "funding_carry"

    def test_no_signals_below_threshold(self, strategy, config):
        rates = [
            FundingRate(coin="BTC", rate=0.00001),  # very low
            FundingRate(coin="ETH", rate=-0.00001),
        ]
        snapshot = _make_snapshot(rates)
        signals = strategy.evaluate(snapshot, config)
        assert len(signals) == 0

    def test_short_on_high_positive_funding(self, strategy, config):
        rates = [
            FundingRate(coin="BTC", rate=0.01),  # high positive
        ]
        snapshot = _make_snapshot(rates)
        signals = strategy.evaluate(snapshot, config)
        assert len(signals) == 1
        assert signals[0].side == "short"  # collect from longs
        assert signals[0].coin == "BTC"

    def test_long_on_high_negative_funding(self, strategy, config):
        rates = [
            FundingRate(coin="ETH", rate=-0.01),  # high negative
        ]
        snapshot = _make_snapshot(rates)
        signals = strategy.evaluate(snapshot, config)
        assert len(signals) == 1
        assert signals[0].side == "long"  # collect from shorts
        assert signals[0].coin == "ETH"

    def test_filters_disallowed_coins(self, strategy, config):
        rates = [
            FundingRate(coin="SHIB", rate=0.05),  # high rate but not in allowed
        ]
        snapshot = _make_snapshot(rates)
        signals = strategy.evaluate(snapshot, config)
        assert len(signals) == 0

    def test_sorted_by_confidence(self, strategy, config):
        rates = [
            FundingRate(coin="BTC", rate=0.005),
            FundingRate(coin="ETH", rate=0.02),  # higher rate
        ]
        snapshot = _make_snapshot(rates)
        signals = strategy.evaluate(snapshot, config)
        assert len(signals) == 2
        assert signals[0].coin == "ETH"  # highest rate first
        assert signals[0].confidence > signals[1].confidence

    def test_signal_has_rationale(self, strategy, config):
        rates = [FundingRate(coin="BTC", rate=0.01)]
        snapshot = _make_snapshot(rates)
        signals = strategy.evaluate(snapshot, config)
        assert "funding" in signals[0].rationale.lower()
        assert "BTC" in signals[0].rationale
