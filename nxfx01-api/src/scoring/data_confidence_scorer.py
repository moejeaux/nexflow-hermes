"""Data Confidence Score — enforces missing-data policy.

Missing data = uncertainty = lower confidence = lower aggressiveness.
This module NEVER treats missing data as neutral. Each feature group's
absence applies a concrete penalty and may cap the maximum possible score.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("nxfx01.scoring.data_confidence")

_policy: dict | None = None


def _load_policy() -> dict:
    global _policy
    if _policy is None:
        p = Path(__file__).parent.parent.parent / "config" / "scoring_policy.yaml"
        with open(p) as f:
            _policy = yaml.safe_load(f)
    return _policy


def _get_cfg() -> dict:
    return _load_policy().get("sub_scores", {}).get("data_confidence", {})


def compute(
    completeness_flags: dict[str, bool],
    completeness_levels: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute DataConfidenceScore and determine constraints.

    Args:
        completeness_flags: {group_name: has_any_data} for each feature group.
            Expected groups: contract, deployer, funding, smart_money, whale,
                           graph, liquidity, social
        completeness_levels: Optional {group_name: 0.0-1.0} for partial data.
            If not provided, flags are treated as binary (1.0 or 0.0).

    Returns dict with:
        score (0-100), critical_missing (list of groups),
        max_allowed_score (int), can_be_fast (bool),
        missing_groups (list), penalty_breakdown (dict).
    """
    cfg = _get_cfg()
    groups_cfg = cfg.get("groups", {})
    rules = cfg.get("rules", {})

    critical_missing_cap = rules.get("critical_missing_max_score", 40)
    penalty_per_missing = rules.get("missing_group_penalty_pct", 8)
    min_confidence_fast = rules.get("min_confidence_for_fast", 60)

    result: dict[str, Any] = {
        "score": 100,
        "critical_missing": [],
        "max_allowed_score": 100,
        "can_be_fast": True,
        "missing_groups": [],
        "penalty_breakdown": {},
    }

    levels = completeness_levels or {}
    total_weight = 0.0
    weighted_completeness = 0.0

    for group_name, group_cfg in groups_cfg.items():
        weight = group_cfg.get("weight", 0.1)
        is_critical = group_cfg.get("critical", False)
        total_weight += weight

        # Determine completeness level for this group
        has_data = completeness_flags.get(group_name, False)
        level = levels.get(group_name, 1.0 if has_data else 0.0)

        weighted_completeness += level * weight

        if not has_data or level == 0.0:
            result["missing_groups"].append(group_name)

            if is_critical:
                result["critical_missing"].append(group_name)
                result["max_allowed_score"] = min(
                    result["max_allowed_score"], critical_missing_cap
                )
                result["can_be_fast"] = False
            else:
                # Non-critical missing: percentage penalty
                penalty = penalty_per_missing
                result["penalty_breakdown"][group_name] = penalty

    # Compute raw confidence score
    if total_weight > 0:
        raw_score = (weighted_completeness / total_weight) * 100
    else:
        raw_score = 0

    # Apply non-critical group penalties
    total_penalty = sum(result["penalty_breakdown"].values())
    raw_score = max(0, raw_score - total_penalty)

    result["score"] = max(0, min(100, round(raw_score)))

    # Check FAST eligibility
    if result["score"] < min_confidence_fast:
        result["can_be_fast"] = False

    return result


def apply_confidence_to_score(
    launch_score: int,
    confidence_result: dict[str, Any],
) -> tuple[int, str]:
    """Apply data confidence constraints to a launch score.

    Returns (adjusted_score, explanation).
    """
    max_allowed = confidence_result.get("max_allowed_score", 100)
    confidence = confidence_result.get("score", 100)

    # Scale the launch score by confidence level
    if confidence < 100:
        scale_factor = 0.5 + (confidence / 200)  # maps 0→0.5, 100→1.0
        adjusted = round(launch_score * scale_factor)
    else:
        adjusted = launch_score

    # Cap at max allowed
    adjusted = min(adjusted, max_allowed)

    parts = [f"confidence={confidence}"]
    if confidence_result.get("critical_missing"):
        parts.append(f"critical_missing={confidence_result['critical_missing']}")
        parts.append(f"capped_at={max_allowed}")
    if confidence_result.get("missing_groups"):
        parts.append(f"missing={confidence_result['missing_groups']}")

    explanation = "; ".join(parts)
    return adjusted, explanation
