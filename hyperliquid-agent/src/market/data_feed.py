"""Read-only Hyperliquid market data feed.

Uses HL Info class ONLY — no Exchange class. All trade execution
goes through the ACP plugin (acp/degen_claw.py), not directly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from hyperliquid.info import Info

from src.config import HL_API_URL, HL_WALLET_ADDRESS
from src.market.freshness import FreshnessTracker
from src.market.types import (
    AccountState,
    BookLevel,
    Candle,
    FundingRate,
    OrderBook,
    Position,
)

logger = logging.getLogger(__name__)


class MarketDataFeed:
    """Read-only wrapper around Hyperliquid Info API.

    Provides market data + account state. Does NOT place orders.
    """

    def __init__(
        self,
        freshness: FreshnessTracker,
        wallet_address: str | None = None,
        api_url: str | None = None,
    ):
        self._api_url = api_url or HL_API_URL
        self._wallet_address = (wallet_address or HL_WALLET_ADDRESS).lower()
        self._freshness = freshness

        self.info = Info(self._api_url, skip_ws=True)

        # Caches
        self._mids: dict[str, float] = {}
        self._candles: dict[str, list[Candle]] = {}
        self._funding: list[FundingRate] = []

        logger.info("MarketDataFeed initialized (read-only) for %s", self._wallet_address)

    # ── account state ────────────────────────────────────────────────────

    def get_account_state(self) -> AccountState:
        """Fetch current account balances and positions."""
        raw = self.info.user_state(self._wallet_address)

        positions = []
        for p in raw.get("assetPositions", []):
            pos = p.get("position", {})
            szi = float(pos.get("szi", 0))
            if szi == 0:
                continue
            side = "long" if szi > 0 else "short"
            abs_size = abs(szi)
            positions.append(Position(
                coin=pos.get("coin", ""),
                side=side,
                size=abs_size,
                entry_price=float(pos.get("entryPx", 0)),
                mark_price=float(pos.get("positionValue", 0)) / max(abs_size, 1e-9),
                unrealized_pnl=float(pos.get("unrealizedPnl", 0)),
                leverage=float(pos.get("leverage", {}).get("value", 1)),
                liquidation_price=float(lp) if (lp := pos.get("liquidationPx")) else None,
                margin_used=float(pos.get("marginUsed", 0)),
            ))

        cross_margin = raw.get("crossMarginSummary", {})
        state = AccountState(
            equity=float(cross_margin.get("accountValue", 0)),
            available_margin=float(cross_margin.get("totalRawUsd", 0)),
            total_margin_used=float(cross_margin.get("totalMarginUsed", 0)),
            positions=positions,
            timestamp=datetime.now(timezone.utc),
        )
        self._freshness.record("account_state")
        return state

    # ── prices ───────────────────────────────────────────────────────────

    def refresh_prices(self) -> dict[str, float]:
        """Fetch latest mid prices for all markets."""
        self._mids = {k: float(v) for k, v in self.info.all_mids().items()}
        self._freshness.record("prices")
        return self._mids

    def get_mid(self, coin: str) -> float | None:
        return self._mids.get(coin)

    def get_all_mids(self) -> dict[str, float]:
        return dict(self._mids)

    # ── candles ──────────────────────────────────────────────────────────

    def refresh_candles(self, coin: str, interval: str = "4h", limit: int = 100) -> list[Candle]:
        """Fetch OHLCV candles."""
        raw = self.info.candles_snapshot(coin, interval, limit)
        candles = []
        for c in raw:
            candles.append(Candle(
                timestamp=datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc),
                open=float(c["o"]),
                high=float(c["h"]),
                low=float(c["l"]),
                close=float(c["c"]),
                volume=float(c["v"]),
            ))

        key = f"{coin}_{interval}"
        self._candles[key] = candles
        self._freshness.record(f"candles_{key}")
        if coin == "BTC":
            self._freshness.record("btc_candles")
        return candles

    def get_candles(self, coin: str, interval: str = "4h") -> list[Candle]:
        return self._candles.get(f"{coin}_{interval}", [])

    # ── funding rates ────────────────────────────────────────────────────

    def refresh_funding(self) -> list[FundingRate]:
        """Fetch current funding rates for all perp markets."""
        rates = []
        try:
            ctx_data = self.info.meta_and_asset_ctxs()
            if ctx_data and len(ctx_data) > 1:
                for asset_ctx in ctx_data[1]:
                    if not isinstance(asset_ctx, dict):
                        continue
                    coin = asset_ctx.get("coin", "")
                    funding = float(asset_ctx.get("funding", 0))
                    rates.append(FundingRate(
                        coin=coin,
                        rate=funding,
                        predicted_rate=None,
                        timestamp=datetime.now(timezone.utc),
                    ))
        except Exception as e:
            logger.error("Failed to fetch funding rates: %s", e)

        self._funding = rates
        self._freshness.record("funding")
        return rates

    def get_funding(self) -> list[FundingRate]:
        return list(self._funding)

    def get_funding_rate(self, coin: str) -> FundingRate | None:
        return next((r for r in self._funding if r.coin == coin), None)

    def get_extreme_funding(
        self, min_hourly_rate: float, allowed_coins: set[str] | None = None
    ) -> list[FundingRate]:
        """Return funding rates above threshold, sorted by magnitude."""
        candidates = []
        for r in self._funding:
            if allowed_coins and r.coin not in allowed_coins:
                continue
            if abs(r.hourly) >= min_hourly_rate:
                candidates.append(r)
        candidates.sort(key=lambda r: abs(r.hourly), reverse=True)
        return candidates

    # ── order book ───────────────────────────────────────────────────────

    def get_l2_book(self, coin: str) -> OrderBook:
        """Fetch L2 order book snapshot."""
        raw = self.info.l2_snapshot(coin)
        levels = raw.get("levels", [[], []])
        book = OrderBook(
            coin=coin,
            bids=[BookLevel(price=float(b["px"]), size=float(b["sz"])) for b in levels[0]],
            asks=[BookLevel(price=float(a["px"]), size=float(a["sz"])) for a in levels[1]] if len(levels) > 1 else [],
            timestamp=datetime.now(timezone.utc),
        )
        self._freshness.record(f"orderbook_{coin}")
        return book

    # ── metadata ─────────────────────────────────────────────────────────

    def get_meta(self) -> dict:
        """Fetch exchange metadata (universe, asset info)."""
        return self.info.meta()

    # ── bulk refresh ─────────────────────────────────────────────────────

    def refresh_all(self, btc_candle_interval: str = "4h") -> None:
        """Refresh prices, funding rates, and BTC candles."""
        self.refresh_prices()
        self.refresh_funding()
        self.refresh_candles("BTC", btc_candle_interval)
