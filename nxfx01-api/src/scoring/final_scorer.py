"""Final Safety Scorer — Stage 2 scoring after behavior analysis.

v2.1: Uses 7 sub-scores + mempool features layer + major interest detection.
Passes mempool snapshots to SM/Whale scorers; evaluates major_interest as
first-class concept; integrates mempool-based de-risk triggers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from src import db
from src.scoring import (
    data_confidence_scorer,
    derisk_engine,
    liquidity_quality_scorer,
    major_interest,
    rug_risk_scorer,
    smart_money_scorer,
    social_quality_scorer,
    whale_behavior_scorer,
)

logger = logging.getLogger("nxfx01.final_scorer")

_policy: dict | None = None


def _load_policy() -> dict:
    global _policy
    if _policy is None:
        policy_path = Path(__file__).parent.parent.parent / "config" / "scoring_policy.yaml"
        with open(policy_path) as f:
            _policy = yaml.safe_load(f)
    return _policy


def _normalize_wallets(raw: list | dict) -> dict:
    if isinstance(raw, dict):
        return raw
    return {w["wallet"]: w for w in raw if isinstance(w, dict) and "wallet" in w}


async def score_launch(launch_id: str) -> dict:
    """Compute Stage 2 final safety score and mode using v2 sub-scores."""
    policy = _load_policy()
    weights = policy["final_weights"]
    thresholds = policy["thresholds"]
    fingerprint_cfg = policy.get("fingerprint", {})
    critical_flags_set = set(policy.get("critical_red_flags", []))
    hard_gates = thresholds.get("fast_hard_gates", {})

    row = await db.fetchrow(
        """
        SELECT contract_safety, deployer_reputation, funding_risk,
               holder_distribution, liquidity_stability,
               smart_money_participation, whale_participation,
               overall_safety_initial, action_initial,
               deployer_launch_velocity_24h, notes, raw_signals,
               token_address, pair_address, deployer_address,
               lp_usd, rolling_volume_1h_usd, rolling_volume_4h_usd,
               effective_spread_bp, lp_depth_2pct_usd,
               social_mentions_total, social_mentions_trusted,
               social_sentiment_score, negative_reports_count,
               creator_social_presence,
               smart_money_alignment, whale_behavior_score,
               graph_risk_score, rug_risk_score,
               liquidity_quality_score, social_quality_score,
               data_confidence_score
        FROM launches WHERE launch_id = $1
        """,
        launch_id,
    )
    if not row:
        return {"error": f"Launch {launch_id} not found"}

    action_initial = row["action_initial"]
    velocity = row["deployer_launch_velocity_24h"] or 0
    notes = json.loads(row["notes"]) if isinstance(row["notes"], str) else (row["notes"] or {})
    raw_signals = json.loads(row["raw_signals"]) if isinstance(row["raw_signals"], str) else (row["raw_signals"] or {})
    initial_score = row["overall_safety_initial"] or 0

    # Detect whether we have real behavior data
    fifo_wallet_count = int(
        (raw_signals.get("behavior_update") or {}).get("fifo_wallet_count", 0)
    )
    has_behavior_data = fifo_wallet_count > 0

    # v2.1: Fetch latest mempool snapshot for this token
    mempool_row = await db.fetchrow(
        """
        SELECT sm_pending_buy_usd, sm_pending_sell_usd,
               whale_pending_buy_usd, whale_pending_sell_usd,
               tiny_swap_density, anomaly_density,
               fee_urgency_score, unique_pending_routers,
               derived_sm_conviction, derived_whale_bias,
               sm_pending_net_usd, whale_pending_net_usd
        FROM mempool_features
        WHERE token_address = $1
        ORDER BY snapshot_ts DESC LIMIT 1
        """,
        row["token_address"],
    )
    mempool_snapshot: dict | None = dict(mempool_row) if mempool_row else None

    # ---- Auto-BLOCK checks ----
    velocity_block = fingerprint_cfg.get("velocity_block", 3)
    if velocity >= velocity_block:
        explanation = f"Final BLOCK: deployer velocity {velocity} >= {velocity_block}"
        await _save_final_score(launch_id, 0, "BLOCK", explanation, notes, policy)
        return {"launch_id": launch_id, "overall_safety_final": 0, "action_final": "BLOCK"}

    all_flags = []
    for key in ["contract_red_flags", "deployer_red_flags", "funding_red_flags",
                "holder_distribution_red_flags", "liquidity_red_flags"]:
        all_flags.extend(notes.get(key, []))

    critical_found = [f for f in all_flags if f.split(":")[0] in critical_flags_set]
    if critical_found:
        explanation = f"Final BLOCK: critical flags {critical_found}"
        await _save_final_score(launch_id, 0, "BLOCK", explanation, notes, policy)
        return {"launch_id": launch_id, "overall_safety_final": 0, "action_final": "BLOCK"}

    # ---- BLOCK stays BLOCK ----
    if action_initial == "BLOCK":
        behavior_scores = [
            row["holder_distribution"] or 0, row["liquidity_stability"] or 0,
            row["smart_money_participation"] or 0, row["whale_participation"] or 0,
        ]
        if not (all(s >= 80 for s in behavior_scores) and not all_flags):
            explanation = f"Final BLOCK maintained (initial was BLOCK). flags={all_flags}"
            await _save_final_score(launch_id, 0, "BLOCK", explanation, notes, policy)
            return {"launch_id": launch_id, "overall_safety_final": 0, "action_final": "BLOCK"}

    # ============================================================
    # v2 SUB-SCORE COMPUTATION
    # ============================================================

    # Gather sub-scores that were pre-computed by behavior_updater v2,
    # or compute them if not yet available.

    # Read pre-computed FIFO wallet data for SM/whale scorers
    wallets_data = _normalize_wallets(
        (raw_signals.get("behavior_update") or {}).get("wallets_snapshot", [])
    )

    # -- SmartMoneyAlignmentScore --
    sm_result = None
    sm_score = row["smart_money_alignment"]
    if sm_score is None and has_behavior_data:
        sm_result = await smart_money_scorer.compute(
            launch_id, wallets_data, mempool_snapshot=mempool_snapshot,
        )
        sm_score = sm_result["score"]
    sm_score = sm_score or 0

    # -- WhaleBehaviorScore --
    whale_result = None
    whale_score = row["whale_behavior_score"]
    if whale_score is None and has_behavior_data:
        whale_result = await whale_behavior_scorer.compute(
            launch_id, wallets_data, mempool_snapshot=mempool_snapshot,
        )
        whale_score = whale_result["score"]
    whale_score = whale_score or 0

    # -- GraphRiskScore (raw risk, higher = worse) --
    graph_risk = row["graph_risk_score"] or 30  # moderate default if unavailable

    # -- RugRiskScore --
    rug_result = rug_risk_scorer.compute(
        contract_safety=row["contract_safety"],
        graph_risk_score=graph_risk,
        funding_risk=row["funding_risk"],
        notes=notes,
        behavioral_signals=raw_signals.get("behavioral_rug_signals"),
    )
    rug_risk = rug_result["score"]

    # Hard block from rug risk
    if rug_result["hard_block_flags"]:
        explanation = f"Final BLOCK: rug risk hard flags {rug_result['hard_block_flags']}"
        await _save_final_score(launch_id, 0, "BLOCK", explanation, notes, policy)
        return {"launch_id": launch_id, "overall_safety_final": 0, "action_final": "BLOCK"}

    # -- LiquidityQualityScore --
    liq_result = liquidity_quality_scorer.compute(
        lp_usd=row["lp_usd"],
        rolling_volume_1h_usd=row["rolling_volume_1h_usd"],
        rolling_volume_4h_usd=row["rolling_volume_4h_usd"],
        effective_spread_bp=row["effective_spread_bp"],
        lp_depth_2pct_usd=row["lp_depth_2pct_usd"],
    )
    liq_score = liq_result["score"]

    # -- SocialQualityScore --
    social_result = social_quality_scorer.compute(
        social_mentions_total=row["social_mentions_total"],
        social_mentions_trusted=row["social_mentions_trusted"],
        social_sentiment_score=row["social_sentiment_score"],
        negative_reports_count=row["negative_reports_count"],
        creator_social_presence=row["creator_social_presence"],
    )
    social_score = social_result["score"]

    # Apply social rug risk bump
    if social_result["rug_risk_bump"] > 0:
        rug_risk = min(100, rug_risk + social_result["rug_risk_bump"])

    # -- DataConfidenceScore --
    completeness_flags = {
        "contract": row["contract_safety"] is not None,
        "deployer": row["deployer_reputation"] is not None,
        "funding": row["funding_risk"] is not None,
        "smart_money": sm_score > 0 and has_behavior_data,
        "whale": whale_score > 0 and has_behavior_data,
        "graph": row["graph_risk_score"] is not None,
        "liquidity": row["lp_usd"] is not None or row["liquidity_stability"] is not None,
        "social": row["social_mentions_total"] is not None,
        "mempool": mempool_snapshot is not None,
    }
    confidence_result = data_confidence_scorer.compute(completeness_flags)
    data_confidence = confidence_result["score"]

    # ============================================================
    # WEIGHTED SCORE CALCULATION
    # ============================================================

    # For risk scores (graph_risk, rug_risk), use inverted values (100-risk)
    score_values = {
        "contract_safety": row["contract_safety"] or 0,
        "deployer_reputation": row["deployer_reputation"] or 0,
        "funding_risk": row["funding_risk"] or 0,
        "smart_money_alignment": sm_score,
        "whale_behavior": whale_score,
        "graph_risk": max(0, 100 - graph_risk),       # invert: high safety = good
        "rug_risk": max(0, 100 - rug_risk),            # invert: high safety = good
        "liquidity_quality": liq_score,
        "social_quality": social_score,
        "holder_distribution": row["holder_distribution"] or 0,
    }

    overall = sum(score_values[k] * weights.get(k, 0) for k in weights)
    overall = round(max(0, min(100, overall)))

    # Apply data confidence modulation
    if not has_behavior_data:
        overall = max(overall, initial_score)
        logger.info("No behavior data for %s — using initial %d as floor", launch_id, initial_score)
    else:
        overall, conf_explanation = data_confidence_scorer.apply_confidence_to_score(
            overall, confidence_result
        )

    # ---- Regime adjustment ----
    regime = await db.get_config("base_market_regime") or "NORMAL"
    regime_adj = thresholds.get("regime_adjustments", {}).get(regime, 0)
    fast_min = thresholds["fast_min"] + regime_adj
    block_max = thresholds["block_max"]

    # ---- Mode assignment ----
    if overall >= fast_min:
        action = "FAST"
    elif overall < block_max:
        action = "BLOCK"
    else:
        action = "WAIT"

    # ============================================================
    # v2 FAST HARD GATES — must all pass or FAST is impossible
    # ============================================================
    if action == "FAST":
        gate_failures = []

        if row["lp_usd"] is not None and row["lp_usd"] < hard_gates.get("min_lp_usd", 5000):
            gate_failures.append(f"lp_usd={row['lp_usd']}")
        elif row["lp_usd"] is None:
            gate_failures.append("lp_usd=unknown")

        if (row["rolling_volume_1h_usd"] is not None
                and row["rolling_volume_1h_usd"] < hard_gates.get("min_volume_1h_usd", 1000)):
            gate_failures.append(f"vol_1h={row['rolling_volume_1h_usd']}")

        if (row["effective_spread_bp"] is not None
                and row["effective_spread_bp"] > hard_gates.get("max_effective_spread_bp", 500)):
            gate_failures.append(f"spread={row['effective_spread_bp']}bp")

        if rug_risk > hard_gates.get("max_rug_risk_score", 45):
            gate_failures.append(f"rug_risk={rug_risk}")

        if data_confidence < hard_gates.get("min_data_confidence", 60):
            gate_failures.append(f"data_conf={data_confidence}")

        if graph_risk > hard_gates.get("max_graph_risk_score", 60):
            gate_failures.append(f"graph_risk={graph_risk}")

        if not confidence_result.get("can_be_fast", True):
            gate_failures.append("critical_data_missing")

        if not liq_result.get("passes_hard_gates", True):
            gate_failures.extend(liq_result.get("gate_failures", []))

        if gate_failures:
            action = "WAIT"
            logger.info("FAST blocked by hard gates for %s: %s", launch_id, gate_failures)
            notes["fast_gate_failures"] = gate_failures

    # ---- Behavior-based downgrades (with real data) ----
    if action == "FAST" and has_behavior_data:
        if (row["holder_distribution"] or 0) < 30:
            action = "WAIT"
        if liq_score < 25:
            action = "WAIT"
        real_flags = [f for f in all_flags
                     if f not in ("no_holder_data_yet", "no_lp_data_yet")]
        if len(real_flags) >= 2:
            action = "WAIT"

    # ---- Upgrade check (WAIT → FAST) ----
    if action_initial == "WAIT" and action == "FAST" and has_behavior_data:
        if (sm_score < 40 or liq_score < 50 or (row["holder_distribution"] or 0) < 60):
            action = "WAIT"
            logger.info("Blocked WAIT→FAST for %s: weak sub-scores", launch_id)

    # ============================================================
    # DE-RISK TRIGGER EVALUATION
    # ============================================================
    if action in ("FAST", "WAIT") and has_behavior_data:
        _sm_cohort_exit = (sm_result or {}).get("sm_cohort_exit_pct", 0)
        _whale_z = (whale_result or {}).get("whale_net_flow_z", 0)
        _whale_sells_rips = (whale_result or {}).get("whale_sells_in_rips_ratio", 0)

        # v2.1: extract mempool-derived signals
        _mp_flags: dict = {}
        _sm_pending = 0.0
        _whale_pending = 0.0
        if mempool_snapshot:
            _mp_flags = {
                "anomaly_density": mempool_snapshot.get("anomaly_density", 0),
                "tiny_swap_density": mempool_snapshot.get("tiny_swap_density", 0),
            }
            _sm_pending = float(mempool_snapshot.get("derived_sm_conviction", 0))
            _whale_pending = float(mempool_snapshot.get("derived_whale_bias", 0))

        triggers = derisk_engine.evaluate_triggers(
            sm_cohort_exit_pct=_sm_cohort_exit,
            whale_net_flow_z=_whale_z,
            sells_in_rips_ratio=_whale_sells_rips,
            rug_risk_score=rug_risk,
            lp_removed_pct=0,  # tracked by behavior updater
            volume_vs_peak_pct=1.0,
            effective_spread_bp=float(row["effective_spread_bp"] or 0),
            graph_risk_score=graph_risk,
            mempool_flags=_mp_flags,
            sm_pending_conviction=_sm_pending,
            whale_pending_bias=_whale_pending,
        )
        if triggers:
            position_action = derisk_engine.determine_position_action(triggers)
            await derisk_engine.persist_triggers(launch_id, triggers, position_action)

            if position_action == "HARD_EXIT":
                action = "BLOCK"
                logger.info("HARD_EXIT triggered for %s → BLOCK", launch_id)

    # ============================================================
    # v2.1 MAJOR INTEREST EVALUATION
    # ============================================================
    # Determine if hard gates passed (same logic as the FAST gate check above)
    _fast_gate_passed = "fast_gate_failures" not in notes and action in ("FAST",)
    _mp_tiny = float((mempool_snapshot or {}).get("tiny_swap_density", 0))
    _mp_eval_flags: dict = {}
    if mempool_snapshot:
        _mp_eval_flags = {
            "derived_sm_conviction": mempool_snapshot.get("derived_sm_conviction", 0),
            "derived_whale_bias": mempool_snapshot.get("derived_whale_bias", 0),
        }

    mi_result = major_interest.evaluate(
        smart_money_alignment=sm_score,
        whale_behavior=whale_score,
        liquidity_quality=liq_score,
        rug_risk=rug_risk,
        graph_risk=graph_risk,
        social_quality=social_score,
        data_confidence=data_confidence,
        mempool_flags=_mp_eval_flags,
        mempool_tiny_swap_density=_mp_tiny,
        passes_hard_gates=_fast_gate_passed,
        critical_missing=confidence_result.get("critical_missing"),
    )

    # ---- Build explanation ----
    parts = [f"{k}={score_values[k]} (w={weights.get(k, 0)})" for k in weights if k in score_values]
    parts.append(f"rug_risk_raw={rug_risk}")
    parts.append(f"graph_risk_raw={graph_risk}")
    parts.append(f"data_confidence={data_confidence}")
    parts.append(f"liq_passes_gates={liq_result.get('passes_hard_gates', 'N/A')}")
    parts.append(f"major_interest={mi_result['major_interest_flag']}(score={mi_result['major_interest_score']})")
    if mempool_snapshot:
        parts.append(f"mempool_sm_conv={mempool_snapshot.get('derived_sm_conviction', 0):.2f}")
        parts.append(f"mempool_whale_bias={mempool_snapshot.get('derived_whale_bias', 0):.2f}")
    transition = f"{action_initial}→{action}" if action != action_initial else action
    parts.append(f"transition={transition}")
    if regime_adj:
        parts.append(f"regime={regime}(+{regime_adj})")

    explanation = f"Final score={overall}, mode={action}. {'; '.join(parts)}"

    # ---- Persist all sub-scores ----
    await _save_final_score_v2(
        launch_id, overall, action, explanation, notes, policy,
        sub_scores={
            "smart_money_alignment": sm_score,
            "whale_behavior_score": whale_score,
            "graph_risk_score": graph_risk,
            "rug_risk_score": rug_risk,
            "liquidity_quality_score": liq_score,
            "social_quality_score": social_score,
            "data_confidence_score": data_confidence,
        },
        sm_detail=sm_result,
        whale_detail=whale_result,
        major_interest_result=mi_result,
    )

    logger.info("Final score: %s → %d (%s, was %s) major_interest=%s",
                launch_id, overall, action, action_initial,
                mi_result["major_interest_flag"])

    return {
        "launch_id": launch_id,
        "overall_safety_final": overall,
        "action_final": action,
        "action_initial": action_initial,
        "explanation": explanation,
        "sub_scores": {
            "smart_money_alignment": sm_score,
            "whale_behavior": whale_score,
            "graph_risk": graph_risk,
            "rug_risk": rug_risk,
            "liquidity_quality": liq_score,
            "social_quality": social_score,
            "data_confidence": data_confidence,
        },
        "major_interest": {
            "flag": mi_result["major_interest_flag"],
            "score": mi_result["major_interest_score"],
            "detail": mi_result["detail"],
            "blockers": mi_result["blockers"],
        },
    }


async def _save_final_score(
    launch_id: str, overall: int, action: str, explanation: str, notes: dict, policy: dict
) -> None:
    notes["safety_explanation_final"] = explanation
    await db.execute(
        """
        UPDATE launches SET
            overall_safety_final = $1,
            action_final = $2,
            status = 'behavior_scored',
            notes = $3::jsonb,
            scoring_version = 'v2.1'
        WHERE launch_id = $4
        """,
        overall, action, json.dumps(notes), launch_id,
    )


async def _save_final_score_v2(
    launch_id: str, overall: int, action: str, explanation: str,
    notes: dict, policy: dict,
    sub_scores: dict[str, int],
    sm_detail: dict | None = None,
    whale_detail: dict | None = None,
    major_interest_result: dict | None = None,
) -> None:
    """Persist Stage 2 score with all v2 sub-scores."""
    notes["safety_explanation_final"] = explanation

    # Persist smart money detail fields if computed
    sm_fields = {}
    if sm_detail:
        sm_fields = {
            "founding_cohort_size": sm_detail.get("founding_cohort_size"),
            "smart_money_count": sm_detail.get("smart_money_count"),
            "smart_money_share": sm_detail.get("smart_money_share"),
            "accumulation_ratio_30m": sm_detail.get("accumulation_ratio_30m"),
            "sm_cohort_exit_pct": sm_detail.get("sm_cohort_exit_pct"),
            "median_sm_hold_minutes": sm_detail.get("median_sm_hold_minutes"),
        }

    whale_fields = {}
    if whale_detail:
        whale_fields = {
            "whale_net_flow_tokens": whale_detail.get("whale_net_flow_tokens"),
            "whale_accumulation_trend": whale_detail.get("whale_accumulation_trend"),
            "whale_buys_on_dips_ratio": whale_detail.get("whale_buys_on_dips_ratio"),
            "whale_sells_in_rips_ratio": whale_detail.get("whale_sells_in_rips_ratio"),
        }

    # v2.1: major interest fields
    mi = major_interest_result or {}
    mi_flag = mi.get("major_interest_flag", False)
    mi_score = mi.get("major_interest_score", 0)
    mi_detail_json = json.dumps(mi.get("detail", {}))

    await db.execute(
        """
        UPDATE launches SET
            overall_safety_final = $1,
            action_final = $2,
            status = 'behavior_scored',
            notes = $3::jsonb,
            scoring_version = 'v2.1',
            smart_money_alignment = $4,
            whale_behavior_score = $5,
            graph_risk_score = $6,
            rug_risk_score = $7,
            liquidity_quality_score = $8,
            social_quality_score = $9,
            data_confidence_score = $10,
            founding_cohort_size = $11,
            smart_money_count = $12,
            smart_money_share = $13,
            accumulation_ratio_30m = $14,
            sm_cohort_exit_pct = $15,
            median_sm_hold_minutes = $16,
            whale_net_flow_tokens = $17,
            whale_accumulation_trend = $18,
            whale_buys_on_dips_ratio = $19,
            whale_sells_in_rips_ratio = $20,
            major_interest_flag = $21,
            major_interest_score = $22,
            major_interest_detail = $23::jsonb
        WHERE launch_id = $24
        """,
        overall, action, json.dumps(notes),
        sub_scores.get("smart_money_alignment"),
        sub_scores.get("whale_behavior_score"),
        sub_scores.get("graph_risk_score"),
        sub_scores.get("rug_risk_score"),
        sub_scores.get("liquidity_quality_score"),
        sub_scores.get("social_quality_score"),
        sub_scores.get("data_confidence_score"),
        sm_fields.get("founding_cohort_size"),
        sm_fields.get("smart_money_count"),
        sm_fields.get("smart_money_share"),
        sm_fields.get("accumulation_ratio_30m"),
        sm_fields.get("sm_cohort_exit_pct"),
        sm_fields.get("median_sm_hold_minutes"),
        whale_fields.get("whale_net_flow_tokens"),
        whale_fields.get("whale_accumulation_trend"),
        whale_fields.get("whale_buys_on_dips_ratio"),
        whale_fields.get("whale_sells_in_rips_ratio"),
        mi_flag, mi_score, mi_detail_json,
        launch_id,
    )


async def run() -> dict:
    """Score all launches that have behavior data but no final score."""
    rows = await db.fetch(
        """
        SELECT launch_id FROM launches
        WHERE status = 'initial_scored'
          AND behavior_scored_at IS NOT NULL
          AND overall_safety_final IS NULL
        ORDER BY detected_at ASC
        LIMIT 100
        """
    )

    results = []
    for row in rows:
        result = await score_launch(str(row["launch_id"]))
        results.append(result)

    return {"processed": len(results), "results": results}
