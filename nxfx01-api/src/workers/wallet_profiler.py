"""Wallet Profiler & Clusterer — maintains wallet tiers, scores, and clusters.

Computes wallet_value_score, wallet_performance_score, assigns tiers,
groups wallets by shared funding + co-participation patterns,
and detects alpha cohorts.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict

import httpx

from src import db

logger = logging.getLogger("nxfx01.wallet_profiler")

BLOCKSCOUT_BASE = "https://base.blockscout.com"

# Tier thresholds (value_score + performance_score combined)
TIER_THRESHOLDS = {
    "TIER_1_WHALE": {"min_value": 80},
    "TIER_2_SMART_MONEY": {"min_performance": 65},
    "TIER_4_FLAGGED": {"max_combined": 20},
}

# Alpha cohort: win rate threshold over minimum launches
ALPHA_WIN_RATE_MIN = 0.60
ALPHA_MIN_LAUNCHES = 10


async def _get_wallet_balance(client: httpx.AsyncClient, address: str) -> float:
    """Get ETH balance in ether from Blockscout."""
    try:
        resp = await client.get(f"{BLOCKSCOUT_BASE}/api/v2/addresses/{address}", timeout=10)
        if resp.status_code != 200:
            return 0.0
        data = resp.json()
        coin_balance = data.get("coin_balance", "0")
        return int(coin_balance) / 1e18 if coin_balance else 0.0
    except Exception:
        return 0.0


async def _get_token_count(client: httpx.AsyncClient, address: str) -> int:
    """Get number of distinct tokens held."""
    try:
        resp = await client.get(
            f"{BLOCKSCOUT_BASE}/api/v2/addresses/{address}/token-balances",
            timeout=10,
        )
        if resp.status_code != 200:
            return 0
        data = resp.json()
        return len(data) if isinstance(data, list) else 0
    except Exception:
        return 0


def _compute_value_score(eth_balance: float, token_count: int) -> int:
    """Heuristic value score 0-100 based on balance and portfolio breadth."""
    # ETH balance component (0-70)
    if eth_balance >= 100:
        eth_score = 70
    elif eth_balance >= 10:
        eth_score = 50
    elif eth_balance >= 1:
        eth_score = 30
    elif eth_balance >= 0.1:
        eth_score = 15
    else:
        eth_score = 5

    # Token diversity component (0-30)
    if token_count >= 50:
        token_score = 30
    elif token_count >= 20:
        token_score = 20
    elif token_count >= 5:
        token_score = 10
    else:
        token_score = 3

    return min(100, eth_score + token_score)


async def _compute_performance_score(wallet: str) -> int:
    """Estimate performance based on participation in launches with known outcomes."""
    rows = await db.fetch(
        """
        SELECT lo.pnl_24h, lo.rugged, lo.final_status
        FROM launch_participants lp
        JOIN launch_outcomes lo ON lo.launch_id = lp.launch_id
        WHERE lp.wallet = $1
          AND lo.pnl_24h IS NOT NULL
        ORDER BY lo.recorded_at DESC
        LIMIT 50
        """,
        wallet,
    )

    if not rows:
        return 50  # no data → neutral

    wins = sum(1 for r in rows if (r["pnl_24h"] or 0) > 0)
    losses = sum(1 for r in rows if (r["pnl_24h"] or 0) < 0)
    rugs_entered = sum(1 for r in rows if r["rugged"])
    total = len(rows)

    win_rate = wins / total if total > 0 else 0.5
    rug_rate = rugs_entered / total if total > 0 else 0

    # Score: high win rate + low rug exposure = high score
    score = int(win_rate * 70 + (1 - rug_rate) * 30)
    return max(0, min(100, score))


def _assign_tier(value_score: int, performance_score: int, metadata: dict) -> str:
    """Assign wallet tier based on scores and metadata."""
    # Check for flagged indicators
    is_flagged = metadata.get("is_flagged", False)
    if is_flagged or (value_score + performance_score) < TIER_THRESHOLDS["TIER_4_FLAGGED"]["max_combined"]:
        return "TIER_4_FLAGGED"

    # Whale: high value regardless of performance
    if value_score >= TIER_THRESHOLDS["TIER_1_WHALE"]["min_value"]:
        return "TIER_1_WHALE"

    # Smart money: good historical performance
    if performance_score >= TIER_THRESHOLDS["TIER_2_SMART_MONEY"]["min_performance"]:
        return "TIER_2_SMART_MONEY"

    # Default to retail
    if value_score >= 10 or performance_score >= 30:
        return "TIER_3_RETAIL"

    return "UNKNOWN"


async def profile_wallet(wallet_address: str) -> dict:
    """Full profiling for a single wallet."""
    wallet_address = wallet_address.lower()

    async with httpx.AsyncClient() as client:
        eth_balance = await _get_wallet_balance(client, wallet_address)
        token_count = await _get_token_count(client, wallet_address)

    value_score = _compute_value_score(eth_balance, token_count)
    perf_score = await _compute_performance_score(wallet_address)

    # Fetch existing metadata
    existing = await db.fetchrow(
        "SELECT metadata FROM wallets WHERE wallet = $1", wallet_address
    )
    metadata = {}
    if existing and existing["metadata"]:
        metadata = json.loads(existing["metadata"]) if isinstance(existing["metadata"], str) else existing["metadata"]

    tier = _assign_tier(value_score, perf_score, metadata)

    # Check alpha cohort eligibility
    alpha_flag = False
    launch_count = await db.fetchval(
        "SELECT COUNT(DISTINCT launch_id) FROM launch_participants WHERE wallet = $1",
        wallet_address,
    ) or 0

    if launch_count >= ALPHA_MIN_LAUNCHES:
        # Check win rate on early entries
        early_wins = await db.fetchval(
            """
            SELECT COUNT(*) FROM launch_participants lp
            JOIN launch_outcomes lo ON lo.launch_id = lp.launch_id
            WHERE lp.wallet = $1
              AND lp.role = 'EARLY_BUYER'
              AND lo.pnl_24h > 0
            """,
            wallet_address,
        ) or 0
        early_total = await db.fetchval(
            """
            SELECT COUNT(*) FROM launch_participants lp
            JOIN launch_outcomes lo ON lo.launch_id = lp.launch_id
            WHERE lp.wallet = $1
              AND lp.role = 'EARLY_BUYER'
              AND lo.pnl_24h IS NOT NULL
            """,
            wallet_address,
        ) or 0
        if early_total >= ALPHA_MIN_LAUNCHES:
            win_rate = early_wins / early_total
            alpha_flag = win_rate >= ALPHA_WIN_RATE_MIN

    # Upsert wallet
    await db.execute(
        """
        INSERT INTO wallets (wallet, wallet_tier, wallet_value_score, wallet_performance_score,
                            alpha_cohort_flag, metadata, first_seen_at, last_seen_at)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, now(), now())
        ON CONFLICT (wallet) DO UPDATE SET
            wallet_tier = $2,
            wallet_value_score = $3,
            wallet_performance_score = $4,
            alpha_cohort_flag = $5,
            metadata = wallets.metadata || $6::jsonb,
            last_seen_at = now()
        """,
        wallet_address, tier, value_score, perf_score, alpha_flag,
        json.dumps({"eth_balance": eth_balance, "token_count": token_count, "launch_count": launch_count}),
    )

    logger.debug("Wallet profiled: %s tier=%s value=%d perf=%d alpha=%s", wallet_address, tier, value_score, perf_score, alpha_flag)

    return {
        "wallet": wallet_address,
        "wallet_tier": tier,
        "wallet_value_score": value_score,
        "wallet_performance_score": perf_score,
        "alpha_cohort_flag": alpha_flag,
    }


async def cluster_wallets(wallet_addresses: list[str]) -> dict:
    """Simple v1 clustering: group wallets by shared funder and co-participation.

    Heuristic approach — shared funder (within 2 hops) and co-participation
    in 3+ launches indicates a cluster.
    """
    if len(wallet_addresses) < 2:
        return {"clusters": [], "message": "Need at least 2 wallets to cluster"}

    # Find co-participation patterns
    coparticipation: dict[tuple[str, str], int] = defaultdict(int)

    for wa in wallet_addresses:
        # Get launches this wallet participated in
        launches = await db.fetch(
            "SELECT launch_id FROM launch_participants WHERE wallet = $1",
            wa.lower(),
        )
        launch_ids = {str(r["launch_id"]) for r in launches}

        for wb in wallet_addresses:
            if wa >= wb:
                continue
            other_launches = await db.fetch(
                "SELECT launch_id FROM launch_participants WHERE wallet = $1",
                wb.lower(),
            )
            other_ids = {str(r["launch_id"]) for r in other_launches}
            overlap = len(launch_ids & other_ids)
            if overlap >= 3:
                coparticipation[(wa.lower(), wb.lower())] = overlap

    # Build clusters from co-participation pairs
    clusters: list[set[str]] = []
    for (a, b), count in coparticipation.items():
        # Find existing cluster for a or b
        found = None
        for cluster in clusters:
            if a in cluster or b in cluster:
                cluster.add(a)
                cluster.add(b)
                found = cluster
                break
        if not found:
            clusters.append({a, b})

    # Persist clusters
    created_clusters = []
    for i, members in enumerate(clusters):
        cluster_id = f"cluster_{hash(frozenset(members)) % 100000:05d}"

        # Determine cluster tier from member tiers
        member_tiers = await db.fetch(
            "SELECT wallet_tier FROM wallets WHERE wallet = ANY($1)",
            list(members),
        )
        tier_counts = Counter(r["wallet_tier"] for r in member_tiers)
        if tier_counts.get("TIER_4_FLAGGED", 0) > len(members) / 2:
            cluster_tier = "TIER_4_FLAGGED"
        elif tier_counts.get("TIER_1_WHALE", 0) > 0:
            cluster_tier = "TIER_1_WHALE_CLUSTER"
        elif tier_counts.get("TIER_2_SMART_MONEY", 0) > len(members) / 3:
            cluster_tier = "TIER_2_SMART_CLUSTER"
        else:
            cluster_tier = "TIER_3_NEUTRAL"

        await db.execute(
            """
            INSERT INTO clusters (cluster_id, cluster_tier, member_count, description)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (cluster_id) DO UPDATE SET
                cluster_tier = $2,
                member_count = $3
            """,
            cluster_id, cluster_tier, len(members),
            f"Co-participation cluster of {len(members)} wallets",
        )

        # Update member wallets with cluster_id
        for member in members:
            await db.execute(
                "UPDATE wallets SET cluster_id = $1, cluster_tier = $2 WHERE wallet = $3",
                cluster_id, cluster_tier, member,
            )

        created_clusters.append({
            "cluster_id": cluster_id,
            "cluster_tier": cluster_tier,
            "members": list(members),
        })

    return {"clusters": created_clusters, "pairs_found": len(coparticipation)}


async def run() -> dict:
    """Profile wallets that appear in recent launches but haven't been profiled recently."""
    # Get wallets from recent launches that need profiling
    rows = await db.fetch(
        """
        SELECT DISTINCT lp.wallet
        FROM launch_participants lp
        LEFT JOIN wallets w ON w.wallet = lp.wallet
        WHERE (w.updated_at IS NULL OR w.updated_at < now() - interval '1 day')
        ORDER BY lp.wallet
        LIMIT 100
        """,
    )

    results = []
    for row in rows:
        result = await profile_wallet(row["wallet"])
        results.append(result)

    return {"processed": len(results), "results": results}
