"""Liquidity Quality Score — measures tradability, LP health, and execution feasibility.

Hard-gates ultra-thin tokens from FAST classification. Tokens below minimum
LP/volume/spread thresholds can NEVER be high-priority regardless of other scores.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("nxfx01.scoring.liquidity_quality")

_policy: dict | None = None


def _load_policy() -> dict:
    global _policy
    if _policy is None:
        p = Path(__file__).parent.parent.parent / "config" / "scoring_policy.yaml"
        with open(p) as f:
            _policy = yaml.safe_load(f)
    return _policy


def _get_cfg() -> dict:
    return _load_policy().get("sub_scores", {}).get("liquidity_quality", {})


def _tier_score(value: float, tiers: dict, higher_is_better: bool = True) -> float:
    """Score a value against tier thresholds. Returns 0-100."""
    if higher_is_better:
        if value >= tiers.get("excellent", float("inf")):
            return 100
        if value >= tiers.get("good", float("inf")):
            return 80
        if value >= tiers.get("adequate", float("inf")):
            return 55
        if value >= tiers.get("minimum", 0):
            return 30
        return 0
    else:
        # Lower is better (e.g., spread)
        if value <= tiers.get("excellent", 0):
            return 100
        if value <= tiers.get("good", 0):
            return 80
        if value <= tiers.get("adequate", 0):
            return 55
        if value <= tiers.get("maximum", float("inf")):
            return 25
        return 0


def compute(
    lp_usd: float | None,
    rolling_volume_1h_usd: float | None,
    rolling_volume_4h_usd: float | None = None,
    effective_spread_bp: float | None = None,
    lp_depth_2pct_usd: float | None = None,
) -> dict[str, Any]:
    """Compute LiquidityQualityScore.

    Args:
        lp_usd: Current LP size in USD.
        rolling_volume_1h_usd: Rolling 1h volume in USD.
        rolling_volume_4h_usd: Rolling 4h volume in USD.
        effective_spread_bp: Effective spread in basis points.
        lp_depth_2pct_usd: Max trade size at 2% slippage.

    Returns dict with:
        score (0-100), passes_hard_gates (bool), gate_failures (list),
        has_data (bool).
    """
    cfg = _get_cfg()
    hard_gates = cfg.get("hard_gates", {})
    scoring_cfg = cfg.get("scoring", {})
    weights = cfg.get("weights", {})

    result: dict[str, Any] = {
        "score": 0,
        "passes_hard_gates": False,
        "gate_failures": [],
        "lp_usd": lp_usd,
        "rolling_volume_1h_usd": rolling_volume_1h_usd,
        "effective_spread_bp": effective_spread_bp,
        "has_data": False,
    }

    # Check if we have enough data
    if lp_usd is None and rolling_volume_1h_usd is None:
        # No liquidity data at all — cannot assess
        result["gate_failures"].append("no_liquidity_data")
        return result

    result["has_data"] = True

    # -- Hard gate checks --
    min_lp = hard_gates.get("min_lp_usd", 5000)
    min_vol = hard_gates.get("min_volume_1h_usd", 1000)
    max_spread = hard_gates.get("max_effective_spread_bp", 500)

    passes = True
    if lp_usd is not None and lp_usd < min_lp:
        result["gate_failures"].append(f"lp_usd_{lp_usd:.0f}_below_{min_lp}")
        passes = False
    elif lp_usd is None:
        result["gate_failures"].append("lp_usd_unknown")
        passes = False

    if rolling_volume_1h_usd is not None and rolling_volume_1h_usd < min_vol:
        result["gate_failures"].append(f"volume_1h_{rolling_volume_1h_usd:.0f}_below_{min_vol}")
        passes = False
    elif rolling_volume_1h_usd is None:
        # Volume unknown — still can pass if LP is strong
        pass

    if effective_spread_bp is not None and effective_spread_bp > max_spread:
        result["gate_failures"].append(f"spread_{effective_spread_bp:.0f}bp_above_{max_spread}")
        passes = False

    result["passes_hard_gates"] = passes

    # -- Compute score from tiers --
    lp_tiers = scoring_cfg.get("lp_usd_tiers", {})
    vol_tiers = scoring_cfg.get("volume_tiers", {})
    spread_tiers = scoring_cfg.get("spread_tiers", {})

    lp_score = _tier_score(lp_usd or 0, lp_tiers, higher_is_better=True)
    vol_score = _tier_score(rolling_volume_1h_usd or 0, vol_tiers, higher_is_better=True)

    # Spread: use estimate if not available (assume moderate)
    spread_val = effective_spread_bp if effective_spread_bp is not None else 200
    spread_score = _tier_score(spread_val, spread_tiers, higher_is_better=False)

    raw_score = (
        lp_score * weights.get("lp_size", 0.40)
        + vol_score * weights.get("volume", 0.30)
        + spread_score * weights.get("spread", 0.30)
    )

    # If hard gates fail, cap score at 25 (reflects real constraint)
    if not passes:
        raw_score = min(raw_score, 25)

    result["score"] = max(0, min(100, round(raw_score)))
    return result
