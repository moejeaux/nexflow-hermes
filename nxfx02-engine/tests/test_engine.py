"""Tests for NXFX02 Position & Execution Engine."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Wire up shared models
_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_root / "nxfx-shared" / "src"))
sys.path.insert(0, str(_root / "nxfx02-engine" / "src"))
sys.path.insert(0, str(_root / "nxfx05-risk" / "src"))

from enums import ActionMode, MarketRegime
from engine import NXFX02Config, NXFX02Engine, PortfolioState
from nxfx01_payload import (
    NXFX01LaunchPayload,
    LaunchFlagsPayload,
    LaunchScoresPayload,
    LiquiditySnapshotPayload,
    MarketContextPayload,
    MempoolDerivedFlagsPayload,
    MempoolFeaturesPayload,
)
from nxfx05_risk_limits import DrawdownState, NXFX05RiskLimits, RegimeOverride
from supervisor import NXFX05Supervisor, RiskConfig, LivePortfolioMetrics


def _make_limits(**overrides) -> NXFX05RiskLimits:
    defaults = dict(
        timestamp=datetime.now(timezone.utc),
        risk_per_trade_base=0.005,
        max_per_token_exposure=0.03,
        max_concurrent_fast_positions=5,
        max_daily_trades=30,
        allow_new_entries=True,
        drawdown_state=DrawdownState(current_dd_pct=0.0),
        regime_overrides={
            MarketRegime.HOT: RegimeOverride(risk_multiplier=1.0),
            MarketRegime.NORMAL: RegimeOverride(risk_multiplier=0.7),
            MarketRegime.COLD: RegimeOverride(risk_multiplier=0.25, allow_fast=False),
        },
    )
    defaults.update(overrides)
    return NXFX05RiskLimits(**defaults)


def _make_launch(**overrides) -> NXFX01LaunchPayload:
    defaults = dict(
        launch_id="test-launch-001",
        token_address="0x0000000000000000000000000000000000001234",
        chain="base",
        timestamp=datetime.now(timezone.utc),
        scores=LaunchScoresPayload(
            contract_safety=85, deployer_reputation=78, funding_risk=20,
            smart_money_alignment=72, whale_behavior=65, graph_risk=15,
            rug_risk=12, liquidity_quality=80, social_quality=60,
            data_confidence=88, modulated_score=79,
        ),
        flags=LaunchFlagsPayload(action_final=ActionMode.FAST, major_interest=False),
        market_context=MarketContextPayload(regime=MarketRegime.HOT),
        liquidity_snapshot=LiquiditySnapshotPayload(
            lp_usd=50_000, volume_1h_usd=20_000, spread_bp=60,
            max_trade_usd_at_1pct_slippage=10_000,
        ),
    )
    defaults.update(overrides)
    return NXFX01LaunchPayload(**defaults)


# ---- Gate tests ----

def test_reject_wait_mode():
    engine = NXFX02Engine()
    launch = _make_launch(flags=LaunchFlagsPayload(action_final=ActionMode.WAIT))
    plan = engine.evaluate(launch, _make_limits())
    assert not plan.decision.execute
    assert "WAIT" in plan.decision.reason


def test_reject_block_mode():
    engine = NXFX02Engine()
    launch = _make_launch(flags=LaunchFlagsPayload(action_final=ActionMode.BLOCK))
    plan = engine.evaluate(launch, _make_limits())
    assert not plan.decision.execute
    assert "BLOCK" in plan.decision.reason


def test_reject_entries_disabled():
    engine = NXFX02Engine()
    launch = _make_launch()
    plan = engine.evaluate(launch, _make_limits(allow_new_entries=False))
    assert not plan.decision.execute
    assert "disabled" in plan.decision.reason


def test_reject_cold_regime():
    engine = NXFX02Engine()
    launch = _make_launch(
        market_context=MarketContextPayload(regime=MarketRegime.COLD)
    )
    plan = engine.evaluate(launch, _make_limits())
    assert not plan.decision.execute
    assert "COLD" in plan.decision.reason


def test_reject_max_positions():
    engine = NXFX02Engine()
    launch = _make_launch()
    portfolio = PortfolioState(current_fast_positions=5)
    plan = engine.evaluate(launch, _make_limits(), portfolio)
    assert not plan.decision.execute
    assert "concurrent" in plan.decision.reason


def test_reject_already_positioned():
    engine = NXFX02Engine()
    launch = _make_launch()
    portfolio = PortfolioState(
        open_token_addresses={launch.token_address}
    )
    plan = engine.evaluate(launch, _make_limits(), portfolio)
    assert not plan.decision.execute
    assert "positioned" in plan.decision.reason


def test_reject_zero_liquidity():
    engine = NXFX02Engine()
    launch = _make_launch(
        liquidity_snapshot=LiquiditySnapshotPayload(
            max_trade_usd_at_1pct_slippage=0.0
        )
    )
    plan = engine.evaluate(launch, _make_limits())
    assert not plan.decision.execute
    assert "liquidity" in plan.decision.reason


def test_reject_low_data_confidence():
    engine = NXFX02Engine()
    launch = _make_launch(
        scores=LaunchScoresPayload(
            contract_safety=85, deployer_reputation=78, data_confidence=40,
            modulated_score=50,
        )
    )
    plan = engine.evaluate(launch, _make_limits())
    assert not plan.decision.execute
    assert "confidence" in plan.decision.reason


def test_reject_drawdown_hard_limit():
    engine = NXFX02Engine()
    launch = _make_launch()
    limits = _make_limits(
        drawdown_state=DrawdownState(current_dd_pct=0.21, dd_hard_limit=0.20)
    )
    plan = engine.evaluate(launch, limits)
    assert not plan.decision.execute
    assert "hard_limit" in plan.decision.reason


# ---- Sizing tests ----

def test_basic_fast_executes():
    engine = NXFX02Engine(NXFX02Config(equity_usd=10_000))
    launch = _make_launch()
    plan = engine.evaluate(launch, _make_limits())
    assert plan.decision.execute
    assert plan.sizing.target_position_notional_usd > 0
    assert plan.sizing.expected_risk_pct_of_equity > 0


def test_major_interest_increases_size():
    # Use high exposure cap so the bonus isn't masked by the cap
    engine = NXFX02Engine(NXFX02Config(equity_usd=10_000))
    limits = _make_limits(max_per_token_exposure=0.20)

    launch_no_mi = _make_launch(
        liquidity_snapshot=LiquiditySnapshotPayload(
            lp_usd=500_000, volume_1h_usd=200_000, spread_bp=20,
            max_trade_usd_at_1pct_slippage=100_000,
        ),
    )
    launch_mi = _make_launch(
        launch_id="test-mi",
        token_address="0x0000000000000000000000000000000000005678",
        flags=LaunchFlagsPayload(action_final=ActionMode.FAST, major_interest=True),
        liquidity_snapshot=LiquiditySnapshotPayload(
            lp_usd=500_000, volume_1h_usd=200_000, spread_bp=20,
            max_trade_usd_at_1pct_slippage=100_000,
        ),
    )

    plan_no = engine.evaluate(launch_no_mi, limits)
    plan_mi = engine.evaluate(launch_mi, limits)

    assert plan_no.decision.execute
    assert plan_mi.decision.execute
    assert plan_mi.sizing.target_position_notional_usd > plan_no.sizing.target_position_notional_usd


def test_mempool_sell_reduces_size():
    engine = NXFX02Engine(NXFX02Config(equity_usd=10_000))
    limits = _make_limits()

    launch_clean = _make_launch()
    launch_sell = _make_launch(
        launch_id="test-sell",
        token_address="0x0000000000000000000000000000000000009999",
        mempool_features=MempoolFeaturesPayload(
            derived_flags=MempoolDerivedFlagsPayload(
                has_strong_pending_smart_sell=True
            )
        ),
    )

    plan_clean = engine.evaluate(launch_clean, limits)
    plan_sell = engine.evaluate(launch_sell, limits)

    assert plan_clean.decision.execute
    assert plan_sell.decision.execute
    assert plan_sell.sizing.target_position_notional_usd < plan_clean.sizing.target_position_notional_usd


def test_regime_normal_smaller_than_hot():
    # Use high exposure cap and deep liquidity so caps don't mask regime diff
    engine = NXFX02Engine(NXFX02Config(equity_usd=10_000))
    limits = _make_limits(max_per_token_exposure=0.20)
    deep_liq = LiquiditySnapshotPayload(
        lp_usd=500_000, volume_1h_usd=200_000, spread_bp=20,
        max_trade_usd_at_1pct_slippage=100_000,
    )

    launch_hot = _make_launch(liquidity_snapshot=deep_liq)
    launch_normal = _make_launch(
        launch_id="test-normal",
        token_address="0x0000000000000000000000000000000000004444",
        market_context=MarketContextPayload(regime=MarketRegime.NORMAL),
        liquidity_snapshot=deep_liq,
    )

    plan_hot = engine.evaluate(launch_hot, limits)
    plan_normal = engine.evaluate(launch_normal, limits)

    assert plan_hot.decision.execute
    assert plan_normal.decision.execute
    assert plan_normal.sizing.target_position_notional_usd < plan_hot.sizing.target_position_notional_usd


def test_drawdown_throttles_size():
    engine = NXFX02Engine(NXFX02Config(equity_usd=10_000))

    launch = _make_launch()

    limits_clean = _make_limits(drawdown_state=DrawdownState(current_dd_pct=0.0))
    limits_dd = _make_limits(drawdown_state=DrawdownState(current_dd_pct=0.05))

    plan_clean = engine.evaluate(launch, limits_clean)
    plan_dd = engine.evaluate(launch, limits_dd)

    assert plan_clean.decision.execute
    assert plan_dd.decision.execute
    assert plan_dd.sizing.target_position_notional_usd < plan_clean.sizing.target_position_notional_usd


def test_liquidity_cap_respected():
    engine = NXFX02Engine(NXFX02Config(
        equity_usd=100_000,
        liquidity_cap_fraction=0.20,
    ))
    launch = _make_launch(
        liquidity_snapshot=LiquiditySnapshotPayload(
            max_trade_usd_at_1pct_slippage=500.0,
            lp_usd=10_000, volume_1h_usd=5_000, spread_bp=50,
        )
    )
    plan = engine.evaluate(launch, _make_limits())
    assert plan.decision.execute
    # Should be capped at 20% of $500 = $100
    assert plan.sizing.target_position_notional_usd <= 100.01


def test_slicing_for_large_orders():
    engine = NXFX02Engine(NXFX02Config(
        equity_usd=100_000,
        liquidity_slice_threshold_usd=1_000,
    ))
    launch = _make_launch(
        liquidity_snapshot=LiquiditySnapshotPayload(
            max_trade_usd_at_1pct_slippage=50_000,
            lp_usd=200_000, volume_1h_usd=100_000, spread_bp=20,
        )
    )
    plan = engine.evaluate(launch, _make_limits())
    assert plan.decision.execute
    if plan.sizing.target_position_notional_usd > 1_000:
        assert plan.execution.slice_count > 1
        assert plan.execution.entry_style.value == "sliced"


# ---- NXFX05 Supervisor tests ----

def test_supervisor_normal_limits():
    sup = NXFX05Supervisor()
    sup.update_metrics(LivePortfolioMetrics(equity_usd=10_000, peak_equity_usd=10_000))
    limits = sup.produce_limits()
    assert limits.allow_new_entries
    assert limits.drawdown_state.current_dd_pct == 0.0


def test_supervisor_halt():
    sup = NXFX05Supervisor()
    sup.halt_trading()
    limits = sup.produce_limits()
    assert not limits.allow_new_entries
    sup.resume_trading()
    limits = sup.produce_limits()
    assert limits.allow_new_entries


def test_supervisor_hard_limit_blocks():
    sup = NXFX05Supervisor(RiskConfig(dd_hard_limit=0.20))
    sup.update_metrics(LivePortfolioMetrics(
        equity_usd=7_500, peak_equity_usd=10_000  # 25% DD
    ))
    limits = sup.produce_limits()
    assert not limits.allow_new_entries


def test_supervisor_throttles_daily_cap():
    sup = NXFX05Supervisor(RiskConfig(
        dd_throttle_start=0.05,
        dd_soft_limit=0.10,
        max_daily_trades=30,
        dd_throttle_trade_cap_min=10,
    ))
    sup.update_metrics(LivePortfolioMetrics(
        equity_usd=9_200, peak_equity_usd=10_000  # 8% DD
    ))
    limits = sup.produce_limits()
    assert limits.max_daily_trades < 30
    assert limits.max_daily_trades >= 10


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
