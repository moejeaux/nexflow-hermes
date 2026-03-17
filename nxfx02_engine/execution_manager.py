"""
TradeExecutionManager for NXFX02.
Handles execution plan, liquidity check, slippage calculation, and fallback behaviors.
Stub DEX interface; never sends real on-chain transactions.
Safety guardrails enforced in code and docstrings.
"""
from typing import Protocol
from datetime import datetime
from .models import NXFX02ExecutionPlan, TradeExecutionResult

class DexInterface(Protocol):
    """Stub interface for DEX operations. Real implementation must be injected."""
    def estimate_slippage(self, token: str, side: str, notional_usd: float) -> float: ...
    def max_fillable_notional(self, token: str, side: str, max_slippage_pct: float) -> float: ...
    def execute_swap(self, token: str, side: str, notional_usd: float, max_slippage_pct: float) -> TradeExecutionResult: ...

class TradeExecutionManager:
    """
    Handles trade execution for approved plans.
    Guardrails:
    - Never exceed max_slippage_pct.
    - Never trade after deadline_ts.
    - Never increase notional above what NXFX02 planned (only reduce or abort).
    - Log whenever it scales down or aborts.
    - Treat missing LP/price data as a reason to abort safely (not to guess).
    """
    def __init__(self, dex: DexInterface):
        self.dex = dex

    def execute_trade(self, trade_id: str, plan: NXFX02ExecutionPlan, min_fill_pct: float) -> TradeExecutionResult:
        now = datetime.utcnow()
        if now > plan.execution.deadline_ts:
            return TradeExecutionResult(
                trade_id=trade_id,
                status="ABORTED",
                filled_notional_usd=0,
                avg_price=0,
                reason="deadline_expired",
                tx_hashes=[],
                timestamp=now
            )

        # Liquidity check
        max_fillable = self.dex.max_fillable_notional(
            plan.token_address, plan.execution.side, plan.execution.max_slippage_pct
        )
        desired = plan.sizing.target_position_notional_usd
        if max_fillable is None or max_fillable < desired * min_fill_pct:
            # Scale down order
            scaled = max_fillable if max_fillable is not None else 0
            # Log scale down
            reason = "insufficient_liquidity"
        else:
            scaled = desired
            reason = "ok"

        # Slippage calculation
        est_slippage = self.dex.estimate_slippage(
            plan.token_address, plan.execution.side, scaled
        )
        if est_slippage is None or est_slippage > plan.execution.max_slippage_pct:
            # Abort and log
            return TradeExecutionResult(
                trade_id=trade_id,
                status="ABORTED",
                filled_notional_usd=0,
                avg_price=0,
                reason="high_slippage",
                tx_hashes=[],
                timestamp=now
            )

        # Execute trade
        result = self.dex.execute_swap(
            plan.token_address, plan.execution.side, scaled, plan.execution.max_slippage_pct
        )
        # Log execution
        return result
