"""NXFX02 Position & Execution Engine.

Takes NXFX01 launch intelligence + NXFX05 risk limits and produces
execution plans. Owns sizing logic — never scores or classifies.

Sizing rule summary:
  1. Gate check: action must be FAST, risk limits must allow entry.
  2. Base notional = equity × risk_per_trade_base.
  3. Regime multiplier applied from NXFX05 regime_overrides.
  4. Quality multiplier: higher modulated_score → more aggressive (1.0–2.0×).
  5. Major interest bonus: +50% if major_interest flag is set.
  6. Liquidity cap: never exceed 20% of max_trade_usd_at_1pct_slippage.
  7. Exposure cap: never exceed max_per_token_exposure × equity.
  8. Drawdown throttle: scale down linearly as drawdown approaches soft limit.
  9. Mempool penalty: if strong pending smart sell, cut size by 50%.
  10. Slicing: orders > liquidity_slice_threshold are split into slices.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field

# Allow running from workspace root by adding nxfx-shared/src to path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "nxfx-shared" / "src"))

from nxfx01_payload import NXFX01LaunchPayload
from nxfx02_execution_plan import (
    NXFX02ExecutionPlan,
    ExecutionDecision,
    ExecutionParams,
    SizingPlan,
)
from nxfx05_risk_limits import NXFX05RiskLimits
from enums import ActionMode, EntryStyle, MarketRegime, OrderType, TimeInForce

logger = logging.getLogger("nxfx02")


# ---- Configuration ----

@dataclass
class NXFX02Config:
    """Tunable parameters for the sizing engine."""

    # Assumed equity for sizing (will be fed from portfolio state in production)
    equity_usd: float = 10_000.0

    # Default stop distance used to convert risk_pct → notional
    # (e.g., 0.15 = we expect a max 15% adverse move before stop)
    default_stop_distance_pct: float = 0.15

    # Quality multiplier range: modulated_score maps [50..100] → [1.0..max]
    quality_multiplier_max: float = 2.0
    quality_score_floor: int = 50  # below this, multiplier = 1.0

    # Major interest bonus multiplier
    major_interest_bonus: float = 1.5

    # Max fraction of available 1%-slippage liquidity we'll take
    liquidity_cap_fraction: float = 0.20

    # If order exceeds this USD value, slice it
    liquidity_slice_threshold_usd: float = 2_000.0
    max_slices: int = 5

    # Mempool sell penalty: applied when strong pending smart sell
    mempool_sell_penalty: float = 0.50

    # Default slippage tolerance
    default_max_slippage_pct: float = 1.0

    # Minimum size to bother trading
    min_trade_usd: float = 25.0


# ---- State tracking ----

@dataclass
class PortfolioState:
    """Lightweight in-memory portfolio state for NXFX02.

    In production this would be populated from DB / execution receipts.
    """
    current_fast_positions: int = 0
    trades_today: int = 0
    open_token_addresses: set[str] = field(default_factory=set)


# ---- Engine ----

class NXFX02Engine:
    """Position & Execution Engine.

    Stateless per call — all state is passed in via arguments.
    """

    def __init__(self, config: NXFX02Config | None = None) -> None:
        self.config = config or NXFX02Config()

    def evaluate(
        self,
        launch: NXFX01LaunchPayload,
        risk_limits: NXFX05RiskLimits,
        portfolio: PortfolioState | None = None,
    ) -> NXFX02ExecutionPlan:
        """Produce an execution plan for a launch given current risk limits.

        Returns a plan with decision.execute=False and an explanation if
        any gate check fails. Otherwise returns a sized execution plan.
        """
        portfolio = portfolio or PortfolioState()
        cfg = self.config

        # ---- Gate checks (any failure → no-execute) ----

        reject_reason = self._check_gates(launch, risk_limits, portfolio)
        if reject_reason:
            return self._no_execute_plan(launch, reject_reason)

        # ---- Sizing pipeline ----

        regime = launch.market_context.regime
        regime_override = risk_limits.regime_overrides.get(regime)
        regime_mult = regime_override.risk_multiplier if regime_override else 0.7

        # Step 1: Base notional from risk budget
        risk_budget_usd = cfg.equity_usd * risk_limits.risk_per_trade_base
        base_notional = risk_budget_usd / cfg.default_stop_distance_pct

        # Step 2: Regime adjustment
        notional = base_notional * regime_mult

        # Step 3: Quality multiplier (modulated_score 50→100 maps to 1.0→max)
        score = launch.scores.modulated_score
        if score >= cfg.quality_score_floor:
            quality_frac = (score - cfg.quality_score_floor) / (
                100 - cfg.quality_score_floor
            )
            quality_mult = 1.0 + quality_frac * (cfg.quality_multiplier_max - 1.0)
        else:
            quality_mult = 1.0
        notional *= quality_mult

        # Step 4: Major interest bonus
        if launch.flags.major_interest:
            notional *= cfg.major_interest_bonus

        # Step 5: Mempool sell penalty
        if launch.mempool_features.derived_flags.has_strong_pending_smart_sell:
            notional *= cfg.mempool_sell_penalty
            logger.info(
                "Mempool sell penalty applied for %s (%.0f%% cut)",
                launch.token_address,
                (1 - cfg.mempool_sell_penalty) * 100,
            )

        # Step 6: Liquidity cap
        liq_cap = (
            launch.liquidity_snapshot.max_trade_usd_at_1pct_slippage
            * cfg.liquidity_cap_fraction
        )
        if liq_cap > 0:
            notional = min(notional, liq_cap)

        # Step 7: Per-token exposure cap
        exposure_cap = cfg.equity_usd * risk_limits.max_per_token_exposure
        notional = min(notional, exposure_cap)

        # Step 8: Drawdown throttle
        dd = risk_limits.drawdown_state
        if dd.current_dd_pct > 0 and dd.dd_soft_limit > 0:
            dd_ratio = dd.current_dd_pct / dd.dd_soft_limit
            if dd_ratio >= 1.0:
                return self._no_execute_plan(
                    launch, "drawdown_at_soft_limit"
                )
            # Linear scale-down: at 0% dd → 1.0×, at soft_limit → 0.0×
            dd_scale = max(0.0, 1.0 - dd_ratio)
            notional *= dd_scale

        # Step 9: Floor check
        if notional < cfg.min_trade_usd:
            return self._no_execute_plan(
                launch,
                f"sized_below_minimum ({notional:.2f} < {cfg.min_trade_usd})",
            )

        notional = round(notional, 2)

        # ---- Execution parameters ----

        # Determine slicing
        if notional > cfg.liquidity_slice_threshold_usd:
            slice_count = min(
                cfg.max_slices,
                max(2, int(notional / cfg.liquidity_slice_threshold_usd) + 1),
            )
            entry_style = EntryStyle.SLICED
        else:
            slice_count = 1
            entry_style = EntryStyle.SINGLE

        # Slippage: tighter for larger orders
        if notional > 5000:
            max_slippage = 0.5
        elif notional > 1000:
            max_slippage = cfg.default_max_slippage_pct
        else:
            max_slippage = 1.5

        risk_pct = notional / cfg.equity_usd if cfg.equity_usd > 0 else 0.0

        reason_parts = [launch.flags.action_final.value]
        if launch.flags.major_interest:
            reason_parts.append("major_interest")
        reason_parts.append(f"regime_{regime.value}")
        reason_parts.append(f"score_{score}")
        reason = "_".join(reason_parts)

        return NXFX02ExecutionPlan(
            launch_id=launch.launch_id,
            token_address=launch.token_address,
            chain=launch.chain,
            decision=ExecutionDecision(execute=True, reason=reason),
            sizing=SizingPlan(
                target_position_notional_usd=notional,
                max_additional_notional_usd=round(
                    min(notional * 0.5, exposure_cap - notional), 2
                ),
                expected_risk_pct_of_equity=round(risk_pct, 6),
            ),
            execution=ExecutionParams(
                entry_style=entry_style,
                slice_count=slice_count,
                max_slippage_pct=max_slippage,
                order_type=OrderType.MARKET,
                time_in_force=TimeInForce.IOC,
            ),
        )

    # ---- Gate checks ----

    def _check_gates(
        self,
        launch: NXFX01LaunchPayload,
        risk_limits: NXFX05RiskLimits,
        portfolio: PortfolioState,
    ) -> str | None:
        """Return a rejection reason string, or None if all gates pass."""

        # G1: Must be FAST
        if launch.flags.action_final != ActionMode.FAST:
            return f"action_is_{launch.flags.action_final.value}"

        # G2: Master kill switch
        if not risk_limits.allow_new_entries:
            return "new_entries_disabled"

        # G3: Regime allows FAST?
        regime = launch.market_context.regime
        override = risk_limits.regime_overrides.get(regime)
        if override and not override.allow_fast:
            return f"regime_{regime.value}_blocks_fast"

        # G4: Concurrent position limit
        if portfolio.current_fast_positions >= risk_limits.max_concurrent_fast_positions:
            return "max_concurrent_positions_reached"

        # G5: Daily trade limit
        if portfolio.trades_today >= risk_limits.max_daily_trades:
            return "max_daily_trades_reached"

        # G6: Already have a position in this token
        if launch.token_address in portfolio.open_token_addresses:
            return "already_positioned"

        # G7: Drawdown hard limit
        dd = risk_limits.drawdown_state
        if dd.current_dd_pct >= dd.dd_hard_limit:
            return "drawdown_hard_limit_breached"

        # G8: Zero liquidity
        if launch.liquidity_snapshot.max_trade_usd_at_1pct_slippage <= 0:
            return "no_executable_liquidity"

        # G9: Data confidence too low for execution
        if launch.scores.data_confidence < 50:
            return f"data_confidence_too_low ({launch.scores.data_confidence})"

        return None

    # ---- Helpers ----

    @staticmethod
    def _no_execute_plan(
        launch: NXFX01LaunchPayload, reason: str
    ) -> NXFX02ExecutionPlan:
        return NXFX02ExecutionPlan(
            launch_id=launch.launch_id,
            token_address=launch.token_address,
            chain=launch.chain,
            decision=ExecutionDecision(execute=False, reason=reason),
        )
