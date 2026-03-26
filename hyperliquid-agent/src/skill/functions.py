"""G.A.M.E. Function definitions — the skill interface for NXFH01.

Each function is exposed to the Virtuals G.A.M.E. agent. The agent
autonomously decides which functions to call based on its goal and state.
Trades are routed through ACP to the Degen Claw agent (ID 8654).
"""

from __future__ import annotations

import json
import logging

from game_sdk.game.custom_types import Argument, Function, FunctionResultStatus

from src.config import StrategyConfig, load_strategy_config
from src.acp.degen_claw import DegenClawAcp
from src.market.data_feed import MarketDataFeed
from src.market.freshness import FreshnessTracker
from src.market.types import FundingRate
from src.execution.executor import OrderExecutor
from src.risk.supervisor import RiskSupervisor
from src.state.portfolio import PortfolioTracker
from src.strategy.base import MarketSnapshot, StrategySignal
from src.strategy.funding_carry import FundingCarryStrategy
from src.strategy.momentum import MomentumStrategy
from src.strategy.regime import BtcRegime, detect_regime
from src.strategy.rwa import RwaStrategy
from src.strategy.smart_money import SmartMoneyConfirmation

logger = logging.getLogger(__name__)


class SkillContext:
    """Shared state across all skill functions. Initialized once at startup."""

    def __init__(
        self,
        feed: MarketDataFeed,
        config: StrategyConfig,
        freshness: FreshnessTracker,
        risk_supervisor: RiskSupervisor,
        executor: OrderExecutor,
        acp: DegenClawAcp,
        smart_money: SmartMoneyConfirmation,
        portfolio: PortfolioTracker,
    ):
        self.feed = feed
        self.config = config
        self.freshness = freshness
        self.risk = risk_supervisor
        self.executor = executor
        self.acp = acp
        self.smart_money = smart_money
        self.portfolio = portfolio

        self.strategies = [
            FundingCarryStrategy(),
            MomentumStrategy(),
            RwaStrategy(),
        ]

        self._last_regime = BtcRegime.NEUTRAL


# Module-level context — set by main.py at startup
_ctx: SkillContext | None = None


def set_context(ctx: SkillContext) -> None:
    global _ctx
    _ctx = ctx


def _get_ctx() -> SkillContext:
    if _ctx is None:
        raise RuntimeError("SkillContext not initialized — call set_context() first")
    return _ctx


# ── skill function implementations ──────────────────────────────────────────

def _get_account_info(_: str, **__) -> tuple[FunctionResultStatus, str, dict]:
    """Get HL account balance, equity, margin, open positions."""
    ctx = _get_ctx()
    try:
        state = ctx.feed.get_account_state()
        ctx.risk.update_equity(state.equity, state.num_positions)

        info = {
            "equity": state.equity,
            "available_margin": state.available_margin,
            "total_margin_used": state.total_margin_used,
            "num_positions": state.num_positions,
            "positions": [
                {
                    "coin": p.coin, "side": p.side, "size": p.size,
                    "entry_price": p.entry_price,
                    "unrealized_pnl": p.unrealized_pnl,
                    "leverage": p.leverage,
                }
                for p in state.positions
            ],
        }
        return FunctionResultStatus.DONE, json.dumps(info, indent=2), info
    except Exception as e:
        return FunctionResultStatus.FAILED, str(e), {}


def _get_market_overview(_: str, **__) -> tuple[FunctionResultStatus, str, dict]:
    """Get prices, funding rates, OI for allowed markets."""
    ctx = _get_ctx()
    try:
        mids = ctx.feed.refresh_prices()
        funding = ctx.feed.refresh_funding()

        allowed = ctx.config.allowed_markets.all
        overview = {}
        for coin in allowed:
            mid = mids.get(coin)
            rate = next((r for r in funding if r.coin == coin), None)
            overview[coin] = {
                "price": mid,
                "funding_8h": rate.rate if rate else None,
                "funding_hourly": rate.hourly if rate else None,
                "funding_annualized": rate.annualized if rate else None,
            }

        return FunctionResultStatus.DONE, json.dumps(overview, indent=2), overview
    except Exception as e:
        return FunctionResultStatus.FAILED, str(e), {}


def _check_regime(_: str, **__) -> tuple[FunctionResultStatus, str, dict]:
    """Get current BTC 4H macro regime."""
    ctx = _get_ctx()
    try:
        candles = ctx.feed.refresh_candles("BTC", "4h", 100)
        regime = detect_regime(candles, ctx.config.btc_regime)
        ctx._last_regime = regime

        result = {"regime": regime.value, "candles_used": len(candles)}
        return FunctionResultStatus.DONE, f"BTC regime: {regime.value}", result
    except Exception as e:
        return FunctionResultStatus.FAILED, str(e), {}


def _scan_opportunities(_: str, **__) -> tuple[FunctionResultStatus, str, dict]:
    """Run all enabled strategies and return ranked signals."""
    ctx = _get_ctx()
    try:
        mids = ctx.feed.refresh_prices()
        funding = ctx.feed.refresh_funding()
        account = ctx.feed.get_account_state()
        ctx.risk.update_equity(account.equity, account.num_positions)

        candles = {}
        for coin in ctx.config.allowed_markets.perps:
            coin_candles = ctx.feed.refresh_candles(coin, "4h", 60)
            candles[f"{coin}_4h"] = coin_candles

        snapshot = MarketSnapshot(
            mids=mids, candles=candles,
            funding_rates=funding, account=account,
        )

        all_signals: list[StrategySignal] = []
        for strategy in ctx.strategies:
            if strategy.is_enabled(ctx.config):
                signals = strategy.evaluate(snapshot, ctx.config)
                all_signals.extend(signals)

        if ctx.smart_money.is_available(ctx.config):
            all_signals = [ctx.smart_money.enrich_signal(s, ctx.config) for s in all_signals]

        all_signals.sort(key=lambda s: s.confidence, reverse=True)

        result = {
            "num_opportunities": len(all_signals),
            "signals": [s.model_dump() for s in all_signals[:10]],
        }

        summary = [f"Found {len(all_signals)} opportunities:"]
        for s in all_signals[:5]:
            summary.append(f"  {s.strategy_name} {s.side} {s.coin} conf={s.confidence:.2f}")

        return FunctionResultStatus.DONE, "\n".join(summary), result
    except Exception as e:
        logger.exception("scan_opportunities failed")
        return FunctionResultStatus.FAILED, str(e), {}


def _evaluate_trade(_: str, coin: str = "", **__) -> tuple[FunctionResultStatus, str, dict]:
    """Validate a proposed trade against all hard constraints."""
    ctx = _get_ctx()
    try:
        from src.risk.constraints import PortfolioState, ProposedAction, validate_all

        mids = ctx.feed.get_all_mids()
        if coin not in mids:
            return FunctionResultStatus.FAILED, f"No price data for {coin}", {}

        state = PortfolioState(
            equity=ctx.risk.state.equity,
            peak_equity=ctx.risk.state.peak_equity,
            daily_pnl=ctx.risk.state.daily.realized_pnl,
            daily_pnl_pct=ctx.risk.state.daily_pnl_pct,
            num_positions=ctx.risk.state.num_positions,
            btc_regime=ctx._last_regime,
        )

        results = {}
        for side in ("long", "short"):
            proposed = ProposedAction(
                coin=coin, side=side,
                size_usd=ctx.risk.state.equity * ctx.config.risk.risk_per_trade_pct,
                leverage=ctx.config.risk.max_leverage_per_asset,
                strategy_name="evaluation",
            )
            allowed, violations = validate_all(proposed, state, ctx.config, ctx.freshness)
            results[side] = {"allowed": allowed, "violations": violations}

        return FunctionResultStatus.DONE, json.dumps(results, indent=2), results
    except Exception as e:
        return FunctionResultStatus.FAILED, str(e), {}


def _execute_trade(_: str, coin: str = "", side: str = "", **__) -> tuple[FunctionResultStatus, str, dict]:
    """Execute a trade via ACP to Degen Claw."""
    ctx = _get_ctx()
    try:
        mids = ctx.feed.get_all_mids()
        mid = mids.get(coin)
        if not mid:
            return FunctionResultStatus.FAILED, f"No price for {coin}", {}

        funding = ctx.feed.get_funding_rate(coin)

        signal = StrategySignal(
            strategy_name="agent_directed",
            coin=coin, side=side, confidence=0.7,
            recommended_size_pct=ctx.config.risk.risk_per_trade_pct,
            leverage=min(ctx.config.risk.max_leverage_per_asset, 5.0),
            stop_loss_pct=0.02, take_profit_pct=0.06,
            rationale=f"Agent-directed {side} on {coin} at ${mid:.2f}",
            constraints_checked=[],
        )

        result = ctx.executor.execute_signal(signal, mid, funding)
        return (
            FunctionResultStatus.DONE if result.executed else FunctionResultStatus.FAILED,
            result.model_dump_json(indent=2),
            result.model_dump(),
        )
    except Exception as e:
        return FunctionResultStatus.FAILED, str(e), {}


def _close_position(_: str, coin: str = "", **__) -> tuple[FunctionResultStatus, str, dict]:
    """Close a position via ACP."""
    ctx = _get_ctx()
    try:
        result = ctx.executor.close_position(coin, f"Agent closing {coin}")
        status = FunctionResultStatus.DONE if result.executed else FunctionResultStatus.FAILED
        return status, result.model_dump_json(indent=2), result.model_dump()
    except Exception as e:
        return FunctionResultStatus.FAILED, str(e), {}


def _get_performance(_: str, **__) -> tuple[FunctionResultStatus, str, dict]:
    """Get trading performance and competition metrics."""
    ctx = _get_ctx()
    try:
        risk_status = ctx.risk.status()
        competition = ctx.portfolio.competition_score()
        freshness_status = ctx.freshness.status()

        perf = {
            "risk": risk_status,
            "competition": competition,
            "data_freshness": freshness_status,
            "strategies_enabled": [s.name for s in ctx.strategies if s.is_enabled(ctx.config)],
        }
        return FunctionResultStatus.DONE, json.dumps(perf, indent=2), perf
    except Exception as e:
        return FunctionResultStatus.FAILED, str(e), {}


def _get_constraints(_: str, **__) -> tuple[FunctionResultStatus, str, dict]:
    """List all hard constraints."""
    constraints = {
        "kill_switch": "HL_KILL_SWITCH env var — instant halt",
        "btc_regime_long_block": "Bearish BTC regime blocks all long entries",
        "funding_rate_minimum": "Funding rate below threshold blocks carry trades",
        "smart_money_freshness": "Stale smart money data is ignored",
        "max_leverage": "Never exceed per-asset max leverage",
        "max_risk_per_trade": "Never exceed % equity per trade",
        "max_daily_loss": "Stop trading if daily loss exceeded",
        "allowed_markets": "Never trade unlisted markets",
        "data_freshness": "Block trade if required data is stale",
        "max_concurrent_positions": "Don't exceed max concurrent positions",
        "max_drawdown": "Hard drawdown kill switch",
    }
    return FunctionResultStatus.DONE, json.dumps(constraints, indent=2), constraints


def _get_acp_status(_: str, **__) -> tuple[FunctionResultStatus, str, dict]:
    """Get ACP connection status, pending/completed jobs."""
    ctx = _get_ctx()
    try:
        ctx.acp.process_pending_callbacks()

        state = ctx.acp.get_acp_state()
        pending = ctx.acp.get_pending_jobs()
        completed = ctx.acp.get_completed_jobs()

        result = {
            "live": ctx.acp.is_live,
            "mode": state.get("mode", "unknown"),
            "pending_jobs": len(pending),
            "completed_jobs": len(completed),
            "pending_details": {
                jid: {"coin": r.coin, "side": r.side, "size_usd": r.size_usd}
                for jid, r in list(pending.items())[:5]
            },
        }
        return FunctionResultStatus.DONE, json.dumps(result, indent=2), result
    except Exception as e:
        return FunctionResultStatus.FAILED, str(e), {}


# ── G.A.M.E. Function definitions ───────────────────────────────────────────

SKILL_FUNCTIONS: list[Function] = [
    Function(
        fn_name="get_account_info",
        fn_description="Get Hyperliquid account balance, equity, margin, and open positions",
        args=[], executable=_get_account_info,
    ),
    Function(
        fn_name="get_market_overview",
        fn_description="Get prices, funding rates, and OI for all allowed markets",
        args=[], executable=_get_market_overview,
    ),
    Function(
        fn_name="check_regime",
        fn_description="Get current BTC 4H macro regime (bullish/neutral/bearish). Bearish blocks long entries.",
        args=[], executable=_check_regime,
    ),
    Function(
        fn_name="scan_opportunities",
        fn_description="Run all enabled strategies and return ranked signals sorted by confidence",
        args=[], executable=_scan_opportunities,
    ),
    Function(
        fn_name="evaluate_trade",
        fn_description="Validate whether a trade on a coin would pass all hard constraints",
        args=[Argument(name="coin", type="str", description="Coin to evaluate (e.g. BTC, ETH)")],
        executable=_evaluate_trade,
    ),
    Function(
        fn_name="execute_trade",
        fn_description="Execute a trade via ACP to Degen Claw. Runs all constraints first.",
        args=[
            Argument(name="coin", type="str", description="Coin to trade (e.g. BTC, ETH)"),
            Argument(name="side", type="str", description="'long' or 'short'"),
        ],
        executable=_execute_trade,
    ),
    Function(
        fn_name="close_position",
        fn_description="Close an open position via ACP",
        args=[Argument(name="coin", type="str", description="Coin position to close")],
        executable=_close_position,
    ),
    Function(
        fn_name="get_performance",
        fn_description="Get competition metrics: Sortino, Return%, Profit Factor, drawdown",
        args=[], executable=_get_performance,
    ),
    Function(
        fn_name="get_constraints",
        fn_description="List all hard constraints and their descriptions",
        args=[], executable=_get_constraints,
    ),
    Function(
        fn_name="get_acp_status",
        fn_description="Get ACP connection status, mode (live/dry_run), pending and completed job counts",
        args=[], executable=_get_acp_status,
    ),
]
