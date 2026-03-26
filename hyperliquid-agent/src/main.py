"""NXFH01 agent entry point — wires all components, starts G.A.M.E. agent.

Trades are routed through ACP to Degen Claw (agent 8654).
Market data is read-only via Hyperliquid Info API.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from game_sdk.game.agent import Agent, WorkerConfig

from src.acp.degen_claw import DegenClawAcp
from src.config import (
    GAME_AGENT_ID,
    GAME_API_KEY,
    HL_WALLET_ADDRESS,
    INITIAL_EQUITY,
    STEP_INTERVAL,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    load_strategy_config,
    validate_required_env,
)
from src.notifications.telegram import TelegramBot, create_bot
from src.market.data_feed import MarketDataFeed
from src.market.freshness import FreshnessTracker
from src.execution.executor import OrderExecutor
from src.risk.supervisor import RiskSupervisor
from src.skill.functions import SKILL_FUNCTIONS, SkillContext, set_context
from src.state.portfolio import PortfolioTracker
from src.state.persistence import StateStore
from src.strategy.smart_money import SmartMoneyConfirmation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("nxfh01")
_ROOT = Path(__file__).resolve().parent.parent


def _get_agent_state(_function_result=None, _current_state=None) -> dict:
    """High-level agent state for the G.A.M.E. planner."""
    from src.skill.functions import _ctx

    if _ctx is None:
        return {"status": "Agent not initialized"}

    try:
        risk_status = _ctx.risk.status()
        regime = _ctx._last_regime.value
        competition = _ctx.portfolio.competition_score()
        enabled = [s.name for s in _ctx.strategies if s.is_enabled(_ctx.config)]
        acp_state = _ctx.acp.get_acp_state()

        return {
            "equity": risk_status['equity'],
            "drawdown_pct": risk_status['drawdown_pct'],
            "daily_pnl_pct": risk_status['daily_pnl_pct'],
            "num_positions": risk_status['num_positions'],
            "btc_regime": regime,
            "sortino_ratio": competition['sortino_ratio'],
            "return_pct": competition['return_pct'],
            "profit_factor": competition['profit_factor'],
            "halted": risk_status['halted'],
            "acp_mode": acp_state.get('mode', 'unknown'),
            "pending_acp_jobs": acp_state.get('pending_jobs', 0),
            "strategies": enabled,
        }
    except Exception as e:
        return {"error": str(e)}


def _get_trading_state(_function_result=None, _current_state=None) -> dict:
    """Detailed trading state for the worker."""
    from src.skill.functions import _ctx

    if _ctx is None:
        return {"status": "Worker not initialized"}

    try:
        # Process any pending ACP callbacks first
        _ctx.acp.process_pending_callbacks()

        account = _ctx.feed.get_account_state()
        equity = account.equity
        # Use INITIAL_EQUITY when Degen Claw manages the subaccount
        if equity < 1.0 and INITIAL_EQUITY > 0:
            equity = INITIAL_EQUITY
        _ctx.risk.update_equity(equity, account.num_positions)

        positions = []
        for p in account.positions:
            positions.append({
                "side": p.side.upper(),
                "coin": p.coin,
                "size": p.size,
                "entry_price": p.entry_price,
                "unrealized_pnl": p.unrealized_pnl,
            })

        can_trade, reason = _ctx.risk.can_trade()

        state = {
            "equity": equity,
            "available_margin": account.available_margin,
            "num_positions": account.num_positions,
            "positions": positions,
            "can_trade": can_trade,
            "trade_reason": reason,
            "size_multiplier": _ctx.risk.get_size_multiplier(),
            "acp_live": _ctx.acp.is_live,
        }

        pending = _ctx.acp.get_pending_jobs()
        if pending:
            state["pending_acp_jobs"] = len(pending)

        return state
    except Exception as e:
        return {"error": str(e)}


def build_context() -> SkillContext:
    """Wire all components together."""
    config = load_strategy_config()
    freshness = FreshnessTracker()

    feed = MarketDataFeed(
        freshness=freshness,
        wallet_address=HL_WALLET_ADDRESS,
    )

    risk_supervisor = RiskSupervisor(config.risk)

    # ACP client — uses OpenClaw ACP CLI
    acp = DegenClawAcp()

    smart_money = SmartMoneyConfirmation(freshness)

    executor = OrderExecutor(
        acp=acp,
        risk_supervisor=risk_supervisor,
        config=config,
        freshness=freshness,
        smart_money=smart_money,
    )

    store = StateStore()
    initial_equity = 0.0
    try:
        account = feed.get_account_state()
        initial_equity = account.equity
        risk_supervisor.update_equity(account.equity, account.num_positions)
        logger.info("Initial equity from HL: $%.2f", initial_equity)
    except Exception as e:
        logger.warning("Could not fetch initial equity: %s", e)

    # Use INITIAL_EQUITY as floor when Degen Claw manages the subaccount
    if initial_equity < 1.0 and INITIAL_EQUITY > 0:
        initial_equity = INITIAL_EQUITY
        risk_supervisor.update_equity(initial_equity, 0)
        logger.info("Using INITIAL_EQUITY override: $%.2f", initial_equity)

    portfolio = PortfolioTracker(starting_equity=max(initial_equity, 1.0))
    for fill in store.load_fills():
        portfolio.record_fill(fill)
    for snap in store.load_equity_curve():
        portfolio.equity_curve.append(snap)

    logger.info("Restored %d fills, %d equity snapshots from SQLite",
                len(portfolio.fills), len(portfolio.equity_curve))

    return SkillContext(
        feed=feed,
        config=config,
        freshness=freshness,
        risk_supervisor=risk_supervisor,
        executor=executor,
        acp=acp,
        smart_money=smart_money,
        portfolio=portfolio,
    )


def _setup_telegram(ctx: SkillContext) -> TelegramBot | None:
    """Create Telegram bot and register agent commands."""
    bot = create_bot(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    if bot is None:
        return None

    from src.skill.functions import (
        _get_account_info,
        _get_market_overview,
        _get_performance,
        _get_acp_status,
        _get_constraints,
    )

    def _cmd_status(_args: str) -> str:
        status, msg, info = _get_account_info()
        risk = ctx.risk.status()
        lines = [
            "*NXFH01 Status*",
            f"Equity: ${info.get('equity', 0):.2f}",
            f"Margin available: ${info.get('available_margin', 0):.2f}",
            f"Positions: {info.get('num_positions', 0)}",
            f"Halted: {risk.get('halted', False)}",
            f"Drawdown: {risk.get('drawdown_pct', 0):.1%}",
        ]
        return "\n".join(lines)

    def _cmd_positions(_args: str) -> str:
        _, _, info = _get_account_info()
        positions = info.get("positions", [])
        if not positions:
            return "No open positions."
        lines = ["*Open Positions*"]
        for p in positions:
            lines.append(
                f"  {p['side']} {p['coin']} "
                f"size={p['size']} entry=${p['entry_price']:.2f} "
                f"PnL=${p['unrealized_pnl']:.2f}"
            )
        return "\n".join(lines)

    def _cmd_performance(_args: str) -> str:
        _, _, info = _get_performance()
        comp = info.get("competition", {})
        lines = [
            "*Performance*",
            f"Sortino: {comp.get('sortino_ratio', 0):.2f}",
            f"Return: {comp.get('return_pct', 0):.2%}",
            f"Profit Factor: {comp.get('profit_factor', 0):.2f}",
            f"Strategies: {', '.join(info.get('strategies_enabled', []))}",
        ]
        return "\n".join(lines)

    def _cmd_acp(_args: str) -> str:
        _, msg, _ = _get_acp_status()
        return f"*ACP Status*\n{msg}"

    def _cmd_constraints(_args: str) -> str:
        _, msg, _ = _get_constraints()
        return f"*Hard Constraints*\n{msg}"

    bot.register("status", _cmd_status)
    bot.register("positions", _cmd_positions)
    bot.register("performance", _cmd_performance)
    bot.register("acp", _cmd_acp)
    bot.register("constraints", _cmd_constraints)

    # Re-register help with the full command list
    bot.register("help", lambda _: (
        "*NXFH01 Commands*\n"
        "/status — account equity, margin, halt state\n"
        "/positions — open positions\n"
        "/performance — Sortino, return%, profit factor\n"
        "/acp — ACP connection status\n"
        "/constraints — list hard constraints\n"
        "/help — this message"
    ))
    bot.register("start", bot._handlers["help"])

    return bot


def _build_workers(ctx: SkillContext) -> list[WorkerConfig]:
    """Build worker configs."""
    trading_worker = WorkerConfig(
        id="nxfh01_trader",
        worker_description=(
            "Trades Hyperliquid perps via ACP to Degen Claw. "
            "Strategies: funding carry, directional momentum, RWA macro windows. "
            "Always checks BTC regime, data freshness, and risk constraints first. "
            "Favors no trade over a low-conviction trade. "
            "Competition scoring: Sortino (40%) + Return% (35%) + Profit Factor (25%)."
        ),
        get_state_fn=_get_trading_state,
        action_space=SKILL_FUNCTIONS,
    )
    return [trading_worker]


def main():
    """Entry point."""
    missing = validate_required_env()
    if missing:
        logger.error("Missing required env vars: %s — exiting", ", ".join(missing))
        sys.exit(1)

    logger.info("Initializing NXFH01 Degen Claw trading agent...")

    ctx = build_context()
    set_context(ctx)

    # Start Telegram bot (if configured)
    tg_bot = _setup_telegram(ctx)
    if tg_bot:
        tg_bot.start_polling()
        tg_bot.notify("NXFH01 agent starting up...")

    enabled = [s.name for s in ctx.strategies if s.is_enabled(ctx.config)]
    logger.info(
        "Config: %d perp markets, %d RWA markets, strategies=%s, ACP=%s",
        len(ctx.config.allowed_markets.perps),
        len(ctx.config.allowed_markets.rwa),
        enabled,
        "LIVE" if ctx.acp.is_live else "DRY-RUN",
    )

    workers = _build_workers(ctx)

    # Agent description
    agent_desc = (
        "NXFH01: Conservative autonomous Hyperliquid perp trader for the "
        "Degen Claw weekly competition ($100K USDC prize pool). "
        "Trades via ACP (agent 8654). Read-only market data from HL Info API. "
        "4 toggleable strategies: funding carry, momentum, smart money, RWA. "
        "Hard constraints: max 5x leverage, 15% drawdown kill switch, "
        "5% daily loss limit, 3 max positions, market allow-list."
    )

    # Create agent — reuse existing ID if set, otherwise create new
    _agent_kwargs = dict(
        api_key=GAME_API_KEY,
        name="NXFH01",
        agent_goal=(
            "Maximize risk-adjusted returns trading Hyperliquid perps via Degen Claw. "
            "Optimize for Sortino Ratio (40%), Return% (35%), and Profit Factor (25%). "
            "Use only enabled strategies. Never trade when constraints are violated. "
            "Favor no trade over a low-conviction trade. Preserve capital above all."
        ),
        agent_description=agent_desc,
        get_agent_state_fn=_get_agent_state,
        workers=workers,
    )

    if GAME_AGENT_ID:
        logger.info("Reusing existing G.A.M.E. agent: %s", GAME_AGENT_ID)
        # Patch client.create_agent to return existing ID instead of creating new
        from unittest.mock import patch
        with patch("game_sdk.game.api_v2.GAMEClientV2.create_agent", return_value=GAME_AGENT_ID):
            agent = Agent(**_agent_kwargs)
    else:
        for attempt in range(5):
            try:
                agent = Agent(**_agent_kwargs)
                break
            except ValueError as e:
                if "429" in str(e) and attempt < 4:
                    wait = 30 * (attempt + 1)
                    logger.warning("Rate limited on agent creation — waiting %ds (attempt %d/5)", wait, attempt + 1)
                    time.sleep(wait)
                else:
                    raise
        # Save the new agent ID so it can be reused on restart
        new_id = agent.agent_id
        logger.info("New G.A.M.E. agent created: %s — saving to .env", new_id)
        env_path = _ROOT / ".env"
        env_text = env_path.read_text() if env_path.exists() else ""
        if "GAME_AGENT_ID=" in env_text:
            import re
            env_text = re.sub(r"^GAME_AGENT_ID=.*$", f"GAME_AGENT_ID={new_id}", env_text, flags=re.MULTILINE)
        else:
            env_text += f"\nGAME_AGENT_ID={new_id}\n"
        env_path.write_text(env_text)
        logger.info("GAME_AGENT_ID=%s written to .env", new_id)

    logger.info("Compiling NXFH01 agent...")
    for attempt in range(5):
        try:
            agent.compile()
            break
        except ValueError as e:
            if "429" in str(e) and attempt < 4:
                wait = 30 * (attempt + 1)
                logger.warning("Rate limited on compile — waiting %ds (attempt %d/5)", wait, attempt + 1)
                time.sleep(wait)
            else:
                raise

    logger.info("NXFH01 running (%s mode). Ctrl+C to stop.",
                "LIVE" if ctx.acp.is_live else "DRY-RUN")
    try:
        while True:
            try:
                agent.step()
            except ValueError as e:
                err = str(e)
                if "429" in err or "Too Many Requests" in err:
                    logger.warning("Rate limited — waiting 60s before next step")
                    time.sleep(60)
                elif "does not match current function" in err or "status 400" in err:
                    logger.warning("Stale session — recompiling agent: %s", err[:120])
                    try:
                        agent.compile()
                    except Exception as ce:
                        logger.warning("Recompile failed: %s — will retry", ce)
                    time.sleep(5)
                else:
                    raise
            time.sleep(STEP_INTERVAL)
    except KeyboardInterrupt:
        logger.info("NXFH01 shutting down...")
        if tg_bot:
            tg_bot.notify("NXFH01 agent shutting down.")
            tg_bot.stop()


if __name__ == "__main__":
    main()
