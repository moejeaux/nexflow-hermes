"""Contract C: NXFX02 → Execution Worker plan.

NXFX02 (Position & Execution Engine) produces this plan after combining
NXFX01 launch intelligence with NXFX05 risk limits. The execution worker
takes this plan and submits on-chain transactions.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from enums import EntryStyle, OrderType, TimeInForce


class ExecutionDecision(BaseModel):
    """Whether to execute and why."""
    execute: bool = False
    reason: str = ""


class SizingPlan(BaseModel):
    """Position sizing output from NXFX02."""
    target_position_notional_usd: float = 0.0
    max_additional_notional_usd: float = 0.0
    expected_risk_pct_of_equity: float = 0.0


class ExecutionParams(BaseModel):
    """How to execute the trade."""
    entry_style: EntryStyle = EntryStyle.SINGLE
    slice_count: int = 1
    max_slippage_pct: float = 1.0
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.IOC


class NXFX02ExecutionPlan(BaseModel):
    """Contract C: Execution plan from NXFX02 → Execution Worker.

    One plan per launch evaluation. If decision.execute is False, the
    sizing and execution fields are informational only (what *would* have
    been traded if conditions were met).
    """
    launch_id: str
    token_address: str
    chain: str = "base"

    decision: ExecutionDecision = Field(default_factory=ExecutionDecision)
    sizing: SizingPlan = Field(default_factory=SizingPlan)
    execution: ExecutionParams = Field(default_factory=ExecutionParams)
