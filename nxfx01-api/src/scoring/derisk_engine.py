"""Sell / De-Risk Trigger Engine — evaluates whether to downgrade, scale down, or exit.

v2.1: Adds mempool-based triggers (strong pending smart/whale sells),
major_interest_flag revocation, and pending-flow-aware escalation.

Monitors active positions and detects trigger conditions from scoring_policy.yaml.
Each trigger has a SOFT_DERISK or HARD_EXIT severity.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src import db

logger = logging.getLogger("nxfx01.scoring.derisk_engine")

_policy: dict | None = None


def _load_policy() -> dict:
    global _policy
    if _policy is None:
        p = Path(__file__).parent.parent.parent / "config" / "scoring_policy.yaml"
        with open(p) as f:
            _policy = yaml.safe_load(f)
    return _policy


def _get_cfg() -> dict:
    return _load_policy().get("derisk", {}).get("triggers", {})


def evaluate_triggers(
    sm_cohort_exit_pct: float = 0.0,
    founding_cohort_exit_pct: float = 0.0,
    whale_net_flow_z: float = 0.0,
    sells_in_rips_ratio: float = 0.0,
    rug_risk_score: int = 0,
    prev_rug_risk_score: int | None = None,
    lp_removed_pct: float = 0.0,
    volume_vs_peak_pct: float = 1.0,
    effective_spread_bp: float = 0.0,
    graph_risk_score: int = 0,
    prev_graph_risk_score: int | None = None,
    mempool_flags: dict[str, Any] | None = None,
    sm_pending_conviction: float = 0.0,
    whale_pending_bias: float = 0.0,
) -> list[dict[str, Any]]:
    """Evaluate all de-risk trigger conditions.

    Returns a list of triggered events, each with:
        trigger_type (str), severity ("SOFT_DERISK" | "HARD_EXIT"),
        details (dict with values that caused the trigger).
    """
    cfg = _get_cfg()
    triggered: list[dict[str, Any]] = []
    mp = mempool_flags or {}

    # -- Smart money cohort exit --
    sm_cfg = cfg.get("sm_cohort_exit", {})
    if sm_cohort_exit_pct > sm_cfg.get("hard_threshold", 0.70):
        triggered.append({
            "trigger_type": "sm_cohort_exit",
            "severity": "HARD_EXIT",
            "details": {"sm_cohort_exit_pct": sm_cohort_exit_pct,
                       "threshold": sm_cfg["hard_threshold"]},
        })
    elif sm_cohort_exit_pct > sm_cfg.get("soft_threshold", 0.40):
        triggered.append({
            "trigger_type": "sm_cohort_exit",
            "severity": "SOFT_DERISK",
            "details": {"sm_cohort_exit_pct": sm_cohort_exit_pct,
                       "threshold": sm_cfg["soft_threshold"]},
        })

    # -- Founding cohort exit --
    fc_cfg = cfg.get("founding_cohort_exit", {})
    if founding_cohort_exit_pct > fc_cfg.get("hard_threshold", 0.80):
        triggered.append({
            "trigger_type": "founding_cohort_exit",
            "severity": "HARD_EXIT",
            "details": {"founding_cohort_exit_pct": founding_cohort_exit_pct},
        })
    elif founding_cohort_exit_pct > fc_cfg.get("soft_threshold", 0.50):
        triggered.append({
            "trigger_type": "founding_cohort_exit",
            "severity": "SOFT_DERISK",
            "details": {"founding_cohort_exit_pct": founding_cohort_exit_pct},
        })

    # -- Whale distribution flip --
    whale_cfg = cfg.get("whale_distribution_flip", {})
    if whale_net_flow_z < -0.7 and sells_in_rips_ratio > 0.5:
        triggered.append({
            "trigger_type": "whale_distribution_flip",
            "severity": "HARD_EXIT",
            "details": {"whale_net_flow_z": whale_net_flow_z,
                       "sells_in_rips_ratio": sells_in_rips_ratio},
        })
    elif whale_net_flow_z < -0.3:
        triggered.append({
            "trigger_type": "whale_distribution_flip",
            "severity": "SOFT_DERISK",
            "details": {"whale_net_flow_z": whale_net_flow_z},
        })

    # -- Rug risk spike --
    rug_cfg = cfg.get("rug_risk_spike", {})
    if rug_risk_score > rug_cfg.get("hard_threshold", 75):
        triggered.append({
            "trigger_type": "rug_risk_spike",
            "severity": "HARD_EXIT",
            "details": {"rug_risk_score": rug_risk_score,
                       "prev": prev_rug_risk_score},
        })
    elif rug_risk_score > rug_cfg.get("soft_threshold", 55):
        triggered.append({
            "trigger_type": "rug_risk_spike",
            "severity": "SOFT_DERISK",
            "details": {"rug_risk_score": rug_risk_score},
        })

    # -- LP drain --
    lp_cfg = cfg.get("lp_drain", {})
    if lp_removed_pct > lp_cfg.get("hard_threshold", 0.50):
        triggered.append({
            "trigger_type": "lp_drain",
            "severity": "HARD_EXIT",
            "details": {"lp_removed_pct": lp_removed_pct},
        })
    elif lp_removed_pct > lp_cfg.get("soft_threshold", 0.20):
        triggered.append({
            "trigger_type": "lp_drain",
            "severity": "SOFT_DERISK",
            "details": {"lp_removed_pct": lp_removed_pct},
        })

    # -- Volume collapse --
    vol_cfg = cfg.get("volume_collapse", {})
    if volume_vs_peak_pct < vol_cfg.get("hard_threshold", 0.03):
        triggered.append({
            "trigger_type": "volume_collapse",
            "severity": "HARD_EXIT",
            "details": {"volume_vs_peak_pct": volume_vs_peak_pct},
        })
    elif volume_vs_peak_pct < vol_cfg.get("soft_threshold", 0.10):
        triggered.append({
            "trigger_type": "volume_collapse",
            "severity": "SOFT_DERISK",
            "details": {"volume_vs_peak_pct": volume_vs_peak_pct},
        })

    # -- Spread explosion --
    spread_cfg = cfg.get("spread_explosion", {})
    if effective_spread_bp > spread_cfg.get("hard_threshold_bp", 1000):
        triggered.append({
            "trigger_type": "spread_explosion",
            "severity": "HARD_EXIT",
            "details": {"effective_spread_bp": effective_spread_bp},
        })
    elif effective_spread_bp > spread_cfg.get("soft_threshold_bp", 300):
        triggered.append({
            "trigger_type": "spread_explosion",
            "severity": "SOFT_DERISK",
            "details": {"effective_spread_bp": effective_spread_bp},
        })

    # -- Graph risk spike --
    graph_cfg = cfg.get("graph_risk_spike", {})
    if graph_risk_score > graph_cfg.get("hard_threshold", 70):
        triggered.append({
            "trigger_type": "graph_risk_spike",
            "severity": "HARD_EXIT",
            "details": {"graph_risk_score": graph_risk_score,
                       "prev": prev_graph_risk_score},
        })
    elif graph_risk_score > graph_cfg.get("soft_threshold", 50):
        triggered.append({
            "trigger_type": "graph_risk_spike",
            "severity": "SOFT_DERISK",
            "details": {"graph_risk_score": graph_risk_score},
        })

    # ============================================================
    # v2.1: MEMPOOL-BASED TRIGGERS
    # ============================================================

    mempool_cfg = cfg.get("mempool_smart_sell", {})

    # -- Strong pending smart-money sell (pre-block distribution) --
    if mp.get("has_strong_pending_smart_sell", False):
        # If realized exits are also happening, escalate to HARD
        if sm_cohort_exit_pct > 0.25:
            triggered.append({
                "trigger_type": "mempool_smart_sell",
                "severity": "HARD_EXIT",
                "details": {
                    "has_strong_pending_smart_sell": True,
                    "sm_cohort_exit_pct": sm_cohort_exit_pct,
                    "sm_pending_conviction": sm_pending_conviction,
                },
            })
        else:
            triggered.append({
                "trigger_type": "mempool_smart_sell",
                "severity": "SOFT_DERISK",
                "details": {
                    "has_strong_pending_smart_sell": True,
                    "sm_pending_conviction": sm_pending_conviction,
                },
            })

    # -- Strong pending whale sell --
    if mp.get("has_strong_pending_whale_sell", False):
        if whale_net_flow_z < -0.3:
            triggered.append({
                "trigger_type": "mempool_whale_sell",
                "severity": "HARD_EXIT",
                "details": {
                    "has_strong_pending_whale_sell": True,
                    "whale_net_flow_z": whale_net_flow_z,
                    "whale_pending_bias": whale_pending_bias,
                },
            })
        else:
            triggered.append({
                "trigger_type": "mempool_whale_sell",
                "severity": "SOFT_DERISK",
                "details": {
                    "has_strong_pending_whale_sell": True,
                    "whale_pending_bias": whale_pending_bias,
                },
            })

    # -- High tiny swap density (bot/wash activity) --
    tiny_density_cfg = cfg.get("mempool_anomaly_density", {})
    tiny_density = mp.get("tiny_swap_density", 0) or 0
    if tiny_density > tiny_density_cfg.get("hard_threshold", 0.75):
        triggered.append({
            "trigger_type": "mempool_anomaly_density",
            "severity": "HARD_EXIT",
            "details": {"tiny_swap_density": tiny_density},
        })
    elif tiny_density > tiny_density_cfg.get("soft_threshold", 0.50):
        triggered.append({
            "trigger_type": "mempool_anomaly_density",
            "severity": "SOFT_DERISK",
            "details": {"tiny_swap_density": tiny_density},
        })

    return triggered


def determine_position_action(triggers: list[dict[str, Any]]) -> str:
    """Determine the overall position action from triggered events.

    Returns: 'HOLD', 'SOFT_DERISK', or 'HARD_EXIT'.
    """
    if not triggers:
        return "HOLD"

    severities = [t["severity"] for t in triggers]

    if "HARD_EXIT" in severities:
        return "HARD_EXIT"

    # Multiple SOFT_DERISK triggers escalate to HARD_EXIT
    if severities.count("SOFT_DERISK") >= 3:
        return "HARD_EXIT"

    return "SOFT_DERISK"


async def persist_triggers(
    launch_id: str,
    triggers: list[dict[str, Any]],
    position_action: str,
) -> None:
    """Save triggered de-risk events to DB."""
    # Insert individual trigger events
    for trigger in triggers:
        await db.execute(
            """
            INSERT INTO derisk_events (launch_id, trigger_type, severity, details)
            VALUES ($1, $2, $3, $4::jsonb)
            """,
            launch_id,
            trigger["trigger_type"],
            trigger["severity"],
            json.dumps(trigger["details"]),
        )

    # Update launch with current position action
    await db.execute(
        """
        UPDATE launches SET
            position_action = $1,
            derisk_triggers = $2::jsonb,
            derisk_updated_at = now()
        WHERE launch_id = $3
        """,
        position_action,
        json.dumps(triggers),
        launch_id,
    )

    logger.info(
        "De-risk: %s → %s (%d triggers)",
        launch_id, position_action, len(triggers),
    )
