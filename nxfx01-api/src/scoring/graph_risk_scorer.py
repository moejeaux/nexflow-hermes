"""Graph Risk Score — detects manipulative transfer patterns in early token graph.

Constructs a directed graph over early token transfers and computes:
- Degree centralization (star-likeness)
- Loop fraction (self-trading / wash cycles)
- LP owner concentration
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from src import db

logger = logging.getLogger("nxfx01.scoring.graph_risk")

_policy: dict | None = None


def _load_policy() -> dict:
    global _policy
    if _policy is None:
        p = Path(__file__).parent.parent.parent / "config" / "scoring_policy.yaml"
        with open(p) as f:
            _policy = yaml.safe_load(f)
    return _policy


def _get_cfg() -> dict:
    return _load_policy().get("sub_scores", {}).get("graph_risk", {})


def _compute_degree_centralization(transfers: list[dict]) -> float:
    """Compute degree centralization: how much volume flows through a small core.

    Returns 0.0 (decentralized) to 1.0 (all volume through one node).
    Uses Freeman's degree centralization adapted for weighted directed graphs.
    """
    if not transfers:
        return 0.0

    # Count volume per address (in + out)
    volume_by_addr: dict[str, float] = defaultdict(float)
    total_volume = 0.0

    for tx in transfers:
        amount = float(tx.get("amount", 0) or 0)
        from_addr = (tx.get("from", "") or "").lower()
        to_addr = (tx.get("to", "") or "").lower()
        if from_addr:
            volume_by_addr[from_addr] += amount
        if to_addr:
            volume_by_addr[to_addr] += amount
        total_volume += amount

    if total_volume == 0 or len(volume_by_addr) < 2:
        return 0.0

    # Top 5 addresses by volume share
    sorted_vols = sorted(volume_by_addr.values(), reverse=True)
    top5_share = sum(sorted_vols[:5]) / (total_volume * 2)  # *2 because counted in+out

    return min(1.0, top5_share)


def _compute_loop_fraction(transfers: list[dict]) -> float:
    """Detect self-trading / short loops (A→B→A or A→B→C→A within short time windows).

    Returns fraction of total volume involved in loops (0.0 to 1.0).
    """
    if not transfers:
        return 0.0

    # Build adjacency with timestamps
    edges: dict[tuple[str, str], float] = defaultdict(float)
    total_volume = 0.0

    for tx in transfers:
        amount = float(tx.get("amount", 0) or 0)
        from_addr = (tx.get("from", "") or "").lower()
        to_addr = (tx.get("to", "") or "").lower()
        if from_addr and to_addr:
            edges[(from_addr, to_addr)] += amount
            total_volume += amount

    if total_volume == 0:
        return 0.0

    loop_volume = 0.0

    # Direct loops: A→B and B→A both exist
    seen = set()
    for (a, b), vol_ab in edges.items():
        if (a, b) in seen:
            continue
        vol_ba = edges.get((b, a), 0)
        if vol_ba > 0:
            loop_volume += min(vol_ab, vol_ba) * 2
            seen.add((a, b))
            seen.add((b, a))

    return min(1.0, loop_volume / total_volume)


def _compute_lp_owner_concentration(
    lp_providers: list[dict],
    deployer_address: str | None,
) -> float:
    """Fraction of LP controlled by deployer or insider addresses.

    Returns 0.0 (distributed) to 1.0 (all LP from deployer/insiders).
    """
    if not lp_providers:
        return 0.0

    total_lp = 0.0
    insider_lp = 0.0
    deployer_lower = (deployer_address or "").lower()

    for provider in lp_providers:
        addr = (provider.get("address", "") or "").lower()
        amount = float(provider.get("amount_usd", 0) or 0)
        total_lp += amount

        # Check if provider is deployer or flagged insider
        if addr == deployer_lower:
            insider_lp += amount
            continue

    if total_lp == 0:
        return 0.0

    return min(1.0, insider_lp / total_lp)


async def compute(
    launch_id: str,
    transfers: list[dict],
    lp_providers: list[dict] | None = None,
    deployer_address: str | None = None,
    lp_change_rate: float | None = None,
) -> dict[str, Any]:
    """Compute GraphRiskScore and detail fields.

    Args:
        launch_id: The launch UUID.
        transfers: List of early token transfers [{from, to, amount, timestamp}].
        lp_providers: List of LP providers [{address, amount_usd}].
        deployer_address: The deployer wallet address.
        lp_change_rate: Rate of LP additions/removals (negative = removal).

    Returns dict with:
        score (0-100, higher = MORE risk), degree_centralization,
        loop_fraction, lp_owner_concentration, lp_change_rate,
        hard_block (bool), has_data (bool).
    """
    cfg = _get_cfg()
    metrics_cfg = cfg.get("metrics", {})
    hard_block_cfg = cfg.get("hard_block_combo", {})

    result: dict[str, Any] = {
        "score": 0,
        "degree_centralization": 0.0,
        "loop_fraction": 0.0,
        "lp_owner_concentration": 0.0,
        "lp_change_rate": lp_change_rate or 0.0,
        "hard_block": False,
        "has_data": False,
    }

    if not transfers:
        # No transfer data — cannot assess graph risk, return moderate default
        result["score"] = 30  # moderate uncertainty risk
        return result

    result["has_data"] = True

    # Compute metrics
    centralization = _compute_degree_centralization(transfers)
    loop_frac = _compute_loop_fraction(transfers)
    lp_concentration = _compute_lp_owner_concentration(
        lp_providers or [], deployer_address
    )

    result["degree_centralization"] = round(centralization, 4)
    result["loop_fraction"] = round(loop_frac, 4)
    result["lp_owner_concentration"] = round(lp_concentration, 4)

    # Check hard block combo
    if (
        centralization > hard_block_cfg.get("centralization_above", 0.70)
        and loop_frac > hard_block_cfg.get("loop_fraction_above", 0.25)
        and lp_concentration > hard_block_cfg.get("lp_concentration_above", 0.80)
    ):
        result["hard_block"] = True
        result["score"] = 100
        return result

    # Compute weighted risk score
    cent_cfg = metrics_cfg.get("degree_centralization", {})
    loop_cfg = metrics_cfg.get("loop_fraction", {})
    lp_cfg = metrics_cfg.get("lp_owner_concentration", {})

    cent_thresh = cent_cfg.get("high_risk_threshold", 0.70)
    loop_thresh = loop_cfg.get("high_risk_threshold", 0.30)
    lp_thresh = lp_cfg.get("high_risk_threshold", 0.80)

    # Each metric: 0 → 0 risk, threshold → 100 risk
    cent_risk = min(100, (centralization / cent_thresh) * 100) if cent_thresh > 0 else 0
    loop_risk = min(100, (loop_frac / loop_thresh) * 100) if loop_thresh > 0 else 0
    lp_risk = min(100, (lp_concentration / lp_thresh) * 100) if lp_thresh > 0 else 0

    raw_score = (
        cent_risk * cent_cfg.get("weight", 0.35)
        + loop_risk * loop_cfg.get("weight", 0.35)
        + lp_risk * lp_cfg.get("weight", 0.30)
    )

    result["score"] = max(0, min(100, round(raw_score)))
    return result
