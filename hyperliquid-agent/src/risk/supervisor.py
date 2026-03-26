"""Risk supervisor — drawdown tracking, position limits, daily loss."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from src.config import RiskConfig

logger = logging.getLogger(__name__)


@dataclass
class DailyStats:
    """Tracks daily PnL for the daily loss limit."""

    date: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    starting_equity: float = 0.0
    realized_pnl: float = 0.0
    trade_count: int = 0

    @property
    def pnl_pct(self) -> float:
        if self.starting_equity <= 0:
            return 0.0
        return self.realized_pnl / self.starting_equity


@dataclass
class RiskState:
    """Live portfolio risk state."""

    equity: float = 0.0
    peak_equity: float = 0.0
    num_positions: int = 0
    daily: DailyStats = field(default_factory=DailyStats)

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity)

    @property
    def daily_pnl_pct(self) -> float:
        return self.daily.pnl_pct


class RiskSupervisor:
    """Monitors portfolio risk and enforces limits."""

    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()
        self._state = RiskState()
        self._halted = False

    @property
    def state(self) -> RiskState:
        return self._state

    @property
    def is_halted(self) -> bool:
        return self._halted

    def update_equity(self, equity: float, num_positions: int = 0) -> None:
        """Update live equity and position count."""
        today = datetime.now(timezone.utc).date()

        # Reset daily stats on new day
        if self._state.daily.date != today:
            self._state.daily = DailyStats(date=today, starting_equity=equity)

        # Set starting equity on first update of the day
        if self._state.daily.starting_equity <= 0:
            self._state.daily.starting_equity = equity

        self._state.equity = equity
        self._state.num_positions = num_positions

        # Track peak equity (high-water mark)
        if equity > self._state.peak_equity:
            self._state.peak_equity = equity

        # Auto-halt on hard drawdown
        if self._state.drawdown_pct >= self.config.max_drawdown_hard_pct:
            if not self._halted:
                logger.warning(
                    "HARD DRAWDOWN reached: %.1f%% >= %.1f%% — halting all trading",
                    self._state.drawdown_pct * 100,
                    self.config.max_drawdown_hard_pct * 100,
                )
                self._halted = True

    def record_trade(self, realized_pnl: float) -> None:
        """Record a completed trade's PnL."""
        self._state.daily.realized_pnl += realized_pnl
        self._state.daily.trade_count += 1

    def halt(self) -> None:
        """Manual emergency halt."""
        self._halted = True
        logger.warning("Trading halted by manual kill switch")

    def resume(self) -> None:
        """Resume trading after halt."""
        self._halted = False
        logger.info("Trading resumed")

    def get_size_multiplier(self) -> float:
        """Return a position size multiplier based on drawdown state.

        - No drawdown: 1.0x
        - Between soft and hard: linearly reduced from 1.0 to 0.25
        - At or beyond hard: 0.0 (halted)
        """
        dd = self._state.drawdown_pct
        soft = self.config.max_drawdown_soft_pct
        hard = self.config.max_drawdown_hard_pct

        if dd < soft:
            return 1.0
        if dd >= hard:
            return 0.0

        # Linear interpolation between soft and hard
        frac = (dd - soft) / (hard - soft)
        return max(0.25, 1.0 - frac * 0.75)

    def can_trade(self) -> tuple[bool, str | None]:
        """Check if trading is currently allowed."""
        if self._halted:
            return False, "Trading is halted"

        if self._state.drawdown_pct >= self.config.max_drawdown_hard_pct:
            return False, f"Hard drawdown limit reached: {self._state.drawdown_pct:.1%}"

        if self._state.daily_pnl_pct < -self.config.max_daily_loss_pct:
            return False, f"Daily loss limit reached: {self._state.daily_pnl_pct:.1%}"

        if self._state.num_positions >= self.config.max_concurrent_positions:
            return False, f"Max positions reached: {self._state.num_positions}"

        return True, None

    def status(self) -> dict:
        """Return current risk status summary."""
        return {
            "equity": self._state.equity,
            "peak_equity": self._state.peak_equity,
            "drawdown_pct": round(self._state.drawdown_pct * 100, 2),
            "daily_pnl_pct": round(self._state.daily_pnl_pct * 100, 2),
            "daily_trades": self._state.daily.trade_count,
            "num_positions": self._state.num_positions,
            "size_multiplier": round(self.get_size_multiplier(), 3),
            "halted": self._halted,
        }
