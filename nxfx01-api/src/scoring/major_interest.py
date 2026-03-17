"""Major Interest Detector — first-class concept for genuine institutional/smart interest.

v2.1: Determines whether a launch has "major interest" — a composite signal that
all critical risk gates pass, smart-money/whale conviction is strong, liquidity
is real, and the mempool isn't dominated by wash-trading or bots.

major_interest_flag is a prerequisite for aggressive FAST sizing and is a key
metric in outcome tracking for the self-learning loop.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("nxfx01.scoring.major_interest")

_policy: dict | None = None


def _load_policy() -> dict:
    global _policy
    if _policy is None:
        p = Path(__file__).parent.parent.parent / "config" / "scoring_policy.yaml"
        with open(p) as f:
            _policy = yaml.safe_load(f)
    return _policy


def _get_cfg() -> dict:
    return _load_policy().get("major_interest", {})


def evaluate(
    smart_money_alignment: int,
    whale_behavior: int,
    liquidity_quality: int,
    rug_risk: int,
    graph_risk: int,
    social_quality: int,
    data_confidence: int,
    mempool_flags: dict[str, Any] | None = None,
    mempool_tiny_swap_density: float = 0.0,
    passes_hard_gates: bool = False,
    critical_missing: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate whether a launch has major interest.

    Args:
        smart_money_alignment: SM alignment score 0-100.
        whale_behavior: Whale behavior score 0-100.
        liquidity_quality: Liquidity quality score 0-100.
        rug_risk: Rug risk score 0-100 (higher = more dangerous).
        graph_risk: Graph risk score 0-100 (higher = more dangerous).
        social_quality: Social quality score 0-100.
        data_confidence: Data confidence score 0-100.
        mempool_flags: Dict of derived mempool flags.
        mempool_tiny_swap_density: Tiny swap density from mempool.
        passes_hard_gates: Whether all FAST hard gates passed.
        critical_missing: List of critical missing data groups.

    Returns dict with:
        major_interest_flag (bool), major_interest_score (0-100),
        detail (dict explaining each component's contribution),
        blockers (list of reasons if flag is False).
    """
    cfg = _get_cfg()
    thresholds = cfg.get("thresholds", {})

    result: dict[str, Any] = {
        "major_interest_flag": False,
        "major_interest_score": 0,
        "detail": {},
        "blockers": [],
    }

    mp = mempool_flags or {}
    crit_missing = critical_missing or []

    # -- Prerequisite: all FAST hard gates must pass --
    if not passes_hard_gates:
        result["blockers"].append("hard_gates_failed")

    # -- Prerequisite: no critical data missing --
    if crit_missing:
        result["blockers"].append(f"critical_data_missing:{','.join(crit_missing)}")

    # -- Risk gates (must be below thresholds) --
    max_rug = thresholds.get("max_rug_risk", 35)
    max_graph = thresholds.get("max_graph_risk", 45)
    if rug_risk > max_rug:
        result["blockers"].append(f"rug_risk={rug_risk}>{max_rug}")
    if graph_risk > max_graph:
        result["blockers"].append(f"graph_risk={graph_risk}>{max_graph}")

    # -- Positive signal gates (must exceed thresholds) --
    min_sm = thresholds.get("min_smart_money_alignment", 60)
    min_whale = thresholds.get("min_whale_behavior", 55)
    min_liq = thresholds.get("min_liquidity_quality", 60)
    min_data = thresholds.get("min_data_confidence", 70)

    if smart_money_alignment < min_sm:
        result["blockers"].append(f"sm_alignment={smart_money_alignment}<{min_sm}")
    if whale_behavior < min_whale:
        result["blockers"].append(f"whale_behavior={whale_behavior}<{min_whale}")
    if liquidity_quality < min_liq:
        result["blockers"].append(f"liquidity_quality={liquidity_quality}<{min_liq}")
    if data_confidence < min_data:
        result["blockers"].append(f"data_confidence={data_confidence}<{min_data}")

    # -- Mempool quality gate --
    max_tiny_density = thresholds.get("max_tiny_swap_density", 0.50)
    if mempool_tiny_swap_density > max_tiny_density:
        result["blockers"].append(f"tiny_swap_density={mempool_tiny_swap_density:.2f}>{max_tiny_density}")

    # -- Mempool sell pressure veto --
    if mp.get("has_strong_pending_smart_sell", False):
        result["blockers"].append("strong_pending_smart_sell")

    # -- Compute major interest score (0-100) regardless of flag --
    # Weighted composite of the positive signals, penalized by risks
    weights = cfg.get("score_weights", {})

    # Positive components (higher = better)
    sm_component = smart_money_alignment * weights.get("smart_money_alignment", 0.30)
    whale_component = whale_behavior * weights.get("whale_behavior", 0.15)
    liq_component = liquidity_quality * weights.get("liquidity_quality", 0.20)
    social_component = social_quality * weights.get("social_quality", 0.05)

    # Risk penalties (inverted: 100-risk → higher = safer)
    rug_component = (100 - rug_risk) * weights.get("rug_risk_safety", 0.15)
    graph_component = (100 - graph_risk) * weights.get("graph_risk_safety", 0.05)

    # Data confidence as quality gate
    data_component = data_confidence * weights.get("data_confidence", 0.10)

    raw_score = (
        sm_component + whale_component + liq_component + social_component
        + rug_component + graph_component + data_component
    )

    # Mempool bonus: strong pending smart buys with no sell pressure
    if mp.get("has_strong_pending_smart_buy", False) and not mp.get("has_strong_pending_smart_sell", False):
        raw_score = min(100, raw_score + 5)

    # Mempool penalty: high bot density
    if mempool_tiny_swap_density > max_tiny_density:
        raw_score = max(0, raw_score - 10)

    result["major_interest_score"] = max(0, min(100, round(raw_score)))

    # -- Detail breakdown --
    result["detail"] = {
        "smart_money_alignment": smart_money_alignment,
        "whale_behavior": whale_behavior,
        "liquidity_quality": liquidity_quality,
        "rug_risk": rug_risk,
        "graph_risk": graph_risk,
        "social_quality": social_quality,
        "data_confidence": data_confidence,
        "mempool_tiny_swap_density": mempool_tiny_swap_density,
        "has_strong_pending_smart_buy": mp.get("has_strong_pending_smart_buy", False),
        "has_strong_pending_smart_sell": mp.get("has_strong_pending_smart_sell", False),
        "passes_hard_gates": passes_hard_gates,
    }

    # -- Set flag: only True when ALL gates pass and score exceeds threshold --
    min_score = thresholds.get("min_major_interest_score", 60)
    if not result["blockers"] and result["major_interest_score"] >= min_score:
        result["major_interest_flag"] = True

    return result
