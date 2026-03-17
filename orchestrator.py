"""NXFX Multi-Agent Orchestrator.

Demonstrates the interaction loop between:
  NXFX01 (Launch Intelligence) → scoring payloads
  NXFX03 (Mempool Intelligence) → feature snapshots
  NXFX05 (Risk Supervisor)     → risk limits
  NXFX02 (Execution Engine)    → sized execution plans

This orchestrator can run with mock data for validation or be wired to
real NXFX01 API outputs in production.

Flow:
  1. NXFX05 produces current risk limits.
  2. NXFX01 surfaces actionable launches (FAST candidates).
  3. For each launch, NXFX03 provides mempool features (already embedded).
  4. NXFX02 evaluates: gate checks → sizing → execution plan.
  5. Orchestrator logs the plan (or hands to execution worker).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Wire up shared models
_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_root / "nxfx-shared" / "src"))
sys.path.insert(0, str(_root / "nxfx02-engine" / "src"))
sys.path.insert(0, str(_root / "nxfx03-mempool" / "src"))
sys.path.insert(0, str(_root / "nxfx05-risk" / "src"))

from enums import ActionMode, MarketRegime
from nxfx01_payload import (
    NXFX01LaunchPayload,
    LaunchFlagsPayload,
    LaunchScoresPayload,
    LiquiditySnapshotPayload,
    MarketContextPayload,
    MempoolAnomaliesPayload,
    MempoolDerivedFlagsPayload,
    MempoolFeaturesPayload,
    MempoolSmartFlowPayload,
)
from nxfx02_execution_plan import NXFX02ExecutionPlan
from engine import NXFX02Engine, NXFX02Config, PortfolioState
from aggregator import MempoolFeatureAggregator, MempoolConfig, PendingSwap
from supervisor import NXFX05Supervisor, RiskConfig, LivePortfolioMetrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("orchestrator")


# =====================================================================
# Mock data generators (replace with real NXFX01 API calls in production)
# =====================================================================


def mock_fast_launch() -> NXFX01LaunchPayload:
    """A high-quality FAST launch with major interest and clean mempool."""
    return NXFX01LaunchPayload(
        launch_id="launch-001-fast-clean",
        token_address="0xABCD1234567890abcdef1234567890abcdef0001",
        chain="base",
        timestamp=datetime.now(timezone.utc),
        scores=LaunchScoresPayload(
            contract_safety=85,
            deployer_reputation=78,
            funding_risk=20,
            smart_money_alignment=72,
            whale_behavior=65,
            graph_risk=15,
            rug_risk=12,
            liquidity_quality=80,
            social_quality=60,
            data_confidence=88,
            modulated_score=79,
        ),
        flags=LaunchFlagsPayload(
            action_final=ActionMode.FAST,
            major_interest=True,
            is_cex_funded=False,
        ),
        market_context=MarketContextPayload(
            regime=MarketRegime.HOT,
            cycle_score=72.0,
        ),
        liquidity_snapshot=LiquiditySnapshotPayload(
            lp_usd=85_000.0,
            volume_1h_usd=32_000.0,
            spread_bp=45.0,
            max_trade_usd_at_1pct_slippage=12_000.0,
        ),
        mempool_features=MempoolFeaturesPayload(
            mempool_smart_flow=MempoolSmartFlowPayload(
                pending_smart_buy_volume=1_200.0,
                pending_smart_sell_volume=50.0,
                pending_smart_buy_ratio=0.65,
                pending_smart_sell_ratio=0.03,
                pending_smart_buy_count=4,
                pending_smart_sell_count=1,
                pending_smart_buy_fee_urgency_max=3.2,
                pending_smart_sell_fee_urgency_max=1.1,
            ),
            mempool_anomalies=MempoolAnomaliesPayload(
                tiny_swap_count=2,
                total_pending_swap_count=18,
                tiny_swap_density=0.11,
                new_addr_tiny_swap_count=0,
            ),
            derived_flags=MempoolDerivedFlagsPayload(
                has_strong_pending_smart_buy=True,
                has_strong_pending_smart_sell=False,
                high_tiny_swap_density=False,
            ),
        ),
    )


def mock_fast_with_mempool_sell() -> NXFX01LaunchPayload:
    """A FAST launch that has strong pending smart-money sells in mempool."""
    return NXFX01LaunchPayload(
        launch_id="launch-002-fast-sell-pressure",
        token_address="0xABCD1234567890abcdef1234567890abcdef0002",
        chain="base",
        timestamp=datetime.now(timezone.utc),
        scores=LaunchScoresPayload(
            contract_safety=82,
            deployer_reputation=75,
            funding_risk=22,
            smart_money_alignment=68,
            whale_behavior=58,
            graph_risk=20,
            rug_risk=18,
            liquidity_quality=74,
            social_quality=55,
            data_confidence=82,
            modulated_score=73,
        ),
        flags=LaunchFlagsPayload(
            action_final=ActionMode.FAST,
            major_interest=False,
            is_cex_funded=False,
        ),
        market_context=MarketContextPayload(
            regime=MarketRegime.NORMAL,
            cycle_score=50.0,
        ),
        liquidity_snapshot=LiquiditySnapshotPayload(
            lp_usd=42_000.0,
            volume_1h_usd=15_000.0,
            spread_bp=85.0,
            max_trade_usd_at_1pct_slippage=6_500.0,
        ),
        mempool_features=MempoolFeaturesPayload(
            mempool_smart_flow=MempoolSmartFlowPayload(
                pending_smart_buy_volume=200.0,
                pending_smart_sell_volume=2_800.0,
                pending_smart_buy_ratio=0.08,
                pending_smart_sell_ratio=0.72,
                pending_smart_buy_count=1,
                pending_smart_sell_count=6,
                pending_smart_buy_fee_urgency_max=1.5,
                pending_smart_sell_fee_urgency_max=5.8,
            ),
            mempool_anomalies=MempoolAnomaliesPayload(
                tiny_swap_count=8,
                total_pending_swap_count=22,
                tiny_swap_density=0.36,
                new_addr_tiny_swap_count=3,
            ),
            derived_flags=MempoolDerivedFlagsPayload(
                has_strong_pending_smart_buy=False,
                has_strong_pending_smart_sell=True,
                high_tiny_swap_density=False,
            ),
        ),
    )


def mock_wait_launch() -> NXFX01LaunchPayload:
    """A WAIT-mode launch — should be rejected by NXFX02."""
    return NXFX01LaunchPayload(
        launch_id="launch-003-wait",
        token_address="0xABCD1234567890abcdef1234567890abcdef0003",
        chain="base",
        timestamp=datetime.now(timezone.utc),
        scores=LaunchScoresPayload(
            contract_safety=65,
            deployer_reputation=50,
            funding_risk=40,
            smart_money_alignment=35,
            whale_behavior=30,
            graph_risk=45,
            rug_risk=42,
            liquidity_quality=55,
            social_quality=40,
            data_confidence=60,
            modulated_score=48,
        ),
        flags=LaunchFlagsPayload(
            action_final=ActionMode.WAIT,
            major_interest=False,
            is_cex_funded=True,
        ),
        market_context=MarketContextPayload(
            regime=MarketRegime.NORMAL,
            cycle_score=50.0,
        ),
        liquidity_snapshot=LiquiditySnapshotPayload(
            lp_usd=8_000.0,
            volume_1h_usd=2_500.0,
            spread_bp=180.0,
            max_trade_usd_at_1pct_slippage=1_800.0,
        ),
    )


def mock_cold_regime_launch() -> NXFX01LaunchPayload:
    """A FAST launch in a COLD regime — should be blocked by regime override."""
    return NXFX01LaunchPayload(
        launch_id="launch-004-cold-regime",
        token_address="0xABCD1234567890abcdef1234567890abcdef0004",
        chain="base",
        timestamp=datetime.now(timezone.utc),
        scores=LaunchScoresPayload(
            contract_safety=90,
            deployer_reputation=85,
            funding_risk=10,
            smart_money_alignment=80,
            whale_behavior=72,
            graph_risk=8,
            rug_risk=6,
            liquidity_quality=88,
            social_quality=70,
            data_confidence=92,
            modulated_score=86,
        ),
        flags=LaunchFlagsPayload(
            action_final=ActionMode.FAST,
            major_interest=True,
            is_cex_funded=False,
        ),
        market_context=MarketContextPayload(
            regime=MarketRegime.COLD,
            cycle_score=20.0,
        ),
        liquidity_snapshot=LiquiditySnapshotPayload(
            lp_usd=120_000.0,
            volume_1h_usd=45_000.0,
            spread_bp=30.0,
            max_trade_usd_at_1pct_slippage=25_000.0,
        ),
    )


def mock_low_liquidity_launch() -> NXFX01LaunchPayload:
    """A FAST launch with zero executable liquidity."""
    return NXFX01LaunchPayload(
        launch_id="launch-005-no-liq",
        token_address="0xABCD1234567890abcdef1234567890abcdef0005",
        chain="base",
        timestamp=datetime.now(timezone.utc),
        scores=LaunchScoresPayload(
            contract_safety=80,
            deployer_reputation=70,
            funding_risk=25,
            smart_money_alignment=65,
            whale_behavior=60,
            graph_risk=18,
            rug_risk=15,
            liquidity_quality=70,
            social_quality=50,
            data_confidence=75,
            modulated_score=72,
        ),
        flags=LaunchFlagsPayload(
            action_final=ActionMode.FAST,
            major_interest=False,
        ),
        market_context=MarketContextPayload(
            regime=MarketRegime.HOT,
            cycle_score=68.0,
        ),
        liquidity_snapshot=LiquiditySnapshotPayload(
            lp_usd=1_000.0,
            volume_1h_usd=200.0,
            spread_bp=800.0,
            max_trade_usd_at_1pct_slippage=0.0,
        ),
    )


# =====================================================================
# Orchestrator
# =====================================================================


def format_plan(plan: NXFX02ExecutionPlan) -> str:
    """Pretty-print an execution plan."""
    return plan.model_dump_json(indent=2)


def run_orchestrator() -> None:
    """Main orchestrator loop — processes mock launches through the pipeline."""

    logger.info("=" * 70)
    logger.info("NXFX Multi-Agent Orchestrator — Starting")
    logger.info("=" * 70)

    # ---- Initialize agents ----

    # NXFX05: Risk Supervisor
    risk_supervisor = NXFX05Supervisor(RiskConfig(
        risk_per_trade_base=0.005,
        max_per_token_exposure=0.03,
        max_concurrent_fast_positions=5,
        max_daily_trades=30,
        dd_soft_limit=0.10,
        dd_hard_limit=0.20,
    ))
    risk_supervisor.update_metrics(LivePortfolioMetrics(
        equity_usd=10_000.0,
        peak_equity_usd=10_500.0,  # slight drawdown: ~4.8%
        current_fast_positions=1,
        trades_today=3,
        current_regime=MarketRegime.NORMAL,
    ))

    # NXFX02: Execution Engine
    execution_engine = NXFX02Engine(NXFX02Config(
        equity_usd=10_000.0,
        default_stop_distance_pct=0.15,
        quality_multiplier_max=2.0,
        major_interest_bonus=1.5,
        liquidity_cap_fraction=0.20,
        liquidity_slice_threshold_usd=2_000.0,
        mempool_sell_penalty=0.50,
        min_trade_usd=25.0,
    ))

    # NXFX03: Mempool Aggregator (features already embedded in mock payloads,
    # but we demonstrate the aggregator producing a snapshot too)
    mempool_agg = MempoolFeatureAggregator(MempoolConfig(
        window_seconds=10,
        tiny_swap_threshold_usd=50.0,
        min_sm_pending_buy_usd=500.0,
        high_tiny_swap_density=0.50,
    ))

    # Portfolio state tracking
    portfolio = PortfolioState(
        current_fast_positions=1,
        trades_today=3,
        open_token_addresses=set(),
    )

    # ---- Build launch queue ----

    launches = [
        ("FAST + major interest (clean)", mock_fast_launch()),
        ("FAST + SM sell pressure", mock_fast_with_mempool_sell()),
        ("WAIT mode (should reject)", mock_wait_launch()),
        ("FAST in COLD regime (should reject)", mock_cold_regime_launch()),
        ("FAST but no liquidity (should reject)", mock_low_liquidity_launch()),
    ]

    # ---- Process each launch ----

    results: list[NXFX02ExecutionPlan] = []

    for label, launch in launches:
        logger.info("-" * 60)
        logger.info("Evaluating: %s", label)
        logger.info(
            "  launch_id=%s  action=%s  score=%d  regime=%s  major_interest=%s",
            launch.launch_id,
            launch.flags.action_final.value,
            launch.scores.modulated_score,
            launch.market_context.regime.value,
            launch.flags.major_interest,
        )

        # Step 1: Get risk limits from NXFX05
        risk_limits = risk_supervisor.produce_limits()

        # Step 2: NXFX02 evaluates
        plan = execution_engine.evaluate(launch, risk_limits, portfolio)
        results.append(plan)

        # Step 3: Log result
        if plan.decision.execute:
            logger.info(
                "  -> EXECUTE: $%.2f notional  (risk %.4f%% equity)  style=%s  slices=%d",
                plan.sizing.target_position_notional_usd,
                plan.sizing.expected_risk_pct_of_equity * 100,
                plan.execution.entry_style.value,
                plan.execution.slice_count,
            )
            logger.info("     reason: %s", plan.decision.reason)

            # Update portfolio state
            portfolio.current_fast_positions += 1
            portfolio.trades_today += 1
            portfolio.open_token_addresses.add(launch.token_address)
        else:
            logger.info("  -> SKIP: %s", plan.decision.reason)

    # ---- Summary ----

    logger.info("=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)

    executed = [r for r in results if r.decision.execute]
    skipped = [r for r in results if not r.decision.execute]

    logger.info("Executed: %d", len(executed))
    for p in executed:
        logger.info(
            "  %s  $%.2f  (%s)",
            p.launch_id,
            p.sizing.target_position_notional_usd,
            p.decision.reason,
        )

    logger.info("Skipped: %d", len(skipped))
    for p in skipped:
        logger.info("  %s  (%s)", p.launch_id, p.decision.reason)

    total_notional = sum(p.sizing.target_position_notional_usd for p in executed)
    total_risk = sum(p.sizing.expected_risk_pct_of_equity for p in executed)
    logger.info(
        "Total notional: $%.2f  Total risk: %.4f%% equity",
        total_notional,
        total_risk * 100,
    )

    # ---- Demonstrate NXFX03 standalone snapshot ----

    logger.info("")
    logger.info("=" * 70)
    logger.info("NXFX03 Mempool Aggregator Demo")
    logger.info("=" * 70)

    demo_token = "0xDEMO0000000000000000000000000000000000FF"
    mempool_agg.register_token(demo_token)

    # Simulate some pending swaps arriving
    import time as _time
    _now = _time.time()

    for i, (direction, usd, tier, fee) in enumerate([
        ("buy", 800.0, "TIER_2_SMART_MONEY", 3.5),
        ("buy", 1200.0, "TIER_1_WHALE", 4.2),
        ("sell", 150.0, "TIER_2_SMART_MONEY", 2.1),
        ("buy", 30.0, "TIER_3_RETAIL", 1.0),   # tiny
        ("buy", 20.0, "UNKNOWN", 0.8),          # tiny + new address
        ("sell", 45.0, "TIER_3_RETAIL", 1.2),   # tiny
        ("buy", 600.0, "TIER_2_SMART_MONEY", 3.8),
    ]):
        mempool_agg.add_pending_swap(PendingSwap(
            tx_hash=f"0xfake{i:04d}",
            sender=f"0xsender{i:04d}",
            token_address=demo_token,
            direction=direction,
            amount_usd=usd,
            priority_fee_gwei=fee,
            sender_tier=tier,
            is_new_address=(i == 4),
            timestamp=_now - (6 - i),  # spread over last few seconds
        ))

    snapshot = mempool_agg.compute_snapshot(demo_token)
    if snapshot:
        logger.info("NXFX03 snapshot for %s:", demo_token[:10] + "...")
        logger.info("  Smart flow: buy=$%.0f sell=$%.0f  buy_ratio=%.2f",
            snapshot.mempool_smart_flow.pending_smart_buy_volume,
            snapshot.mempool_smart_flow.pending_smart_sell_volume,
            snapshot.mempool_smart_flow.pending_smart_buy_ratio,
        )
        logger.info("  Anomalies: tiny=%d/%d  density=%.2f  new_addr_tiny=%d",
            snapshot.mempool_anomalies.tiny_swap_count,
            snapshot.mempool_anomalies.total_pending_swap_count,
            snapshot.mempool_anomalies.tiny_swap_density,
            snapshot.mempool_anomalies.new_addr_tiny_swap_count,
        )
        logger.info("  Flags: strong_buy=%s  strong_sell=%s  high_density=%s",
            snapshot.derived_flags.has_strong_pending_smart_buy,
            snapshot.derived_flags.has_strong_pending_smart_sell,
            snapshot.derived_flags.high_tiny_swap_density,
        )

    # ---- Dump full JSON plans ----

    logger.info("")
    logger.info("=" * 70)
    logger.info("Full Execution Plans (JSON)")
    logger.info("=" * 70)
    for plan in results:
        print(format_plan(plan))
        print()


if __name__ == "__main__":
    run_orchestrator()
