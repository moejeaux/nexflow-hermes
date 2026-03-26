"""Tests for hard constraints."""

import os
from unittest.mock import patch

import pytest

from src.config import StrategyConfig
from src.market.freshness import FreshnessTracker
from src.risk.constraints import (
    ConstraintResult,
    PortfolioState,
    ProposedAction,
    allowed_markets_check,
    btc_regime_long_block,
    data_freshness_check,
    funding_rate_minimum,
    kill_switch_check,
    max_concurrent_positions_check,
    max_daily_loss_check,
    max_drawdown_check,
    max_leverage_check,
    max_risk_per_trade_check,
    validate_all,
)
from src.strategy.regime import BtcRegime


@pytest.fixture
def config():
    return StrategyConfig()


@pytest.fixture
def freshness():
    f = FreshnessTracker()
    f.record("prices")
    f.record("funding")
    f.record("account_state")
    return f


@pytest.fixture
def state():
    return PortfolioState(
        equity=10_000.0,
        peak_equity=10_000.0,
        num_positions=0,
        btc_regime=BtcRegime.NEUTRAL,
    )


@pytest.fixture
def action():
    return ProposedAction(
        coin="ETH", side="long", size_usd=200.0,
        leverage=5.0, strategy_name="momentum",
    )


class TestKillSwitch:
    def test_off(self):
        with patch.dict(os.environ, {"HL_KILL_SWITCH": "false"}):
            r = kill_switch_check()
            assert r.passed

    def test_on(self):
        with patch.dict(os.environ, {"HL_KILL_SWITCH": "true"}):
            r = kill_switch_check()
            assert not r.passed
            assert "Kill switch" in r.violation


class TestBtcRegimeLongBlock:
    def test_bearish_blocks_longs(self, action):
        state = PortfolioState(equity=10_000, btc_regime=BtcRegime.BEARISH)
        r = btc_regime_long_block(action=action, state=state)
        assert not r.passed

    def test_bearish_allows_shorts(self, action):
        action.side = "short"
        state = PortfolioState(equity=10_000, btc_regime=BtcRegime.BEARISH)
        r = btc_regime_long_block(action=action, state=state)
        assert r.passed

    def test_bullish_allows_longs(self, action):
        state = PortfolioState(equity=10_000, btc_regime=BtcRegime.BULLISH)
        r = btc_regime_long_block(action=action, state=state)
        assert r.passed


class TestFundingRateMinimum:
    def test_non_funding_strategy_passes(self, action, config):
        r = funding_rate_minimum(action=action, config=config, current_funding_hourly=0.0)
        assert r.passed  # momentum strategy, not funding_carry

    def test_funding_below_threshold(self, config):
        action = ProposedAction(
            coin="BTC", side="short", size_usd=200,
            leverage=3, strategy_name="funding_carry",
        )
        r = funding_rate_minimum(action=action, config=config, current_funding_hourly=0.0001)
        assert not r.passed

    def test_funding_above_threshold(self, config):
        action = ProposedAction(
            coin="BTC", side="short", size_usd=200,
            leverage=3, strategy_name="funding_carry",
        )
        r = funding_rate_minimum(action=action, config=config, current_funding_hourly=0.002)
        assert r.passed


class TestMaxLeverage:
    def test_within_limit(self, action, config):
        action.leverage = 3.0
        r = max_leverage_check(action=action, config=config)
        assert r.passed

    def test_exceeds_limit(self, action, config):
        action.leverage = 10.0
        r = max_leverage_check(action=action, config=config)
        assert not r.passed


class TestMaxRiskPerTrade:
    def test_within_limit(self, action, state, config):
        action.size_usd = 150  # 1.5% of 10K
        r = max_risk_per_trade_check(action=action, state=state, config=config)
        assert r.passed

    def test_exceeds_limit(self, action, state, config):
        action.size_usd = 500  # 5% of 10K, max is 2%
        r = max_risk_per_trade_check(action=action, state=state, config=config)
        assert not r.passed


class TestAllowedMarkets:
    def test_allowed(self, action, config):
        action.coin = "BTC"
        r = allowed_markets_check(action=action, config=config)
        assert r.passed

    def test_not_allowed(self, action, config):
        action.coin = "SHIB"
        r = allowed_markets_check(action=action, config=config)
        assert not r.passed


class TestDailyLoss:
    def test_within_limit(self, state, config):
        state.daily_pnl_pct = -0.03
        r = max_daily_loss_check(state=state, config=config)
        assert r.passed

    def test_exceeds_limit(self, state, config):
        state.daily_pnl_pct = -0.06
        r = max_daily_loss_check(state=state, config=config)
        assert not r.passed


class TestMaxDrawdown:
    def test_within_limit(self, state, config):
        state.equity = 9_000  # 10% DD
        state.peak_equity = 10_000
        r = max_drawdown_check(state=state, config=config)
        assert r.passed

    def test_exceeds_hard_limit(self, state, config):
        state.equity = 8_400  # 16% DD
        state.peak_equity = 10_000
        r = max_drawdown_check(state=state, config=config)
        assert not r.passed


class TestMaxConcurrentPositions:
    def test_within_limit(self, state, config):
        state.num_positions = 2
        r = max_concurrent_positions_check(state=state, config=config)
        assert r.passed

    def test_at_limit(self, state, config):
        state.num_positions = 3
        r = max_concurrent_positions_check(state=state, config=config)
        assert not r.passed


class TestDataFreshness:
    def test_all_fresh(self, freshness):
        r = data_freshness_check(freshness=freshness)
        assert r.passed

    def test_stale_data(self):
        f = FreshnessTracker()  # nothing recorded
        r = data_freshness_check(freshness=f)
        assert not r.passed


class TestValidateAll:
    def test_all_pass(self, action, state, config, freshness):
        action.coin = "ETH"
        action.size_usd = 150
        action.leverage = 3.0
        allowed, violations = validate_all(action, state, config, freshness)
        assert allowed
        assert len(violations) == 0

    def test_multiple_violations(self, config, freshness):
        action = ProposedAction(
            coin="SHIB", side="long", size_usd=5000,
            leverage=10.0, strategy_name="momentum",
        )
        state = PortfolioState(
            equity=10_000, peak_equity=10_000,
            num_positions=3, btc_regime=BtcRegime.BEARISH,
        )
        allowed, violations = validate_all(action, state, config, freshness)
        assert not allowed
        assert len(violations) >= 3  # market, leverage, positions, regime
