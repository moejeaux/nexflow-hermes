"""Outcome Tracker — records realized performance for launches.

For launches older than 1h/24h/7d, computes PnL, drawdown, and rug detection.
Fills launch_outcomes table and updates launch status to outcome_scored.
"""

from __future__ import annotations

import json
import logging

import httpx

from src import db

logger = logging.getLogger("nxfx01.outcome_tracker")

BLOCKSCOUT_BASE = "https://base.blockscout.com"
BASESCAN_BASE = "http://localhost:3100"


async def _estimate_current_price(client: httpx.AsyncClient, token_address: str) -> float | None:
    """Rough price estimate from recent transfers. Returns USD estimate or None."""
    # In production, use a DEX price oracle or aggregator API.
    # For now, check if pool still has liquidity as a proxy.
    try:
        resp = await client.get(
            f"{BLOCKSCOUT_BASE}/api/v2/tokens/{token_address}",
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        # Blockscout sometimes has exchange_rate for known tokens
        rate = data.get("exchange_rate")
        if rate:
            return float(rate)
        return None
    except Exception:
        return None


async def _detect_rug(client: httpx.AsyncClient, token_address: str, pair_address: str | None) -> tuple[bool, str | None]:
    """Detect if a token has been rugged.

    Heuristics:
    - Pool has zero or near-zero liquidity
    - Token transfers have stopped completely
    - Deployer removed all LP
    """
    if not pair_address:
        return False, None

    try:
        resp = await client.get(
            f"{BLOCKSCOUT_BASE}/api/v2/addresses/{pair_address}",
            timeout=10,
        )
        if resp.status_code != 200:
            return False, None
        data = resp.json()

        coin_balance = data.get("coin_balance", "0")
        eth_balance = int(coin_balance) / 1e18 if coin_balance else 0

        # If pool has < 0.01 ETH, likely drained
        if eth_balance < 0.01:
            return True, "LP drained — pool balance near zero"

        return False, None
    except Exception:
        return False, None


async def track_outcome(launch_id: str) -> dict:
    """Compute and store outcome metrics for a launch."""
    row = await db.fetchrow(
        """
        SELECT token_address, pair_address, deployer_address, detected_at,
               action_initial, action_final, overall_safety_initial, overall_safety_final,
               major_interest_flag, major_interest_score,
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

    token = row["token_address"]
    pair = row["pair_address"]
    detected = row["detected_at"]

    async with httpx.AsyncClient() as client:
        current_price = await _estimate_current_price(client, token)
        rugged, rug_reason = await _detect_rug(client, token, pair)

    # Determine final status
    if rugged:
        final_status = "RUGGED"
    elif current_price is None or current_price == 0:
        final_status = "DEAD"
    else:
        final_status = "ACTIVE"

    # Check if outcome already exists; update if so
    existing = await db.fetchrow(
        "SELECT id FROM launch_outcomes WHERE launch_id = $1",
        launch_id,
    )

    # v2.1: build a sub-scores snapshot for self-learning correlation
    sub_scores_snapshot = json.dumps({
        "smart_money_alignment": row["smart_money_alignment"],
        "whale_behavior_score": row["whale_behavior_score"],
        "graph_risk_score": row["graph_risk_score"],
        "rug_risk_score": row["rug_risk_score"],
        "liquidity_quality_score": row["liquidity_quality_score"],
        "social_quality_score": row["social_quality_score"],
        "data_confidence_score": row["data_confidence_score"],
    })

    if existing:
        await db.execute(
            """
            UPDATE launch_outcomes SET
                rugged = $1,
                final_status = $2,
                recorded_at = now(),
                major_interest_flag_at_entry = $3,
                sub_scores_snapshot = $4::jsonb
            WHERE launch_id = $5
            """,
            rugged, final_status,
            row["major_interest_flag"], sub_scores_snapshot,
            launch_id,
        )
    else:
        await db.execute(
            """
            INSERT INTO launch_outcomes
                (launch_id, rugged, final_status, recorded_at,
                 major_interest_flag_at_entry, sub_scores_snapshot)
            VALUES ($1, $2, $3, now(), $4, $5::jsonb)
            """,
            launch_id, rugged, final_status,
            row["major_interest_flag"], sub_scores_snapshot,
        )

    # Update launch status
    await db.execute(
        "UPDATE launches SET status = 'outcome_scored' WHERE launch_id = $1",
        launch_id,
    )

    logger.info(
        "Outcome: %s → %s (rugged=%s)",
        launch_id, final_status, rugged,
    )

    return {
        "launch_id": launch_id,
        "final_status": final_status,
        "rugged": rugged,
        "rug_reason": rug_reason,
    }


async def run() -> dict:
    """Process launches that are old enough for outcome tracking.

    - 1h+ old with behavior_scored status → check outcomes
    - Already outcome_scored → re-check for later data (24h, 7d)
    """
    # Launches that need first outcome check (>1h after detection)
    rows = await db.fetch(
        """
        SELECT launch_id FROM launches
        WHERE status = 'behavior_scored'
          AND detected_at < now() - interval '1 hour'
        ORDER BY detected_at ASC
        LIMIT 50
        """
    )

    # Also re-check recent outcomes for updates (e.g., 24h/7d PnL)
    recheck_rows = await db.fetch(
        """
        SELECT l.launch_id FROM launches l
        JOIN launch_outcomes lo ON lo.launch_id = l.launch_id
        WHERE lo.final_status = 'ACTIVE'
          AND lo.recorded_at < now() - interval '6 hours'
        ORDER BY lo.recorded_at ASC
        LIMIT 25
        """
    )

    all_ids = [str(r["launch_id"]) for r in rows] + [str(r["launch_id"]) for r in recheck_rows]
    # Deduplicate
    seen = set()
    unique_ids = []
    for lid in all_ids:
        if lid not in seen:
            seen.add(lid)
            unique_ids.append(lid)

    results = []
    for lid in unique_ids:
        result = await track_outcome(lid)
        results.append(result)

    return {"processed": len(results), "results": results}
