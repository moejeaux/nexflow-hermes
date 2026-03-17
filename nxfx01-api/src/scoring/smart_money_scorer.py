"""Smart Money Alignment Score — measures quality and conviction of smart-money
presence in the founding cohort of a token launch.

v2.1: Incorporates mempool pending flow (pre-block conviction), cluster
diversity, and net position over multiple horizons.

Inputs: FIFO wallet data, wallet tier DB lookups, mempool features snapshot.
Output: SmartMoneyAlignmentScore (0-100) + detail fields.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from src import db

logger = logging.getLogger("nxfx01.scoring.smart_money")

_policy: dict | None = None


def _load_policy() -> dict:
    global _policy
    if _policy is None:
        p = Path(__file__).parent.parent.parent / "config" / "scoring_policy.yaml"
        with open(p) as f:
            _policy = yaml.safe_load(f)
    return _policy


def _get_sm_config() -> dict:
    return _load_policy().get("sub_scores", {}).get("smart_money_alignment", {})


async def compute(
    launch_id: str,
    wallets_data: dict[str, dict],
    cohort_window_minutes: int | None = None,
    mempool_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute SmartMoneyAlignmentScore and detail fields.

    Args:
        launch_id: The launch UUID.
        wallets_data: Normalized FIFO wallets dict {addr: data}.
        cohort_window_minutes: Override for founding cohort window.
        mempool_snapshot: Latest mempool features for this token (v2.1).

    Returns dict with:
        score (0-100), founding_cohort_size, smart_money_count,
        smart_money_share, accumulation_ratio_30m, sm_cohort_exit_pct,
        median_sm_hold_minutes, sm_diversity_clusters,
        sm_net_position_30m, sm_pending_conviction, has_data (bool).
    """
    cfg = _get_sm_config()
    weights = cfg.get("weights", {})
    thresholds = cfg.get("thresholds", {})
    window = cohort_window_minutes or cfg.get("founding_cohort_window_minutes", 10)
    max_buyers = cfg.get("founding_cohort_max_buyers", 100)
    min_sm = cfg.get("min_smart_money_for_signal", 2)

    result: dict[str, Any] = {
        "score": 0,
        "founding_cohort_size": 0,
        "smart_money_count": 0,
        "smart_money_share": 0.0,
        "accumulation_ratio_30m": 0.0,
        "sm_cohort_exit_pct": 0.0,
        "median_sm_hold_minutes": 0,
        "sm_diversity_clusters": 0,
        "sm_net_position_30m": 0.0,
        "sm_pending_conviction": 0.0,
        "has_data": False,
    }

    if not wallets_data:
        return result

    # Build founding cohort: first N buyers sorted by entry rank
    entries = []
    for addr, data in wallets_data.items():
        rank_pct = data.get("entry_rank_pct", 1.0)
        entries.append((addr.lower(), rank_pct, data))
    entries.sort(key=lambda x: x[1])
    cohort = entries[:max_buyers]

    result["founding_cohort_size"] = len(cohort)
    result["has_data"] = len(cohort) > 0

    if not cohort:
        return result

    # Look up wallet tiers for cohort
    sm_wallets: list[tuple[str, dict]] = []
    alpha_count = 0
    cluster_ids: set[str] = set()

    for addr, _, data in cohort:
        row = await db.fetchrow(
            "SELECT wallet_tier, alpha_cohort_flag, cluster_id FROM wallets WHERE wallet = $1",
            addr,
        )
        if not row:
            continue
        tier = row["wallet_tier"]
        if tier == "TIER_2_SMART_MONEY":
            sm_wallets.append((addr, data))
            if row["cluster_id"]:
                cluster_ids.add(row["cluster_id"])
        if row["alpha_cohort_flag"]:
            alpha_count += 1

    result["smart_money_count"] = len(sm_wallets)
    result["smart_money_share"] = len(sm_wallets) / len(cohort) if cohort else 0.0
    result["sm_diversity_clusters"] = len(cluster_ids)

    # -- Accumulation ratio at T+30m --
    # Fraction of SM wallets whose final_balance > initial_balance (still holding/adding)
    accumulators = 0
    exits = 0
    hold_durations: list[int] = []
    net_position_30m = 0.0

    for addr, data in sm_wallets:
        initial_bal = int(data.get("initial_balance_raw", "0") or "0")
        final_bal = int(data.get("final_balance_raw", "0") or "0")

        net_position_30m += final_bal - initial_bal

        if initial_bal <= 0:
            continue

        if final_bal >= initial_bal * 0.5:
            accumulators += 1
        else:
            exits += 1

        # Estimate hold duration from FIFO data (if available)
        hold_mins = data.get("estimated_hold_minutes")
        if hold_mins is not None:
            hold_durations.append(int(hold_mins))

    total_sm = accumulators + exits
    result["accumulation_ratio_30m"] = accumulators / total_sm if total_sm > 0 else 0.0
    result["sm_cohort_exit_pct"] = exits / total_sm if total_sm > 0 else 0.0
    result["median_sm_hold_minutes"] = (
        sorted(hold_durations)[len(hold_durations) // 2] if hold_durations else 0
    )
    result["sm_net_position_30m"] = net_position_30m

    # -- v2.1: Mempool pending conviction --
    # Combines realized behavior with pre-block signals
    pending_conviction = 0.0
    mp = mempool_snapshot or {}
    if mp:
        buy_ratio = mp.get("pending_smart_buy_ratio", 0) or 0
        sell_ratio = mp.get("pending_smart_sell_ratio", 0) or 0
        has_strong_buy = mp.get("has_strong_pending_smart_buy", False)
        has_strong_sell = mp.get("has_strong_pending_smart_sell", False)

        if has_strong_buy and not has_strong_sell:
            pending_conviction = min(1.0, 0.5 + buy_ratio * 10)
        elif has_strong_sell and not has_strong_buy:
            pending_conviction = max(-1.0, -0.5 - sell_ratio * 10)
        elif buy_ratio > sell_ratio:
            pending_conviction = min(1.0, (buy_ratio - sell_ratio) * 5)
        elif sell_ratio > buy_ratio:
            pending_conviction = max(-1.0, -(sell_ratio - buy_ratio) * 5)

    result["sm_pending_conviction"] = round(pending_conviction, 4)

    # -- Compute sub-components --
    w = weights

    # 1. Smart money share component (0-100)
    share_score = min(100, result["smart_money_share"] * 400)  # 25% share → 100

    # 2. Accumulation ratio component (0-100)
    acc_ratio = result["accumulation_ratio_30m"]
    strong_threshold = thresholds.get("accumulation_ratio_strong", 0.60)
    if acc_ratio >= strong_threshold:
        acc_score = 100
    else:
        acc_score = min(100, (acc_ratio / strong_threshold) * 100)

    # 3. Hold duration factor (0-100)
    med_hold = result["median_sm_hold_minutes"]
    hold_score = min(100, (med_hold / 120) * 100)  # cap at 120 min = 100

    # 4. Cohort exit penalty (inverted: high exits = low score)
    exit_pct = result["sm_cohort_exit_pct"]
    exit_score = max(0, 100 - exit_pct * 150)  # 67% exits → 0

    # 5. Alpha cohort bonus
    alpha_score = min(100, alpha_count * 40)  # 2-3 alpha wallets → near max

    # 6. v2.1: Cluster diversity bonus (0-100)
    # More distinct clusters = less chance of single-entity gaming
    diversity_score = min(100, result["sm_diversity_clusters"] * 30)

    # 7. v2.1: Mempool pending conviction (0-100)
    # Maps [-1, +1] conviction to [0, 100] score
    conviction_score = max(0, min(100, (pending_conviction + 1) * 50))

    # Weighted combination (v2.1 weights include diversity + pending)
    raw_score = (
        share_score * w.get("smart_money_share", 0.20)
        + acc_score * w.get("accumulation_ratio_30m", 0.20)
        + hold_score * w.get("median_hold_duration_factor", 0.15)
        + exit_score * w.get("cohort_exit_penalty", 0.15)
        + alpha_score * w.get("alpha_cohort_bonus", 0.08)
        + diversity_score * w.get("sm_diversity", 0.10)
        + conviction_score * w.get("mempool_pending_conviction", 0.12)
    )

    # If fewer than min_smart_money_for_signal, cap at 40 (not enough evidence)
    if len(sm_wallets) < min_sm:
        raw_score = min(raw_score, 40)

    # v2.1: If mempool shows strong smart-money selling, apply hard penalty
    if mp.get("has_strong_pending_smart_sell", False) and exit_pct > 0.3:
        raw_score = min(raw_score, 30)
        logger.info("SM score capped: strong pending sell + realized exits for %s", launch_id)

    result["score"] = max(0, min(100, round(raw_score)))
    return result
