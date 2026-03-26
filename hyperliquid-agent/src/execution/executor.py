"""Order executor — validates constraints, routes trades through ACP."""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel

from src.acp.degen_claw import AcpCloseRequest, AcpTradeRequest, DegenClawAcp
from src.config import StrategyConfig
from src.market.freshness import FreshnessTracker
from src.market.types import FundingRate
from src.risk.constraints import PortfolioState, ProposedAction, validate_all
from src.risk.supervisor import RiskSupervisor
from src.strategy.base import StrategySignal
from src.strategy.smart_money import SmartMoneyConfirmation

logger = logging.getLogger(__name__)

MIN_TRADE_SIZE_USD = 10.0


class TradeAction(BaseModel):
    """Structured action output."""

    market: str
    side: Literal["long", "short"]
    size_usd: float
    leverage: float
    entry_type: Literal["market", "limit"]
    entry_price: float | None = None
    stop_loss: float
    take_profit: float
    rationale: str
    constraints_passed: list[str]


class ExecutionResult(BaseModel):
    """Result of an execution attempt."""

    action: TradeAction
    executed: bool
    job_id: str | None = None
    reason: str | None = None


class OrderExecutor:
    """Validates signals against all hard constraints, then submits via ACP."""

    def __init__(
        self,
        acp: DegenClawAcp,
        risk_supervisor: RiskSupervisor,
        config: StrategyConfig,
        freshness: FreshnessTracker,
        smart_money: SmartMoneyConfirmation | None = None,
    ):
        self._acp = acp
        self._risk = risk_supervisor
        self._config = config
        self._freshness = freshness
        self._smart_money = smart_money

    def execute_signal(
        self,
        signal: StrategySignal,
        current_price: float,
        funding_rate: FundingRate | None = None,
        portfolio_state: PortfolioState | None = None,
    ) -> ExecutionResult:
        """Full pipeline: enrich -> validate -> size -> submit via ACP."""

        # 1. Enrich with smart money confirmation
        if self._smart_money and self._smart_money.is_available(self._config):
            signal = self._smart_money.enrich_signal(signal, self._config)

        # 2. Calculate size
        equity = self._risk.state.equity
        size_multiplier = self._risk.get_size_multiplier()
        size_usd = equity * signal.recommended_size_pct * size_multiplier

        # 3. Build proposed action
        proposed = ProposedAction(
            coin=signal.coin,
            side=signal.side,
            size_usd=size_usd,
            leverage=signal.leverage,
            strategy_name=signal.strategy_name,
        )

        # 4. Build portfolio state
        if portfolio_state is None:
            portfolio_state = PortfolioState(
                equity=equity,
                peak_equity=self._risk.state.peak_equity,
                daily_pnl=self._risk.state.daily.realized_pnl,
                daily_pnl_pct=self._risk.state.daily_pnl_pct,
                num_positions=self._risk.state.num_positions,
            )

        # 5. Run hard constraints
        funding_hourly = funding_rate.hourly if funding_rate else None
        allowed, violations = validate_all(
            action=proposed,
            state=portfolio_state,
            config=self._config,
            freshness=self._freshness,
            current_funding_hourly=funding_hourly,
        )

        # Build trade action
        is_buy = signal.side == "long"
        sl_price = current_price * (1 - signal.stop_loss_pct) if is_buy else current_price * (1 + signal.stop_loss_pct)
        tp_price = current_price * (1 + signal.take_profit_pct) if is_buy else current_price * (1 - signal.take_profit_pct)

        action = TradeAction(
            market=signal.coin,
            side=signal.side,
            size_usd=size_usd,
            leverage=signal.leverage,
            entry_type="market",
            entry_price=current_price,
            stop_loss=round(sl_price, 6),
            take_profit=round(tp_price, 6),
            rationale=signal.rationale,
            constraints_passed=signal.constraints_checked,
        )

        if not allowed:
            reason = "; ".join(violations)
            logger.warning(
                "Trade BLOCKED %s %s %s: %s",
                signal.coin, signal.side, signal.strategy_name, reason,
            )
            return ExecutionResult(action=action, executed=False, reason=reason)

        if size_usd < MIN_TRADE_SIZE_USD:
            reason = (
                f"Trade size ${size_usd:.2f} below minimum ${MIN_TRADE_SIZE_USD:.2f} "
                f"(equity=${equity:.2f}, multiplier={size_multiplier:.2f})"
            )
            logger.warning("Trade SKIPPED %s %s: %s", signal.coin, signal.side, reason)
            return ExecutionResult(action=action, executed=False, reason=reason)

        # 6. Submit via ACP to Degen Claw
        logger.info(
            "Submitting via ACP: %s %s %s $%.2f %dx | SL=$%.2f TP=$%.2f | %s",
            signal.strategy_name, signal.side, signal.coin,
            size_usd, int(signal.leverage), sl_price, tp_price, signal.rationale,
        )

        acp_response = self._acp.submit_trade(AcpTradeRequest(
            coin=signal.coin,
            side=signal.side,
            size_usd=size_usd,
            leverage=int(signal.leverage),
            order_type="market",
            stop_loss=round(sl_price, 6),
            take_profit=round(tp_price, 6),
            rationale=signal.rationale,
        ))

        if acp_response.success:
            return ExecutionResult(
                action=action, executed=True, job_id=acp_response.job_id,
            )
        else:
            return ExecutionResult(
                action=action, executed=False, reason=acp_response.error,
            )

    def close_position(self, coin: str, rationale: str = "") -> ExecutionResult:
        """Close a position via ACP."""
        action = TradeAction(
            market=coin, side="long", size_usd=0, leverage=1,
            entry_type="market", stop_loss=0, take_profit=0,
            rationale=rationale or f"Closing {coin} position",
            constraints_passed=[],
        )

        response = self._acp.submit_close(AcpCloseRequest(
            coin=coin, rationale=rationale,
        ))

        return ExecutionResult(
            action=action,
            executed=response.success,
            job_id=response.job_id,
            reason=response.error,
        )
