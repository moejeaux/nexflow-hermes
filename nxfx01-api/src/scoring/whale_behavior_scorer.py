"""Whale Behavior Score — captures what whales are doing (not just presence).

v2.1: Incorporates mempool pending whale flow, z-score normalization for net
flow, and pending bias signal for early distribution/commitment detection.

Analyzes net flows, accumulation trends, and price-contextual behavior
(buying dips vs selling rips) to distinguish conviction from exit liquidity.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import yaml

from src import db

logger = logging.getLogger("nxfx01.scoring.whale_behavior")

_policy: dict | None = None


def _load_policy() -> dict:
    global _policy
    if _policy is None:
        p = Path(__file__).parent.parent.parent / "config" / "scoring_policy.yaml"
        with open(p) as f:
            _policy = yaml.safe_load(f)
    return _policy


def _get_cfg() -> dict:
    return _load_policy().get("sub_scores", {}).get("whale_behavior", {})


def _compute_z_score(value: float, baseline_mean: float, baseline_std: float) -> float:
    """Z-score of value against a baseline. Returns 0 if std is 0."""
    if baseline_std <= 0:
        return 0.0
    return (value - baseline_mean) / baseline_std


async def compute(
    launch_id: str,
    wallets_data: dict[str, dict],
    price_snapshots: list[dict] | None = None,
    mempool_snapshot: dict[str, Any] | None = None,
    whale_flow_baseline: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute WhaleBehaviorScore and detail fields.

    Args:
        launch_id: The launch UUID.
        wallets_data: Normalized FIFO wallets dict {addr: data}.
        price_snapshots: Optional list of {timestamp, price} dicts for
                        price-contextual behavior analysis.
        mempool_snapshot: Latest mempool features for this token (v2.1).
        whale_flow_baseline: {mean, std} for z-score computation (v2.1).

    Returns dict with:
        score (0-100), whale_net_flow_tokens, whale_accumulation_trend,
        whale_buys_on_dips_ratio, whale_sells_in_rips_ratio,
        whale_net_flow_z, whale_pending_bias, has_data (bool).
    """
    cfg = _get_cfg()
    weights = cfg.get("weights", {})

    result: dict[str, Any] = {
        "score": 0,
        "whale_net_flow_tokens": 0.0,
        "whale_accumulation_trend": 0.0,
        "whale_buys_on_dips_ratio": 0.0,
        "whale_sells_in_rips_ratio": 0.0,
        "whale_net_flow_z": 0.0,
        "whale_pending_bias": 0.0,
        "has_data": False,
    }

    if not wallets_data:
        return result

    # Identify whale wallets from DB
    whale_addrs: list[str] = []
    for addr in wallets_data:
        row = await db.fetchrow(
            "SELECT wallet_tier FROM wallets WHERE wallet = $1",
            addr.lower(),
        )
        if row and row["wallet_tier"] == "TIER_1_WHALE":
            whale_addrs.append(addr.lower())

    if not whale_addrs:
        # No whales detected — neutral score
        result["score"] = 50
        return result

    result["has_data"] = True

    # -- Net flow: sum of (final_balance - initial_balance) for whales --
    total_net = 0.0
    positions: list[float] = []
    for addr in whale_addrs:
        data = wallets_data.get(addr, {})
        initial = int(data.get("initial_balance_raw", "0") or "0")
        final = int(data.get("final_balance_raw", "0") or "0")
        net = final - initial
        total_net += net
        positions.append(net)

    result["whale_net_flow_tokens"] = total_net

    # -- Z-score of net flow vs baseline --
    baseline = whale_flow_baseline or {}
    baseline_mean = baseline.get("mean", 0.0)
    baseline_std = baseline.get("std", 1.0)
    result["whale_net_flow_z"] = round(
        _compute_z_score(total_net, baseline_mean, baseline_std), 4
    )

    # -- Accumulation trend (simple: fraction of whales with net positive) --
    if positions:
        accumulators = sum(1 for p in positions if p > 0)
        result["whale_accumulation_trend"] = accumulators / len(positions)
    else:
        result["whale_accumulation_trend"] = 0.0

    # -- Price-contextual behavior --
    buys_on_dips = 0
    total_buys = 0
    sells_in_rips = 0
    total_sells = 0

    if price_snapshots and len(price_snapshots) >= 3:
        prices = [s.get("price", 0) for s in price_snapshots]
        avg_price = sum(prices) / len(prices) if prices else 0
        low_threshold = avg_price * 0.9   # within 10% of bottom
        high_threshold = avg_price * 1.1  # within 10% of top

        for addr in whale_addrs:
            data = wallets_data.get(addr, {})
            txs = data.get("transactions", [])
            for tx in txs if isinstance(txs, list) else []:
                tx_price = tx.get("price_at_time", avg_price)
                if tx.get("direction") == "buy":
                    total_buys += 1
                    if tx_price <= low_threshold:
                        buys_on_dips += 1
                elif tx.get("direction") == "sell":
                    total_sells += 1
                    if tx_price >= high_threshold:
                        sells_in_rips += 1

    result["whale_buys_on_dips_ratio"] = buys_on_dips / total_buys if total_buys > 0 else 0.0
    result["whale_sells_in_rips_ratio"] = sells_in_rips / total_sells if total_sells > 0 else 0.0

    # -- v2.1: Mempool pending whale bias --
    # Positive = net pending buys, Negative = net pending sells
    pending_bias = 0.0
    mp = mempool_snapshot or {}
    if mp:
        whale_buy_vol = mp.get("pending_whale_buy_volume", 0) or 0
        whale_sell_vol = mp.get("pending_whale_sell_volume", 0) or 0
        total_pending = whale_buy_vol + whale_sell_vol
        if total_pending > 0:
            pending_bias = (whale_buy_vol - whale_sell_vol) / total_pending
        has_strong_whale_sell = mp.get("has_strong_pending_whale_sell", False)
        if has_strong_whale_sell:
            pending_bias = min(pending_bias, -0.5)
    result["whale_pending_bias"] = round(pending_bias, 4)

    # -- Compute score from components --

    # 1. Net flow direction (positive = accumulating = good)
    if total_net > 0:
        flow_score = min(100, 60 + (result["whale_accumulation_trend"] * 40))
    elif total_net == 0:
        flow_score = 50
    else:
        # Distribution — bad signal
        flow_score = max(0, 40 - abs(result["whale_accumulation_trend"]) * 40)

    # 2. Accumulation trend (0-100)
    trend_score = result["whale_accumulation_trend"] * 100

    # 3. Buys on dips (higher = conviction = good)
    dip_score = min(100, result["whale_buys_on_dips_ratio"] * 200)

    # 4. Sells in rips (higher = exit liquidity farming = bad; invert)
    rip_penalty = result["whale_sells_in_rips_ratio"]
    rip_score = max(0, 100 - rip_penalty * 200)

    # 5. v2.1: Pending bias component (0-100)
    # Maps [-1, +1] bias to [0, 100]
    pending_score = max(0, min(100, (pending_bias + 1) * 50))

    raw_score = (
        flow_score * weights.get("net_flow_direction", 0.25)
        + trend_score * weights.get("accumulation_trend", 0.20)
        + dip_score * weights.get("buys_on_dips_ratio", 0.20)
        + rip_score * weights.get("sells_in_rips_ratio", 0.15)
        + pending_score * weights.get("mempool_pending_bias", 0.10)
    )

    # v2.1: Penalize if strong pending whale sells concurrent with realized distribution
    if mp.get("has_strong_pending_whale_sell", False) and total_net < 0:
        raw_score = min(raw_score, 25)
        logger.info("Whale score hard-capped: pending sell + realized distribution for %s", launch_id)

    result["score"] = max(0, min(100, round(raw_score)))
    return result
