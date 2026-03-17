"""Deployer & Funder Profiler — analyzes deployer history and funding sources.

Event-driven: triggered when a launch reaches pending_initial and contract_safety
is filled. Queries deployer's past deployments, rug rate, token lifespans, and
funding chain. Also computes cross-launch velocity alert.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml

from src import db

logger = logging.getLogger("nxfx01.deployer_profiler")

BLOCKSCOUT_BASE = "https://base.blockscout.com"

_policy: dict | None = None


def _load_policy() -> dict:
    global _policy
    if _policy is None:
        policy_path = Path(__file__).parent.parent.parent / "config" / "scoring_policy.yaml"
        with open(policy_path) as f:
            _policy = yaml.safe_load(f)
    return _policy


def _determine_launchpad_trust(launch_type: str, deployer_address: str | None) -> str:
    """Determine launchpad trust level from launch_type + allowlist in config.

    Returns one of: NONE, LOW, MEDIUM, HIGH.
    """
    if launch_type != "launchpad":
        return "NONE"

    policy = _load_policy()
    allowlist = policy.get("launchpad_trust_allowlist", {})
    default_level = policy.get("launchpad_trust_default", "LOW")

    # Check deployer address against allowlists (high takes priority)
    if deployer_address:
        addr = deployer_address.lower()
        high_list = allowlist.get("high", {}) or {}
        if addr in high_list:
            return "HIGH"
        medium_list = allowlist.get("medium", {}) or {}
        if addr in medium_list:
            return "MEDIUM"

    return default_level


async def _get_deployer_history(client: httpx.AsyncClient, deployer: str) -> dict:
    """Query Blockscout for tokens previously deployed by this address."""
    try:
        resp = await client.get(
            f"{BLOCKSCOUT_BASE}/api/v2/addresses/{deployer}/tokens",
            params={"type": "ERC-20"},
            timeout=15,
        )
        if resp.status_code != 200:
            return {"tokens": [], "error": f"HTTP {resp.status_code}"}
        data = resp.json()
        return {"tokens": data.get("items", [])}
    except Exception as e:
        return {"tokens": [], "error": str(e)}


async def _get_deployer_txs(client: httpx.AsyncClient, deployer: str) -> list[dict]:
    """Get recent transactions for the deployer to analyze funding sources."""
    try:
        resp = await client.get(
            f"{BLOCKSCOUT_BASE}/api/v2/addresses/{deployer}/transactions",
            params={"filter": "to"},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("items", [])[:50]
    except Exception:
        return []


async def _get_internal_txs(client: httpx.AsyncClient, deployer: str) -> list[dict]:
    """Get internal transactions (potential bridge/contract funding)."""
    try:
        resp = await client.get(
            f"{BLOCKSCOUT_BASE}/api/v2/addresses/{deployer}/internal-transactions",
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("items", [])[:30]
    except Exception:
        return []


def _assess_deployer_reputation(history: dict, velocity_24h: int) -> tuple[int, str, list[str]]:
    """Score deployer reputation based on past deployments.

    Returns (score 0-100, summary, red_flags).
    Higher = better reputation.
    """
    tokens = history.get("tokens", [])
    red_flags: list[str] = []

    if not tokens:
        # Unknown deployer — no history, neutral score
        return 55, "New deployer with no prior Base token deployments", []

    total_deploys = len(tokens)
    # Very prolific deployers are suspicious
    if total_deploys > 20:
        red_flags.append("serial_deployer")
    if total_deploys > 50:
        red_flags.append("extreme_serial_deployer")

    # Cross-launch velocity
    if velocity_24h >= 3:
        red_flags.append("high_launch_velocity")
        summary = f"Deployer launched {velocity_24h} tokens in last 24h — high velocity alert"
        return max(0, 20 - (velocity_24h - 3) * 10), summary, red_flags
    elif velocity_24h >= 2:
        red_flags.append("elevated_launch_velocity")

    # Base score from deployment count (moderate count is fine, too many is bad)
    if total_deploys <= 5:
        base_score = 60
        summary = f"{total_deploys} prior deployments — limited history"
    elif total_deploys <= 15:
        base_score = 50
        summary = f"{total_deploys} prior deployments — moderate deployer"
    else:
        base_score = max(20, 50 - (total_deploys - 15) * 2)
        summary = f"{total_deploys} prior deployments — prolific deployer, elevated risk"

    # Adjust for velocity
    velocity_penalty = max(0, (velocity_24h - 1) * 15)
    score = max(0, min(100, base_score - velocity_penalty))

    return score, summary, red_flags


def _assess_funding_risk(txs: list[dict], internal_txs: list[dict]) -> tuple[int, str, list[str]]:
    """Analyze deployer's funding sources for risk signals.

    Returns (score 0-100, summary, red_flags). Higher = safer.
    """
    red_flags: list[str] = []

    if not txs and not internal_txs:
        return 50, "No transaction history found — unable to determine funding source", ["unknown_funding"]

    # Analyze incoming transactions to the deployer
    funding_sources: list[str] = []
    total_value = 0.0

    for tx in txs:
        value_str = tx.get("value", "0")
        try:
            value_wei = int(value_str)
            value_eth = value_wei / 1e18
            total_value += value_eth
        except (ValueError, TypeError):
            continue

        from_addr = (tx.get("from", {}).get("hash", "") or "").lower()
        if from_addr:
            funding_sources.append(from_addr)

    # Check for known CEX/bridge addresses (simplified — expand over time)
    known_bridges = {
        "0x3154cf16ccdb4c6d922629664174b904d80f2c35": "base_bridge",
        "0x49048044d57e1c92a77f79988d21fa8faf74e97e": "base_portal",
    }

    bridge_funded = False
    for src in funding_sources:
        if src in known_bridges:
            bridge_funded = True

    # Internal transactions might indicate contract/bridge funding
    contract_funded = len(internal_txs) > 0

    # Build summary
    if bridge_funded:
        summary = f"Funded via bridge ({total_value:.2f} ETH total). {len(funding_sources)} funding sources."
        score = 65
    elif contract_funded:
        summary = f"Funded via contracts/internal txs. {len(funding_sources)} direct sources."
        score = 55
    elif len(funding_sources) == 1:
        summary = f"Single funding source: {funding_sources[0][:10]}... ({total_value:.2f} ETH)"
        score = 50
    elif len(funding_sources) > 5:
        summary = f"Multiple funding sources ({len(funding_sources)}), {total_value:.2f} ETH total — complex funding"
        score = 40
        red_flags.append("complex_funding_pattern")
    else:
        summary = f"{len(funding_sources)} funding sources, {total_value:.2f} ETH total"
        score = 55

    # Tiny funding is suspicious for "launch and dump" patterns
    if total_value < 0.01 and total_value > 0:
        red_flags.append("minimal_deployer_funding")
        score -= 10
    elif total_value < 0.1:
        score -= 5

    return max(0, min(100, score)), summary, red_flags


async def profile_deployer(launch_id: str, deployer_address: str) -> dict:
    """Full deployer + funder analysis for a single launch."""
    # Determine launchpad trust level from launch_type
    launch_row = await db.fetchrow(
        "SELECT launch_type, deployer_address FROM launches WHERE launch_id = $1",
        launch_id,
    )
    launch_type = launch_row["launch_type"] if launch_row else "unknown"
    trust_level = _determine_launchpad_trust(launch_type, deployer_address)

    if not deployer_address:
        # No deployer known — assign neutral-mid scores (unknown ≠ bad)
        await db.execute(
            """
            UPDATE launches
            SET deployer_reputation = 55,
                funding_risk = 50,
                launchpad_trust_level = $1,
                notes = notes || $2::jsonb
            WHERE launch_id = $3
            """,
            trust_level,
            json.dumps({
                "deployer_history_summary": "Deployer address unknown",
                "deployer_red_flags": ["unknown_deployer"],
                "funding_sources_summary": "Unable to analyze — deployer unknown",
                "funding_red_flags": ["unknown_deployer"],
                "launchpad_trust_level": trust_level,
            }),
            launch_id,
        )
        return {"launch_id": launch_id, "deployer_reputation": 55, "funding_risk": 50,
                "launchpad_trust_level": trust_level}

    # Compute cross-launch velocity (launches by same deployer in last 24h)
    velocity_24h = await db.fetchval(
        """
        SELECT COUNT(*) FROM launches
        WHERE deployer_address = $1
          AND detected_at > now() - interval '24 hours'
        """,
        deployer_address,
    ) or 0

    async with httpx.AsyncClient() as client:
        # Fetch deployer history and funding data
        history = await _get_deployer_history(client, deployer_address)
        txs = await _get_deployer_txs(client, deployer_address)
        internal_txs = await _get_internal_txs(client, deployer_address)

    # Score
    rep_score, rep_summary, rep_flags = _assess_deployer_reputation(history, velocity_24h)
    fund_score, fund_summary, fund_flags = _assess_funding_risk(txs, internal_txs)

    # Update launches table
    notes_patch = {
        "deployer_history_summary": rep_summary,
        "deployer_red_flags": rep_flags,
        "funding_sources_summary": fund_summary,
        "funding_red_flags": fund_flags,
        "launchpad_trust_level": trust_level,
    }

    raw_patch = {
        "deployer_profile": {
            "deployer": deployer_address,
            "total_prior_tokens": len(history.get("tokens", [])),
            "velocity_24h": velocity_24h,
            "funding_sources_count": len(txs),
        }
    }

    await db.execute(
        """
        UPDATE launches
        SET deployer_reputation = $1,
            funding_risk = $2,
            deployer_launch_velocity_24h = $3,
            launchpad_trust_level = $4,
            notes = notes || $5::jsonb,
            raw_signals = raw_signals || $6::jsonb
        WHERE launch_id = $7
        """,
        rep_score,
        fund_score,
        velocity_24h,
        trust_level,
        json.dumps(notes_patch),
        json.dumps(raw_patch),
        launch_id,
    )

    # Upsert deployer into wallets table
    await db.execute(
        """
        INSERT INTO wallets (wallet, wallet_tier, metadata, first_seen_at, last_seen_at)
        VALUES ($1, 'UNKNOWN', $2::jsonb, now(), now())
        ON CONFLICT (wallet) DO UPDATE
        SET metadata = wallets.metadata || $2::jsonb,
            last_seen_at = now()
        """,
        deployer_address,
        json.dumps({
            "is_deployer": True,
            "total_deployments": len(history.get("tokens", [])),
            "velocity_24h": velocity_24h,
        }),
    )

    logger.info(
        "Deployer profile: %s rep=%d, funding=%d, velocity=%d, trust=%s, flags=%s",
        deployer_address, rep_score, fund_score, velocity_24h, trust_level, rep_flags + fund_flags,
    )

    return {
        "launch_id": launch_id,
        "deployer_reputation": rep_score,
        "funding_risk": fund_score,
        "velocity_24h": velocity_24h,
        "launchpad_trust_level": trust_level,
        "red_flags": rep_flags + fund_flags,
    }


async def run() -> dict:
    """Process launches that have contract_safety but no deployer scores yet.
    Event-driven: only picks launches ready for profiling.
    """
    rows = await db.fetch(
        """
        SELECT launch_id, deployer_address FROM launches
        WHERE status = 'pending_initial'
          AND contract_safety IS NOT NULL
          AND deployer_reputation IS NULL
        ORDER BY detected_at ASC
        LIMIT 50
        """
    )

    results = []
    for row in rows:
        result = await profile_deployer(str(row["launch_id"]), row["deployer_address"])
        results.append(result)

    return {"processed": len(results), "results": results}
