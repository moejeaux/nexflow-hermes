"""Mempool Watcher — pre-block signal layer for tracked token launches.

Subscribes to pending transactions via WebSocket, decodes swap intents,
labels actors (SMART_MONEY, WHALE, etc.), and produces rolling feature
snapshots per tracked token every few seconds.

v2.1: New pipeline stage feeding into SmartMoney/Whale scorers and DeRisk engine.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src import db

logger = logging.getLogger("nxfx01.workers.mempool_watcher")

# Known DEX router addresses on Base (Uniswap V2/V3, Aerodrome, etc.)
KNOWN_ROUTERS: set[str] = {
    "0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24",  # Uniswap V2 Router (Base)
    "0x2626664c2603336e57b271c5c0b26f421741e481",  # Uniswap Universal Router
    "0xcf77a3ba9a5ca399b7c97c74d54e5b1beb874e43",  # Aerodrome Router
    "0x420dd381b31aef6683db6b902084cb0ffece40da",  # Aerodrome V2 Router
}

# Swap function selectors (first 4 bytes of calldata)
SWAP_SELECTORS: dict[str, str] = {
    "0x38ed1739": "swapExactTokensForTokens",
    "0x8803dbee": "swapTokensForExactTokens",
    "0x7ff36ab5": "swapExactETHForTokens",
    "0x4a25d94a": "swapTokensForExactETH",
    "0xfb3bdb41": "swapETHForExactTokens",
    "0x18cbafe5": "swapExactTokensForETH",
    "0x5ae401dc": "multicall",  # Uniswap multicall
    "0x04e45aaf": "exactInputSingle",  # V3
    "0xb858183f": "exactInput",  # V3
    "0x24856bc3": "execute",  # Universal Router
}

# Feature snapshot rolling window
SNAPSHOT_WINDOW_SECONDS = 15
# Minimum notional to not be classified as "tiny"
TINY_SWAP_THRESHOLD_USD = 50.0
# Fee urgency: pending tx priority fee as percentile of recent base fees
FEE_URGENCY_HIGH_PERCENTILE = 80.0


@dataclass
class PendingSwap:
    """Decoded pending swap from mempool."""
    tx_hash: str
    sender: str
    token_address: str
    direction: str  # "buy" or "sell"
    amount_token: float
    amount_usd_estimate: float
    priority_fee_gwei: float
    sender_label: str  # SMART_MONEY, WHALE, RETAIL, FLAGGED, UNKNOWN
    sender_tier: str   # TIER_1_WHALE, TIER_2_SMART_MONEY, etc.
    is_tiny: bool
    router: str
    method: str
    seen_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class MempoolSnapshot:
    """Aggregated mempool features for a single token at a point in time."""
    token_address: str
    launch_id: str | None
    snapshot_at: datetime

    # Smart-money pending flow
    pending_smart_buy_volume: float = 0.0
    pending_smart_sell_volume: float = 0.0
    pending_smart_buy_ratio: float = 0.0
    pending_smart_sell_ratio: float = 0.0
    pending_smart_buy_count: int = 0
    pending_smart_sell_count: int = 0
    pending_smart_buy_fee_urgency_max: float = 0.0
    pending_smart_sell_fee_urgency_max: float = 0.0

    # Whale pending flow
    pending_whale_buy_volume: float = 0.0
    pending_whale_sell_volume: float = 0.0
    pending_whale_buy_count: int = 0
    pending_whale_sell_count: int = 0

    # Anomaly density
    tiny_swap_count: int = 0
    total_pending_swap_count: int = 0
    tiny_swap_density: float = 0.0
    new_addr_tiny_swap_count: int = 0

    # Derived flags
    has_strong_pending_smart_buy: bool = False
    has_strong_pending_smart_sell: bool = False
    has_strong_pending_whale_buy: bool = False
    has_strong_pending_whale_sell: bool = False
    high_tiny_swap_density: bool = False

    pool_liquidity_usd: float = 0.0


class MempoolFeatureAggregator:
    """Aggregates pending swaps into per-token feature snapshots."""

    def __init__(self) -> None:
        # token_address → list of PendingSwap within the rolling window
        self._pending: dict[str, list[PendingSwap]] = defaultdict(list)
        # token_address → launch_id
        self._tracked_tokens: dict[str, str] = {}
        # token_address → current pool liquidity USD
        self._pool_liquidity: dict[str, float] = {}
        # Recent base fee samples for urgency percentile
        self._recent_base_fees: list[float] = []

    def register_token(
        self, token_address: str, launch_id: str, pool_liquidity_usd: float
    ) -> None:
        addr = token_address.lower()
        self._tracked_tokens[addr] = launch_id
        self._pool_liquidity[addr] = pool_liquidity_usd

    def unregister_token(self, token_address: str) -> None:
        addr = token_address.lower()
        self._tracked_tokens.pop(addr, None)
        self._pending.pop(addr, None)
        self._pool_liquidity.pop(addr, None)

    def update_base_fee(self, base_fee_gwei: float) -> None:
        self._recent_base_fees.append(base_fee_gwei)
        # Keep last 100 samples
        if len(self._recent_base_fees) > 100:
            self._recent_base_fees = self._recent_base_fees[-100:]

    def _compute_fee_urgency(self, priority_fee: float) -> float:
        """Compute fee urgency as percentile of priority fee vs recent base fees."""
        if not self._recent_base_fees:
            return 50.0
        below = sum(1 for f in self._recent_base_fees if priority_fee > f)
        return (below / len(self._recent_base_fees)) * 100

    def add_pending_swap(self, swap: PendingSwap) -> None:
        addr = swap.token_address.lower()
        if addr not in self._tracked_tokens:
            return
        self._pending[addr].append(swap)

    def _prune_old(self, token: str, now: datetime) -> None:
        cutoff = now.timestamp() - SNAPSHOT_WINDOW_SECONDS
        self._pending[token] = [
            s for s in self._pending[token] if s.seen_at.timestamp() > cutoff
        ]

    def compute_snapshot(self, token_address: str) -> MempoolSnapshot | None:
        addr = token_address.lower()
        if addr not in self._tracked_tokens:
            return None

        now = datetime.now(timezone.utc)
        self._prune_old(addr, now)

        swaps = self._pending.get(addr, [])
        pool_liq = self._pool_liquidity.get(addr, 0.0)

        snap = MempoolSnapshot(
            token_address=addr,
            launch_id=self._tracked_tokens.get(addr),
            snapshot_at=now,
            pool_liquidity_usd=pool_liq,
        )

        snap.total_pending_swap_count = len(swaps)

        for s in swaps:
            is_smart = s.sender_tier == "TIER_2_SMART_MONEY"
            is_whale = s.sender_tier == "TIER_1_WHALE"
            urgency = self._compute_fee_urgency(s.priority_fee_gwei)

            if s.direction == "buy":
                if is_smart:
                    snap.pending_smart_buy_volume += s.amount_usd_estimate
                    snap.pending_smart_buy_count += 1
                    snap.pending_smart_buy_fee_urgency_max = max(
                        snap.pending_smart_buy_fee_urgency_max, urgency
                    )
                if is_whale:
                    snap.pending_whale_buy_volume += s.amount_usd_estimate
                    snap.pending_whale_buy_count += 1
            elif s.direction == "sell":
                if is_smart:
                    snap.pending_smart_sell_volume += s.amount_usd_estimate
                    snap.pending_smart_sell_count += 1
                    snap.pending_smart_sell_fee_urgency_max = max(
                        snap.pending_smart_sell_fee_urgency_max, urgency
                    )
                if is_whale:
                    snap.pending_whale_sell_volume += s.amount_usd_estimate
                    snap.pending_whale_sell_count += 1

            if s.is_tiny:
                snap.tiny_swap_count += 1
                # Check if sender is new (no wallet row in our DB — approximation)
                # Actual labeling would have been done in add_pending_swap caller

        # Compute ratios
        if pool_liq > 0:
            snap.pending_smart_buy_ratio = snap.pending_smart_buy_volume / pool_liq
            snap.pending_smart_sell_ratio = snap.pending_smart_sell_volume / pool_liq

        # Tiny swap density
        if snap.total_pending_swap_count > 0:
            snap.tiny_swap_density = snap.tiny_swap_count / snap.total_pending_swap_count

        # Derive flags
        snap.has_strong_pending_smart_buy = (
            snap.pending_smart_buy_ratio > 0.02
            and snap.pending_smart_buy_count >= 2
            and snap.pending_smart_buy_fee_urgency_max > FEE_URGENCY_HIGH_PERCENTILE
        )
        snap.has_strong_pending_smart_sell = (
            snap.pending_smart_sell_ratio > 0.01
            and snap.pending_smart_sell_count >= 2
            and snap.pending_smart_sell_fee_urgency_max > FEE_URGENCY_HIGH_PERCENTILE
        )
        snap.has_strong_pending_whale_buy = (
            snap.pending_whale_buy_count >= 1
            and snap.pending_whale_buy_volume > 5000  # >$5K whale pending buy
        )
        snap.has_strong_pending_whale_sell = (
            snap.pending_whale_sell_count >= 1
            and snap.pending_whale_sell_volume > 5000
        )
        snap.high_tiny_swap_density = snap.tiny_swap_density > 0.60

        return snap


def decode_pending_tx(
    tx: dict,
    wallet_labels: dict[str, tuple[str, str]],
    eth_price_usd: float = 2500.0,
) -> PendingSwap | None:
    """Decode a raw pending transaction into a PendingSwap if it's a swap.

    Args:
        tx: Raw pending tx dict with: hash, from, to, input, value,
            maxPriorityFeePerGas, gasPrice, etc.
        wallet_labels: {addr_lower: (label, tier)} preloaded from wallets table.
        eth_price_usd: Current ETH price for USD estimates.

    Returns PendingSwap or None if tx is not a recognized swap.
    """
    to_addr = (tx.get("to") or "").lower()
    if to_addr not in KNOWN_ROUTERS:
        return None

    calldata = tx.get("input") or tx.get("data") or ""
    if len(calldata) < 10:
        return None

    selector = calldata[:10].lower()
    method = SWAP_SELECTORS.get(selector)
    if not method:
        return None

    sender = (tx.get("from") or "").lower()
    tx_hash = tx.get("hash", "")

    # Determine direction from method name heuristic
    # ETH→Token = buy, Token→ETH = sell, Token→Token = infer from value
    value_wei = int(tx.get("value", "0") or "0", 16) if isinstance(tx.get("value"), str) else int(tx.get("value", 0))
    value_eth = value_wei / 1e18
    amount_usd = value_eth * eth_price_usd

    if "ForTokens" in method or "exactInput" in method or "execute" in method:
        direction = "buy" if value_wei > 0 else "sell"
    elif "ForETH" in method:
        direction = "sell"
    else:
        direction = "buy" if value_wei > 0 else "sell"

    # Priority fee
    max_prio = tx.get("maxPriorityFeePerGas")
    gas_price = tx.get("gasPrice")
    if max_prio:
        prio_gwei = int(max_prio, 16) / 1e9 if isinstance(max_prio, str) else max_prio / 1e9
    elif gas_price:
        prio_gwei = int(gas_price, 16) / 1e9 if isinstance(gas_price, str) else gas_price / 1e9
    else:
        prio_gwei = 0.0

    # Label sender
    label_info = wallet_labels.get(sender, ("UNKNOWN", "UNKNOWN"))
    sender_label, sender_tier = label_info

    is_tiny = amount_usd < TINY_SWAP_THRESHOLD_USD

    # Token address extraction from calldata is complex (ABI-dependent).
    # For the router-level watcher, the token address must be resolved by
    # matching against tracked pools. The caller should set token_address
    # after matching the pool's token against tracked launches.
    return PendingSwap(
        tx_hash=tx_hash,
        sender=sender,
        token_address="",  # set by caller after pool matching
        direction=direction,
        amount_token=0.0,  # set by caller after decoding
        amount_usd_estimate=amount_usd,
        priority_fee_gwei=prio_gwei,
        sender_label=sender_label,
        sender_tier=sender_tier,
        is_tiny=is_tiny,
        router=to_addr,
        method=method,
    )


async def _load_tracked_launches() -> list[dict]:
    """Load launches that are FAST/WAIT and less than 2h old."""
    rows = await db.fetch(
        """
        SELECT launch_id, token_address, pair_address, lp_usd
        FROM launches
        WHERE action_final IN ('FAST', 'WAIT')
          AND detected_at > now() - interval '2 hours'
          AND position_action NOT IN ('HARD_EXIT')
        ORDER BY detected_at DESC
        LIMIT 50
        """
    )
    return [dict(r) for r in rows]


async def _load_wallet_labels() -> dict[str, tuple[str, str]]:
    """Load known wallet labels in bulk for fast lookup."""
    rows = await db.fetch(
        """
        SELECT wallet, wallet_tier
        FROM wallets
        WHERE wallet_tier IN ('TIER_1_WHALE', 'TIER_2_SMART_MONEY', 'TIER_4_FLAGGED')
        """
    )
    labels = {}
    for r in rows:
        tier = r["wallet_tier"]
        if tier == "TIER_2_SMART_MONEY":
            label = "SMART_MONEY"
        elif tier == "TIER_1_WHALE":
            label = "WHALE"
        elif tier == "TIER_4_FLAGGED":
            label = "FLAGGED"
        else:
            label = "UNKNOWN"
        labels[r["wallet"].lower()] = (label, tier)
    return labels


async def persist_snapshot(snap: MempoolSnapshot) -> None:
    """Persist a mempool feature snapshot to DB."""
    if not snap.launch_id:
        return

    # Insert snapshot row
    await db.execute(
        """
        INSERT INTO mempool_features (
            launch_id, token_address, snapshot_at,
            pending_smart_buy_volume, pending_smart_sell_volume,
            pending_smart_buy_ratio, pending_smart_sell_ratio,
            pending_smart_buy_count, pending_smart_sell_count,
            pending_smart_buy_fee_urgency_max, pending_smart_sell_fee_urgency_max,
            pending_whale_buy_volume, pending_whale_sell_volume,
            pending_whale_buy_count, pending_whale_sell_count,
            tiny_swap_count, total_pending_swap_count,
            tiny_swap_density, new_addr_tiny_swap_count,
            has_strong_pending_smart_buy, has_strong_pending_smart_sell,
            has_strong_pending_whale_buy, has_strong_pending_whale_sell,
            high_tiny_swap_density,
            pool_liquidity_usd
        ) VALUES (
            $1, $2, $3,
            $4, $5, $6, $7, $8, $9, $10, $11,
            $12, $13, $14, $15,
            $16, $17, $18, $19,
            $20, $21, $22, $23, $24,
            $25
        )
        """,
        snap.launch_id, snap.token_address, snap.snapshot_at,
        snap.pending_smart_buy_volume, snap.pending_smart_sell_volume,
        snap.pending_smart_buy_ratio, snap.pending_smart_sell_ratio,
        snap.pending_smart_buy_count, snap.pending_smart_sell_count,
        snap.pending_smart_buy_fee_urgency_max, snap.pending_smart_sell_fee_urgency_max,
        snap.pending_whale_buy_volume, snap.pending_whale_sell_volume,
        snap.pending_whale_buy_count, snap.pending_whale_sell_count,
        snap.tiny_swap_count, snap.total_pending_swap_count,
        snap.tiny_swap_density, snap.new_addr_tiny_swap_count,
        snap.has_strong_pending_smart_buy, snap.has_strong_pending_smart_sell,
        snap.has_strong_pending_whale_buy, snap.has_strong_pending_whale_sell,
        snap.high_tiny_swap_density,
        snap.pool_liquidity_usd,
    )

    # Update denormalized columns on launches
    flags = {
        "has_strong_pending_smart_buy": snap.has_strong_pending_smart_buy,
        "has_strong_pending_smart_sell": snap.has_strong_pending_smart_sell,
        "has_strong_pending_whale_buy": snap.has_strong_pending_whale_buy,
        "has_strong_pending_whale_sell": snap.has_strong_pending_whale_sell,
        "high_tiny_swap_density": snap.high_tiny_swap_density,
    }

    await db.execute(
        """
        UPDATE launches SET
            mempool_smart_buy_ratio   = $1,
            mempool_smart_sell_ratio  = $2,
            mempool_whale_buy_count   = $3,
            mempool_whale_sell_count  = $4,
            mempool_tiny_swap_density = $5,
            mempool_flags             = $6::jsonb,
            mempool_updated_at        = now()
        WHERE launch_id = $7
        """,
        snap.pending_smart_buy_ratio,
        snap.pending_smart_sell_ratio,
        snap.pending_whale_buy_count,
        snap.pending_whale_sell_count,
        snap.tiny_swap_density,
        json.dumps(flags),
        snap.launch_id,
    )


async def get_latest_snapshot(launch_id: str) -> dict[str, Any] | None:
    """Get the most recent mempool snapshot for a launch."""
    row = await db.fetchrow(
        """
        SELECT * FROM mempool_features
        WHERE launch_id = $1
        ORDER BY snapshot_at DESC
        LIMIT 1
        """,
        launch_id,
    )
    return dict(row) if row else None


async def run_snapshot_cycle(aggregator: MempoolFeatureAggregator) -> dict:
    """Run one snapshot cycle: compute and persist snapshots for all tracked tokens.

    Called every SNAPSHOT_WINDOW_SECONDS by the scheduler.
    """
    tracked = await _load_tracked_launches()
    wallet_labels = await _load_wallet_labels()

    # Register/update tracked tokens
    for launch in tracked:
        aggregator.register_token(
            token_address=launch["token_address"],
            launch_id=str(launch["launch_id"]),
            pool_liquidity_usd=float(launch.get("lp_usd") or 0),
        )

    persisted = 0
    for launch in tracked:
        snap = aggregator.compute_snapshot(launch["token_address"])
        if snap and snap.total_pending_swap_count > 0:
            await persist_snapshot(snap)
            persisted += 1

    return {"tracked": len(tracked), "snapshots_persisted": persisted}
