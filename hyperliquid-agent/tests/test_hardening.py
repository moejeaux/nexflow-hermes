"""Tests for ACP client and executor hardening."""

import pytest
from datetime import datetime, timezone

from src.acp.degen_claw import (
    AcpCloseRequest,
    AcpTradeRequest,
    AcpTradeResponse,
    DegenClawAcp,
)
from src.config import StrategyConfig
from src.execution.executor import MIN_TRADE_SIZE_USD, OrderExecutor, ExecutionResult
from src.market.freshness import FreshnessTracker
from src.risk.constraints import PortfolioState
from src.risk.supervisor import RiskSupervisor
from src.strategy.base import StrategySignal
from src.strategy.regime import BtcRegime


@pytest.fixture
def acp():
    """ACP client in dry-run mode (no credentials)."""
    return DegenClawAcp()


@pytest.fixture
def config():
    return StrategyConfig()


@pytest.fixture
def freshness():
    f = FreshnessTracker()
    f.record("prices")
    f.record("funding")
    f.record("account_state")
    return f


@pytest.fixture
def risk_supervisor(config):
    sup = RiskSupervisor(config.risk)
    sup.update_equity(10_000.0, 0)
    return sup


@pytest.fixture
def executor(acp, risk_supervisor, config, freshness):
    return OrderExecutor(
        acp=acp,
        risk_supervisor=risk_supervisor,
        config=config,
        freshness=freshness,
    )


class TestAcpDryRunMode:
    def test_dry_run_mode(self, acp):
        assert not acp.is_live

    def test_dry_run_state(self, acp):
        state = acp.get_acp_state()
        assert state["mode"] == "dry_run"

    def test_reject_zero_size(self, acp):
        req = AcpTradeRequest(
            coin="BTC", side="long", size_usd=0.0, leverage=3,
        )
        resp = acp.submit_trade(req)
        assert not resp.success
        assert "Invalid trade size" in resp.error

    def test_reject_negative_size(self, acp):
        req = AcpTradeRequest(
            coin="BTC", side="long", size_usd=-100.0, leverage=3,
        )
        resp = acp.submit_trade(req)
        assert not resp.success

    def test_reject_zero_leverage(self, acp):
        req = AcpTradeRequest(
            coin="BTC", side="long", size_usd=100.0, leverage=0,
        )
        resp = acp.submit_trade(req)
        assert not resp.success
        assert "Invalid leverage" in resp.error

    def test_valid_trade_dry_run(self, acp):
        req = AcpTradeRequest(
            coin="ETH", side="short", size_usd=200.0, leverage=3,
            stop_loss=3100.0, take_profit=2800.0,
        )
        resp = acp.submit_trade(req)
        assert resp.success
        assert resp.job_id is not None
        assert resp.job_id.startswith("dry_")

    def test_close_empty_coin(self, acp):
        req = AcpCloseRequest(coin="")
        resp = acp.submit_close(req)
        assert not resp.success
        assert "No coin" in resp.error

    def test_valid_close_dry_run(self, acp):
        req = AcpCloseRequest(coin="BTC", rationale="Taking profit")
        resp = acp.submit_close(req)
        assert resp.success
        assert resp.job_id.startswith("dry_close_")


class TestAcpJobTracking:
    def test_pending_jobs(self, acp):
        req = AcpTradeRequest(
            coin="BTC", side="long", size_usd=100.0, leverage=3,
        )
        resp = acp.submit_trade(req)
        assert resp.job_id in acp.get_pending_jobs()

    def test_mark_completed(self, acp):
        req = AcpTradeRequest(
            coin="BTC", side="long", size_usd=100.0, leverage=3,
        )
        resp = acp.submit_trade(req)
        acp.mark_completed(resp.job_id, AcpTradeResponse(success=True))
        assert resp.job_id not in acp.get_pending_jobs()
        assert resp.job_id in acp.get_completed_jobs()

    def test_multiple_jobs(self, acp):
        jobs = []
        for coin in ["BTC", "ETH", "SOL"]:
            req = AcpTradeRequest(coin=coin, side="long", size_usd=100.0, leverage=3)
            resp = acp.submit_trade(req)
            jobs.append(resp.job_id)

        assert len(acp.get_pending_jobs()) == 3

        acp.mark_completed(jobs[0], AcpTradeResponse(success=True))
        assert len(acp.get_pending_jobs()) == 2
        assert len(acp.get_completed_jobs()) == 1


class TestAcpJobQueue:
    def test_queue_operations(self):
        from src.acp.degen_claw import AcpJobQueue
        q = AcpJobQueue()
        assert q.pending_count == 0

        q.push("job1", "memo1")
        q.push("job2", None)
        assert q.pending_count == 2

        item = q.pop()
        assert item == ("job1", "memo1")
        assert q.pending_count == 1

    def test_drain(self):
        from src.acp.degen_claw import AcpJobQueue
        q = AcpJobQueue()
        q.push("a", None)
        q.push("b", None)

        items = q.drain()
        assert len(items) == 2
        assert q.pending_count == 0


class TestExecutorEdgeCases:
    def _make_signal(self, coin="ETH", side="long", confidence=0.7, size_pct=0.02):
        return StrategySignal(
            strategy_name="test",
            coin=coin,
            side=side,
            confidence=confidence,
            recommended_size_pct=size_pct,
            leverage=3.0,
            stop_loss_pct=0.02,
            take_profit_pct=0.06,
            rationale="Test signal",
            constraints_checked=[],
        )

    def test_trade_below_minimum_size(self, executor, risk_supervisor):
        risk_supervisor.update_equity(100.0, 0)
        signal = self._make_signal(size_pct=0.001)
        result = executor.execute_signal(signal, current_price=3000.0)
        assert not result.executed
        assert "below minimum" in result.reason

    def test_valid_execution(self, executor):
        signal = self._make_signal()
        portfolio = PortfolioState(
            equity=10_000.0, peak_equity=10_000.0, num_positions=0,
        )
        result = executor.execute_signal(
            signal, current_price=3000.0, portfolio_state=portfolio,
        )
        assert result.executed
        assert result.job_id is not None

    def test_constraint_violation_blocks_trade(self, executor):
        signal = self._make_signal(coin="SHIB")
        portfolio = PortfolioState(
            equity=10_000.0, peak_equity=10_000.0, num_positions=0,
        )
        result = executor.execute_signal(
            signal, current_price=0.00001, portfolio_state=portfolio,
        )
        assert not result.executed
        assert "not in allowed markets" in result.reason

    def test_close_position(self, executor):
        result = executor.close_position("BTC", "Test close")
        assert result.executed


class TestConfigValidation:
    def test_load_default_config(self):
        from src.config import load_strategy_config
        from pathlib import Path
        config = load_strategy_config(Path("/nonexistent/path.yaml"))
        assert config.risk.max_leverage_per_asset == 5.0
        assert config.risk.max_drawdown_hard_pct == 0.15
        assert config.risk.max_concurrent_positions == 3
