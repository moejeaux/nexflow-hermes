"""Rug Risk Score — composite rug/scam risk from code, graph, and behavioral signals.

Combines code-risk (from contract_safety), GraphRiskScore, behavioral rug
indicators (tax changes, deployer dumps, abandonment), and funding risk into
a single RugRiskScore (0-100, higher = MORE dangerous).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("nxfx01.scoring.rug_risk")

_policy: dict | None = None


def _load_policy() -> dict:
    global _policy
    if _policy is None:
        p = Path(__file__).parent.parent.parent / "config" / "scoring_policy.yaml"
        with open(p) as f:
            _policy = yaml.safe_load(f)
    return _policy


def _get_cfg() -> dict:
    return _load_policy().get("sub_scores", {}).get("rug_risk", {})


def compute(
    contract_safety: int | None,
    graph_risk_score: int | None,
    funding_risk: int | None,
    notes: dict,
    behavioral_signals: dict | None = None,
) -> dict[str, Any]:
    """Compute RugRiskScore from multiple risk dimensions.

    Args:
        contract_safety: 0-100 (higher = safer). Inverted to code risk.
        graph_risk_score: 0-100 (higher = riskier).
        funding_risk: 0-100 (higher = safer). Inverted to funding risk.
        notes: Launch notes dict with red_flags lists.
        behavioral_signals: Optional dict with behavioral rug indicators:
            - tax_current (float): current sell tax
            - tax_at_launch (float): sell tax at launch
            - deployer_sell_pct (float): fraction of supply deployer sold
            - volume_vs_peak_pct (float): current volume / peak volume
            - hours_since_launch (float)

    Returns dict with:
        score (0-100, higher = MORE rug risk), hard_block_flags (list),
        components (dict of sub-risks), has_data (bool).
    """
    cfg = _get_cfg()
    components_cfg = cfg.get("components", {})
    hard_patterns = set(cfg.get("hard_block_patterns", []))
    behavioral_cfg = cfg.get("behavioral_indicators", {})

    result: dict[str, Any] = {
        "score": 0,
        "hard_block_flags": [],
        "components": {},
        "has_data": True,
    }

    # -- Check for hard block patterns across all flag lists --
    all_flags: list[str] = []
    for key in [
        "contract_red_flags", "deployer_red_flags", "funding_red_flags",
        "holder_distribution_red_flags", "liquidity_red_flags",
    ]:
        all_flags.extend(notes.get(key, []))

    hard_hits = [f for f in all_flags if f.split(":")[0] in hard_patterns]
    if hard_hits:
        result["hard_block_flags"] = hard_hits
        result["score"] = 100
        return result

    # -- Component 1: Code Risk (inverted contract_safety) --
    code_risk = 100 - (contract_safety or 50)
    # Extra penalty for specific code red flags
    code_flags = notes.get("contract_red_flags", [])
    if any("selfdestruct" in f for f in code_flags):
        code_risk = min(100, code_risk + 25)
    if any("mint" in f.lower() for f in code_flags):
        code_risk = min(100, code_risk + 15)
    if any("blacklist" in f.lower() for f in code_flags):
        code_risk = min(100, code_risk + 15)

    result["components"]["code_risk"] = code_risk

    # -- Component 2: Graph Risk --
    graph_risk = graph_risk_score if graph_risk_score is not None else 30
    result["components"]["graph_risk"] = graph_risk

    # -- Component 3: Behavioral Risk --
    behavioral_risk = 0
    signals = behavioral_signals or {}

    # Tax hike detection
    tax_current = signals.get("tax_current", 0)
    tax_at_launch = signals.get("tax_at_launch", 0)
    tax_threshold = behavioral_cfg.get("tax_hike_threshold", 0.10)
    if tax_current - tax_at_launch > tax_threshold:
        behavioral_risk += 40
        result["hard_block_flags"].append("stealth_tax_increase")

    # Deployer dump detection
    deployer_sell_pct = signals.get("deployer_sell_pct", 0)
    dump_threshold = behavioral_cfg.get("deployer_dump_threshold", 0.30)
    if deployer_sell_pct > dump_threshold:
        behavioral_risk += 35

    # Abandonment profile: volume collapse
    volume_vs_peak = signals.get("volume_vs_peak_pct", 1.0)
    hours_since = signals.get("hours_since_launch", 0)
    abandon_window = behavioral_cfg.get("abandonment_window_hours", 4)
    collapse_threshold = behavioral_cfg.get("volume_collapse_threshold", 0.05)

    if hours_since >= 1 and volume_vs_peak < collapse_threshold and hours_since < abandon_window:
        behavioral_risk += 30

    behavioral_risk = min(100, behavioral_risk)
    result["components"]["behavioral_risk"] = behavioral_risk

    # -- Component 4: Funding Risk (inverted) --
    funding_risk_val = 100 - (funding_risk or 50)
    # Extra penalty for mixer/unknown funded deployers
    funding_flags = notes.get("funding_red_flags", [])
    if "mixer_funded_deployer" in funding_flags:
        funding_risk_val = min(100, funding_risk_val + 30)
    if "unknown_funding" in funding_flags:
        funding_risk_val = min(100, funding_risk_val + 10)

    result["components"]["funding_risk"] = funding_risk_val

    # -- Weighted combination --
    raw_score = (
        code_risk * components_cfg.get("code_risk_weight", 0.35)
        + graph_risk * components_cfg.get("graph_risk_weight", 0.25)
        + behavioral_risk * components_cfg.get("behavioral_risk_weight", 0.25)
        + funding_risk_val * components_cfg.get("funding_risk_weight", 0.15)
    )

    # If behavioral hard block flags were set (e.g., stealth tax), enforce high score
    if result["hard_block_flags"]:
        raw_score = max(raw_score, 85)

    result["score"] = max(0, min(100, round(raw_score)))
    return result
