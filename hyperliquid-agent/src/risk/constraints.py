"""Hard constraints — non-negotiable rules that override everything.

Each constraint is a pure function returning (passed, violation_message).
ANY violation blocks the trade.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

from pydantic import BaseModel

from src.config import StrategyConfig
from src.market.freshness import FreshnessTracker
from src.strategy.regime import BtcRegime

logger = logging.getLogger(__name__)


class ProposedAction(BaseModel):
    """What the executor wants to do — validated against constraints before execution."""

    coin: str
    side: Literal["long", "short"]
    size_usd: float
    leverage: float
    strategy_name: str


class PortfolioState(BaseModel):
    """Current portfolio state for constraint checking."""

    equity: float
    peak_equity: float = 0.0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    num_positions: int = 0
    btc_regime: BtcRegime = BtcRegime.NEUTRAL


class ConstraintResult(BaseModel):
    passed: bool
    violation: str | None = None


# ── individual constraint functions ──────────────────────────────────────────

def kill_switch_check(**_) -> ConstraintResult:
    """HL_KILL_SWITCH env var — instant halt."""
    if os.getenv("HL_KILL_SWITCH", "false").lower() in ("true", "1", "yes"):
        return ConstraintResult(passed=False, violation="Kill switch is active")
    return ConstraintResult(passed=True)


def btc_regime_long_block(
    action: ProposedAction, state: PortfolioState, **_
) -> ConstraintResult:
    """Bearish BTC regime blocks all long mean-reversion entries."""
    if state.btc_regime == BtcRegime.BEARISH and action.side == "long":
        return ConstraintResult(
            passed=False,
            violation=f"BTC regime is BEARISH — long entries blocked (strategy={action.strategy_name})",
        )
    return ConstraintResult(passed=True)


def funding_rate_minimum(
    action: ProposedAction,
    config: StrategyConfig,
    current_funding_hourly: float | None = None,
    **_,
) -> ConstraintResult:
    """Funding carry requires rate above minimum threshold."""
    if action.strategy_name != "funding_carry":
        return ConstraintResult(passed=True)

    threshold = config.funding_carry.min_funding_rate_hourly
    if current_funding_hourly is None:
        return ConstraintResult(
            passed=False, violation="No funding rate data available for funding carry"
        )
    if abs(current_funding_hourly) < threshold:
        return ConstraintResult(
            passed=False,
            violation=(
                f"Funding rate {current_funding_hourly:.6f}/hr below threshold "
                f"{threshold:.6f}/hr — funding carry blocked"
            ),
        )
    return ConstraintResult(passed=True)


def smart_money_freshness(
    action: ProposedAction,
    freshness: FreshnessTracker,
    config: StrategyConfig,
    **_,
) -> ConstraintResult:
    """Stale smart money data must be ignored entirely."""
    if action.strategy_name != "smart_money":
        return ConstraintResult(passed=True)

    max_age = config.smart_money.max_freshness_minutes * 60
    if not freshness.is_fresh("smart_money", max_age):
        return ConstraintResult(
            passed=False,
            violation=f"Smart money data stale (max {config.smart_money.max_freshness_minutes}min)",
        )
    return ConstraintResult(passed=True)


def max_leverage_check(action: ProposedAction, config: StrategyConfig, **_) -> ConstraintResult:
    """Never exceed per-asset max leverage."""
    if action.leverage > config.risk.max_leverage_per_asset:
        return ConstraintResult(
            passed=False,
            violation=(
                f"Leverage {action.leverage}x exceeds max "
                f"{config.risk.max_leverage_per_asset}x for {action.coin}"
            ),
        )
    return ConstraintResult(passed=True)


def max_risk_per_trade_check(
    action: ProposedAction, state: PortfolioState, config: StrategyConfig, **_
) -> ConstraintResult:
    """Never exceed % equity per trade."""
    if state.equity <= 0:
        return ConstraintResult(passed=False, violation="Equity is zero or negative")

    risk_pct = action.size_usd / state.equity
    if risk_pct > config.risk.risk_per_trade_pct:
        return ConstraintResult(
            passed=False,
            violation=(
                f"Trade size ${action.size_usd:.2f} is {risk_pct:.1%} of equity "
                f"(max {config.risk.risk_per_trade_pct:.1%})"
            ),
        )
    return ConstraintResult(passed=True)


def max_daily_loss_check(state: PortfolioState, config: StrategyConfig, **_) -> ConstraintResult:
    """Stop trading if daily loss exceeded."""
    if state.daily_pnl_pct < -config.risk.max_daily_loss_pct:
        return ConstraintResult(
            passed=False,
            violation=(
                f"Daily loss {state.daily_pnl_pct:.1%} exceeds max "
                f"{config.risk.max_daily_loss_pct:.1%} — trading halted for the day"
            ),
        )
    return ConstraintResult(passed=True)


def allowed_markets_check(action: ProposedAction, config: StrategyConfig, **_) -> ConstraintResult:
    """Never trade markets not explicitly allowed."""
    if action.coin not in config.allowed_markets.all:
        return ConstraintResult(
            passed=False,
            violation=f"{action.coin} is not in allowed markets: {config.allowed_markets.all}",
        )
    return ConstraintResult(passed=True)


def data_freshness_check(freshness: FreshnessTracker, **_) -> ConstraintResult:
    """Block trade if any required core data is stale (>60s for prices, >300s for candles)."""
    required = [
        ("prices", 60),
        ("funding", 120),
        ("account_state", 60),
    ]
    all_fresh, stale = freshness.check_all_required(required)
    if not all_fresh:
        return ConstraintResult(
            passed=False,
            violation=f"Required data is stale: {', '.join(stale)}",
        )
    return ConstraintResult(passed=True)


def max_concurrent_positions_check(
    state: PortfolioState, config: StrategyConfig, **_
) -> ConstraintResult:
    """Don't exceed max concurrent positions."""
    if state.num_positions >= config.risk.max_concurrent_positions:
        return ConstraintResult(
            passed=False,
            violation=(
                f"{state.num_positions} positions open "
                f"(max {config.risk.max_concurrent_positions})"
            ),
        )
    return ConstraintResult(passed=True)


def max_drawdown_check(state: PortfolioState, config: StrategyConfig, **_) -> ConstraintResult:
    """Hard drawdown kill switch."""
    if state.peak_equity <= 0:
        return ConstraintResult(passed=True)

    dd_pct = (state.peak_equity - state.equity) / state.peak_equity
    if dd_pct >= config.risk.max_drawdown_hard_pct:
        return ConstraintResult(
            passed=False,
            violation=(
                f"Drawdown {dd_pct:.1%} exceeds hard limit "
                f"{config.risk.max_drawdown_hard_pct:.1%} — all trading halted"
            ),
        )
    return ConstraintResult(passed=True)


# ── constraint runner ────────────────────────────────────────────────────────

ALL_CONSTRAINTS = [
    kill_switch_check,
    btc_regime_long_block,
    funding_rate_minimum,
    smart_money_freshness,
    max_leverage_check,
    max_risk_per_trade_check,
    max_daily_loss_check,
    allowed_markets_check,
    data_freshness_check,
    max_concurrent_positions_check,
    max_drawdown_check,
]


def validate_all(
    action: ProposedAction,
    state: PortfolioState,
    config: StrategyConfig,
    freshness: FreshnessTracker,
    current_funding_hourly: float | None = None,
) -> tuple[bool, list[str]]:
    """Run all hard constraints. Returns (allowed, list_of_violations).

    ANY single violation = trade blocked.
    """
    kwargs = dict(
        action=action,
        state=state,
        config=config,
        freshness=freshness,
        current_funding_hourly=current_funding_hourly,
    )

    violations: list[str] = []
    for constraint_fn in ALL_CONSTRAINTS:
        result = constraint_fn(**kwargs)
        if not result.passed and result.violation:
            violations.append(result.violation)
            logger.warning("Constraint failed: %s", result.violation)

    return len(violations) == 0, violations
