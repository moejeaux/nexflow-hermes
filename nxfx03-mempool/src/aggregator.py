"""NXFX03 Mempool Feature Aggregator.

Produces per-token mempool feature snapshots (Contract D) from raw pending
swap events. Owns feature computation and derived flag thresholds — never
makes trading decisions.

This module mirrors and extends the existing mempool_watcher in nxfx01-api,
but structured as a standalone service producing NXFX03MempoolFeatures objects.

In production, raw pending swaps arrive from a websocket/subscription to a
mempool provider (e.g., Alchemy pending txs, Blocknative). The aggregator
buffers them per token and computes rolling window snapshots.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "nxfx-shared" / "src"))

from nxfx03_mempool_features import (
    MempoolAnomalies,
    MempoolDerivedFlags,
    MempoolSmartFlow,
    NXFX03MempoolFeatures,
)

logger = logging.getLogger("nxfx03")


# ---- Configuration ----

@dataclass
class MempoolConfig:
    """Tunable thresholds for mempool feature computation."""

    window_seconds: int = 10

    # Tiny swap threshold
    tiny_swap_threshold_usd: float = 50.0

    # Strong signal thresholds (from scoring_policy.yaml)
    min_sm_pending_buy_usd: float = 500.0
    min_sm_pending_sell_usd: float = 500.0
    min_whale_pending_buy_usd: float = 2000.0

    # Anomaly density threshold
    high_tiny_swap_density: float = 0.50

    # Fee urgency high multiplier (relative to median)
    fee_urgency_high_multiplier: float = 2.0


# ---- Pending swap event ----

@dataclass
class PendingSwap:
    """A decoded pending swap from the mempool."""
    tx_hash: str
    sender: str
    token_address: str
    direction: str          # "buy" or "sell"
    amount_usd: float
    priority_fee_gwei: float = 0.0
    sender_tier: str = "UNKNOWN"   # WalletTier string
    is_new_address: bool = False
    timestamp: float = field(default_factory=time.time)


# ---- Aggregator ----

class MempoolFeatureAggregator:
    """Buffers pending swaps per token and computes rolling feature snapshots.

    Thread-safe for single-writer (one event loop adding swaps) but not
    for concurrent writers. Production should use asyncio locks if needed.
    """

    def __init__(self, config: MempoolConfig | None = None) -> None:
        self.config = config or MempoolConfig()
        # token_address → list of PendingSwap
        self._buffers: dict[str, list[PendingSwap]] = defaultdict(list)
        # token_address → set (tracked tokens)
        self._tracked: set[str] = set()

    def register_token(self, token_address: str) -> None:
        """Start tracking mempool activity for a token."""
        addr = token_address.lower()
        self._tracked.add(addr)
        logger.debug("NXFX03: Tracking token %s", addr)

    def unregister_token(self, token_address: str) -> None:
        """Stop tracking a token and clear its buffer."""
        addr = token_address.lower()
        self._tracked.discard(addr)
        self._buffers.pop(addr, None)

    def add_pending_swap(self, swap: PendingSwap) -> None:
        """Ingest a decoded pending swap event."""
        addr = swap.token_address.lower()
        if addr not in self._tracked:
            return
        self._buffers[addr].append(swap)

    def compute_snapshot(
        self, token_address: str
    ) -> NXFX03MempoolFeatures | None:
        """Compute current mempool features for a tracked token.

        Prunes events outside the rolling window, aggregates, and returns
        a Contract D payload. Returns None if token is not tracked.
        """
        addr = token_address.lower()
        if addr not in self._tracked:
            return None

        cfg = self.config
        now = time.time()
        cutoff = now - cfg.window_seconds

        # Prune stale events
        buf = [s for s in self._buffers.get(addr, []) if s.timestamp >= cutoff]
        self._buffers[addr] = buf

        # ---- Aggregate smart-money flows ----
        sm_tiers = {"TIER_1_WHALE", "TIER_2_SMART_MONEY"}

        sm_buys = [s for s in buf if s.sender_tier in sm_tiers and s.direction == "buy"]
        sm_sells = [s for s in buf if s.sender_tier in sm_tiers and s.direction == "sell"]
        all_buys = [s for s in buf if s.direction == "buy"]
        all_sells = [s for s in buf if s.direction == "sell"]

        sm_buy_vol = sum(s.amount_usd for s in sm_buys)
        sm_sell_vol = sum(s.amount_usd for s in sm_sells)
        total_buy_vol = sum(s.amount_usd for s in all_buys)
        total_sell_vol = sum(s.amount_usd for s in all_sells)

        sm_buy_ratio = sm_buy_vol / total_buy_vol if total_buy_vol > 0 else 0.0
        sm_sell_ratio = sm_sell_vol / total_sell_vol if total_sell_vol > 0 else 0.0

        sm_buy_fees = [s.priority_fee_gwei for s in sm_buys] or [0.0]
        sm_sell_fees = [s.priority_fee_gwei for s in sm_sells] or [0.0]

        smart_flow = MempoolSmartFlow(
            pending_smart_buy_volume=round(sm_buy_vol, 2),
            pending_smart_sell_volume=round(sm_sell_vol, 2),
            pending_smart_buy_ratio=round(sm_buy_ratio, 4),
            pending_smart_sell_ratio=round(sm_sell_ratio, 4),
            pending_smart_buy_count=len(sm_buys),
            pending_smart_sell_count=len(sm_sells),
            pending_smart_buy_fee_urgency_max=round(max(sm_buy_fees), 4),
            pending_smart_sell_fee_urgency_max=round(max(sm_sell_fees), 4),
        )

        # ---- Anomalies ----
        tiny_swaps = [s for s in buf if s.amount_usd < cfg.tiny_swap_threshold_usd]
        total_pending = len(buf)
        tiny_density = len(tiny_swaps) / total_pending if total_pending > 0 else 0.0
        new_addr_tiny = sum(1 for s in tiny_swaps if s.is_new_address)

        anomalies = MempoolAnomalies(
            tiny_swap_count=len(tiny_swaps),
            total_pending_swap_count=total_pending,
            tiny_swap_density=round(tiny_density, 4),
            new_addr_tiny_swap_count=new_addr_tiny,
        )

        # ---- Derived flags ----
        has_strong_buy = (
            sm_buy_vol >= cfg.min_sm_pending_buy_usd
            and len(sm_buys) >= 2
        )
        has_strong_sell = (
            sm_sell_vol >= cfg.min_sm_pending_sell_usd
            and len(sm_sells) >= 2
        )
        high_density = tiny_density >= cfg.high_tiny_swap_density

        derived = MempoolDerivedFlags(
            has_strong_pending_smart_buy=has_strong_buy,
            has_strong_pending_smart_sell=has_strong_sell,
            high_tiny_swap_density=high_density,
        )

        return NXFX03MempoolFeatures(
            token_address=addr,
            chain="base",
            window_seconds=cfg.window_seconds,
            timestamp=datetime.now(timezone.utc),
            mempool_smart_flow=smart_flow,
            mempool_anomalies=anomalies,
            derived_flags=derived,
        )
