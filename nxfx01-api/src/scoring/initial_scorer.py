"""Initial Safety Scorer — Stage 1 scoring after contract + deployer analysis.

Combines contract_safety, deployer_reputation, and funding_risk into
overall_safety_initial and action_initial. Uses scoring_policy.yaml weights
and threshold rules including critical red flags, velocity blocks, and
regime adjustments.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from src import db

logger = logging.getLogger("nxfx01.initial_scorer")

_policy: dict | None = None


def _load_policy() -> dict:
    global _policy
    if _policy is None:
        policy_path = Path(__file__).parent.parent.parent / "config" / "scoring_policy.yaml"
        with open(policy_path) as f:
            _policy = yaml.safe_load(f)
    return _policy


async def _get_regime_adjustment() -> int:
    """Get the current regime's threshold adjustment for FAST."""
    policy = _load_policy()
    regime = await db.get_config("base_market_regime") or "NORMAL"
    adjustments = policy.get("thresholds", {}).get("regime_adjustments", {})
    return adjustments.get(regime, 0)


def _has_critical_flags(notes: dict, policy: dict) -> tuple[bool, list[str]]:
    """Check if any critical red flags are present that force BLOCK."""
    critical_set = set(policy.get("critical_red_flags", []))
    found = []

    for flag_list_key in ["contract_red_flags", "deployer_red_flags", "funding_red_flags"]:
        for flag in notes.get(flag_list_key, []):
            # Handle flags like "bad_template_match:honeypot_v3" → check prefix
            base_flag = flag.split(":")[0] if ":" in flag else flag
            if base_flag in critical_set:
                found.append(flag)

    return len(found) > 0, found


async def score_launch(launch_id: str) -> dict[str, Any]:
    """Compute Stage 1 initial safety score and mode for a single launch."""
    policy = _load_policy()
    weights = policy["initial_weights"]
    thresholds = policy["thresholds"]
    fingerprint_cfg = policy.get("fingerprint", {})

    # Fetch launch data
    row = await db.fetchrow(
        """
        SELECT contract_safety, deployer_reputation, funding_risk,
               deployer_launch_velocity_24h, bytecode_hash, notes,
               launchpad_trust_level
        FROM launches WHERE launch_id = $1
        """,
        launch_id,
    )
    if not row:
        return {"error": f"Launch {launch_id} not found"}

    contract = row["contract_safety"] or 0
    deployer = row["deployer_reputation"] or 0
    funding = row["funding_risk"] or 0
    velocity = row["deployer_launch_velocity_24h"] or 0
    notes = json.loads(row["notes"]) if isinstance(row["notes"], str) else (row["notes"] or {})
    trust_level = row["launchpad_trust_level"] or "NONE"

    # ---- Launchpad trust modifier ----
    # Boost deployer_reputation for launchpad-deployed tokens, clamped to [0, 95]
    trust_modifiers = policy.get("launchpad_trust_modifiers", {})
    trust_bonus = trust_modifiers.get(trust_level, 0)
    if trust_bonus:
        deployer = min(95, max(0, deployer + trust_bonus))

    # ---- Velocity override ----
    velocity_block = fingerprint_cfg.get("velocity_block", 3)
    velocity_warn = fingerprint_cfg.get("velocity_warn", 2)

    if velocity >= velocity_block:
        # Auto-BLOCK: too many launches from this deployer
        explanation = (
            f"Auto-BLOCK: deployer launched {velocity} tokens in 24h "
            f"(threshold: {velocity_block}). contract={contract}, deployer={deployer}, funding={funding}"
        )
        await _save_initial_score(launch_id, 0, "BLOCK", explanation, notes, policy)
        return {"launch_id": launch_id, "overall_safety_initial": 0, "action_initial": "BLOCK",
                "reason": "velocity_auto_block"}

    # ---- Critical red flags → auto-BLOCK ----
    has_critical, critical_flags = _has_critical_flags(notes, policy)
    if has_critical:
        explanation = (
            f"Auto-BLOCK: critical red flags detected: {critical_flags}. "
            f"contract={contract}, deployer={deployer}, funding={funding}"
        )
        await _save_initial_score(launch_id, 0, "BLOCK", explanation, notes, policy)
        return {"launch_id": launch_id, "overall_safety_initial": 0, "action_initial": "BLOCK",
                "reason": "critical_red_flags"}

    # ---- Weighted score calculation ----
    # Normalize so weights don't need to sum to 1.0
    weight_sum = (
        weights["contract_safety"]
        + weights["deployer_reputation"]
        + weights["funding_risk"]
    )
    overall = (
        contract * weights["contract_safety"]
        + deployer * weights["deployer_reputation"]
        + funding * weights["funding_risk"]
    ) / weight_sum

    # Velocity warning penalty (doesn't auto-block but reduces score)
    if velocity >= velocity_warn:
        velocity_penalty = fingerprint_cfg.get("velocity_penalty_per_launch", 15) * (velocity - 1)
        overall = max(0, overall - velocity_penalty)

    overall = round(overall)

    # ---- Mode assignment ----
    regime_adj = await _get_regime_adjustment()
    fast_min = thresholds["fast_min"] + regime_adj
    block_max = thresholds["block_max"]

    if overall >= fast_min:
        action = "FAST"
    elif overall < block_max:
        action = "BLOCK"
    else:
        action = "WAIT"

    # ---- Explanation ----
    parts = [
        f"contract_safety={contract} (w={weights['contract_safety']})",
        f"deployer_reputation={deployer} (w={weights['deployer_reputation']})",
        f"funding_risk={funding} (w={weights['funding_risk']})",
    ]
    if trust_bonus:
        parts.append(f"launchpad_trust={trust_level} (+{trust_bonus} to deployer_rep)")
    if velocity >= velocity_warn:
        parts.append(f"velocity_24h={velocity} (penalty applied)")
    if regime_adj != 0:
        parts.append(f"regime_adjustment=+{regime_adj}")

    dominant = max(
        [("contract_safety", contract), ("deployer_reputation", deployer), ("funding_risk", funding)],
        key=lambda x: abs(x[1] - 50),
    )
    parts.append(f"dominant_factor={dominant[0]}")

    explanation = f"Initial score={overall}, mode={action}. {'; '.join(parts)}"

    await _save_initial_score(launch_id, overall, action, explanation, notes, policy)

    logger.info("Initial score: %s → %d (%s)", launch_id, overall, action)

    return {
        "launch_id": launch_id,
        "overall_safety_initial": overall,
        "action_initial": action,
        "explanation": explanation,
    }


async def _save_initial_score(
    launch_id: str, overall: int, action: str, explanation: str, notes: dict, policy: dict
) -> None:
    """Persist Stage 1 score to DB."""
    notes["safety_explanation_initial"] = explanation
    policy_version = policy.get("version", "unknown")

    await db.execute(
        """
        UPDATE launches
        SET overall_safety_initial = $1,
            action_initial = $2,
            status = 'initial_scored',
            policy_version = $3,
            initial_scored_at = now(),
            notes = $4::jsonb
        WHERE launch_id = $5
        """,
        overall,
        action,
        policy_version,
        json.dumps(notes),
        launch_id,
    )


async def run() -> dict:
    """Score all launches that have contract + deployer data but no initial score."""
    rows = await db.fetch(
        """
        SELECT launch_id FROM launches
        WHERE status = 'pending_initial'
          AND contract_safety IS NOT NULL
          AND deployer_reputation IS NOT NULL
          AND funding_risk IS NOT NULL
          AND overall_safety_initial IS NULL
        ORDER BY detected_at ASC
        LIMIT 100
        """
    )

    results = []
    for row in rows:
        result = await score_launch(str(row["launch_id"]))
        results.append(result)

    return {"processed": len(results), "results": results}
