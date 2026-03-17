"""Social Quality Score — X/Twitter sentiment and legitimacy signals.

Scans mentions of token ticker, contract address, and deployer handle from
trusted accounts and the broader crypto community. Produces a SocialQualityScore
that modestly boosts or penalizes the launch score.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("nxfx01.scoring.social_quality")

_policy: dict | None = None


def _load_policy() -> dict:
    global _policy
    if _policy is None:
        p = Path(__file__).parent.parent.parent / "config" / "scoring_policy.yaml"
        with open(p) as f:
            _policy = yaml.safe_load(f)
    return _policy


def _get_cfg() -> dict:
    return _load_policy().get("sub_scores", {}).get("social_quality", {})


def compute(
    social_mentions_total: int | None = None,
    social_mentions_trusted: int | None = None,
    social_sentiment_score: float | None = None,
    negative_reports_count: int | None = None,
    creator_social_presence: str | None = None,
    shill_bot_mention_count: int | None = None,
) -> dict[str, Any]:
    """Compute SocialQualityScore.

    Args:
        social_mentions_total: Total mentions of ticker + contract in scan window.
        social_mentions_trusted: Mentions from owner's account + curated trusted list.
        social_sentiment_score: Normalized sentiment (-1.0 to +1.0).
        negative_reports_count: Count of scam/rug reports from credible accounts.
        creator_social_presence: "none" | "neutral" | "positive" | "negative"
        shill_bot_mention_count: Mentions from low-cred / new / bot accounts.

    Returns dict with:
        score (0-100), rug_risk_bump (int), has_data (bool).
    """
    cfg = _get_cfg()
    weights = cfg.get("weights", {})
    thresholds = cfg.get("thresholds", {})

    result: dict[str, Any] = {
        "score": 50,  # neutral default
        "rug_risk_bump": 0,
        "has_data": False,
    }

    # If we have zero social data, return neutral (no boost, no penalty)
    if social_mentions_total is None and social_mentions_trusted is None:
        return result

    result["has_data"] = True

    # -- Component 1: Trusted mentions (0-100) --
    trusted = social_mentions_trusted or 0
    min_trusted = thresholds.get("min_trusted_mentions_for_boost", 2)
    if trusted >= min_trusted:
        trusted_score = min(100, 50 + trusted * 15)
    elif trusted > 0:
        trusted_score = 40
    else:
        trusted_score = 20  # no trusted mentions — below neutral

    # -- Component 2: Sentiment (0-100) --
    raw_sentiment = social_sentiment_score if social_sentiment_score is not None else 0.0
    # Map -1..+1 to 0..100
    sentiment_score = max(0, min(100, (raw_sentiment + 1) * 50))

    # -- Component 3: Negative reports (inverted: more reports = lower score) --
    neg_count = negative_reports_count or 0
    hard_flag = thresholds.get("negative_reports_hard_flag", 3)
    if neg_count >= hard_flag:
        neg_score = 0
        result["rug_risk_bump"] = 20  # bump rug risk score
    elif neg_count > 0:
        neg_score = max(0, 80 - neg_count * 25)
        if neg_count >= 2:
            result["rug_risk_bump"] = 10
    else:
        neg_score = 100

    # -- Component 4: Creator social presence (0-100) --
    presence = (creator_social_presence or "none").lower()
    presence_map = {"positive": 90, "neutral": 50, "none": 30, "negative": 10}
    presence_score = presence_map.get(presence, 30)

    # -- Shill bot penalty --
    shill_count = shill_bot_mention_count or 0
    shill_threshold = thresholds.get("shill_bot_penalty_threshold", 20)
    shill_penalty = 0
    if shill_count > shill_threshold:
        shill_penalty = min(30, (shill_count - shill_threshold) * 2)

    # Weighted combination
    raw_score = (
        trusted_score * weights.get("mentions_from_trusted", 0.35)
        + sentiment_score * weights.get("sentiment_score", 0.25)
        + neg_score * weights.get("negative_reports_penalty", 0.25)
        + presence_score * weights.get("creator_presence", 0.15)
    )

    raw_score = max(0, raw_score - shill_penalty)

    result["score"] = max(0, min(100, round(raw_score)))
    return result
