"""Tests for competition metrics (Sortino, Return%, Profit Factor)."""

import pytest
from datetime import datetime, timezone

from src.state.portfolio import EquitySnapshot, Fill, PortfolioTracker


@pytest.fixture
def tracker():
    return PortfolioTracker(starting_equity=10_000.0)


class TestTotalReturn:
    def test_no_data(self, tracker):
        assert tracker.total_return_pct() == 0.0

    def test_positive_return(self, tracker):
        tracker.record_equity(11_000.0)
        assert tracker.total_return_pct() == pytest.approx(0.10)

    def test_negative_return(self, tracker):
        tracker.record_equity(9_000.0)
        assert tracker.total_return_pct() == pytest.approx(-0.10)


class TestProfitFactor:
    def test_no_fills(self, tracker):
        assert tracker.profit_factor() == 0.0

    def test_all_winners(self, tracker):
        for _ in range(3):
            tracker.record_fill(Fill(
                coin="BTC", side="long", size=0.01,
                entry_price=50000, exit_price=51000,
                realized_pnl=100.0,
                entry_time=datetime.now(timezone.utc),
                exit_time=datetime.now(timezone.utc),
                strategy="funding_carry",
            ))
        assert tracker.profit_factor() == float("inf")

    def test_mixed(self, tracker):
        tracker.record_fill(Fill(
            coin="BTC", side="long", size=0.01,
            entry_price=50000, exit_price=51000,
            realized_pnl=200.0,
            entry_time=datetime.now(timezone.utc),
            exit_time=datetime.now(timezone.utc),
            strategy="momentum",
        ))
        tracker.record_fill(Fill(
            coin="ETH", side="short", size=1.0,
            entry_price=3000, exit_price=3100,
            realized_pnl=-100.0,
            entry_time=datetime.now(timezone.utc),
            exit_time=datetime.now(timezone.utc),
            strategy="momentum",
        ))
        assert tracker.profit_factor() == pytest.approx(2.0)


class TestWinRate:
    def test_no_fills(self, tracker):
        assert tracker.win_rate() == 0.0

    def test_fifty_percent(self, tracker):
        for pnl in [100, -50]:
            tracker.record_fill(Fill(
                coin="BTC", side="long", size=0.01,
                entry_price=50000, exit_price=50500,
                realized_pnl=pnl,
                entry_time=datetime.now(timezone.utc),
                exit_time=datetime.now(timezone.utc),
                strategy="funding_carry",
            ))
        assert tracker.win_rate() == pytest.approx(0.5)


class TestSortino:
    def test_not_enough_data(self, tracker):
        tracker.record_equity(10_100)
        assert tracker.sortino_ratio() == 0.0

    def test_positive_returns(self, tracker):
        for i in range(20):
            tracker.record_equity(10_000 + i * 50)
        sortino = tracker.sortino_ratio(annualize=False)
        assert sortino > 0  # all positive returns → high sortino

    def test_mixed_returns(self, tracker):
        equities = [10_000, 10_100, 9_900, 10_200, 9_800, 10_300, 9_700]
        for eq in equities:
            tracker.record_equity(eq)
        sortino = tracker.sortino_ratio(annualize=False)
        # Should be finite and computable with downside volatility
        assert sortino != float("inf")


class TestCompetitionScore:
    def test_structure(self, tracker):
        tracker.record_equity(10_500)
        score = tracker.competition_score()
        assert "sortino_ratio" in score
        assert "return_pct" in score
        assert "profit_factor" in score
        assert score["sortino_weight"] == 0.40
        assert score["return_weight"] == 0.35
        assert score["profit_factor_weight"] == 0.25
