"""Behavior Updater — Stage 2 post-launch behavior analysis.

For launches in last 60 minutes where behavior hasn't been filled yet:
- Fetches FIFO metrics from mcp-basescan
- Maps early buyers to wallet tiers
- Computes holder_distribution, liquidity_stability, smart_money_participation,
  whale_participation
- Builds notable_participants list
- Sets behavior_filled timestamps
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import yaml

from src import db

logger = logging.getLogger("nxfx01.behavior_updater")

# mcp-basescan endpoint (local)
BASESCAN_BASE = "http://localhost:3100"
BLOCKSCOUT_BASE = "https://base.blockscout.com"

BEHAVIOR_VERSION = "v1.0"


def _normalize_wallets(raw: list | dict) -> dict:
    """Convert FIFO wallets list to {address: data} dict."""
    if isinstance(raw, dict):
        return raw
    return {w["wallet"]: w for w in raw if isinstance(w, dict) and "wallet" in w}


def _load_policy() -> dict:
    policy_path = Path(__file__).parent.parent.parent / "config" / "scoring_policy.yaml"
    with open(policy_path) as f:
        return yaml.safe_load(f)


async def _get_fifo_metrics(client: httpx.AsyncClient, token_address: str) -> dict | None:
    """Fetch FIFO wallet metrics from mcp-basescan."""
    try:
        resp = await client.get(
            f"{BASESCAN_BASE}/api/token-fifo-metrics",
            params={"contract_address": token_address, "max_pages": "5"},
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception as e:
        logger.warning("FIFO metrics fetch failed for %s: %s", token_address, e)
        return None


async def _get_fifo_briefing(client: httpx.AsyncClient, token_address: str) -> dict | None:
    """Fetch FIFO briefing (wallet tier counts) from mcp-basescan."""
    try:
        resp = await client.get(
            f"{BASESCAN_BASE}/api/token-fifo-briefing",
            params={"contract_address": token_address, "max_pages": "5"},
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception as e:
        logger.warning("FIFO briefing fetch failed for %s: %s", token_address, e)
        return None


async def _get_pool_info(client: httpx.AsyncClient, pair_address: str) -> dict | None:
    """Get basic LP info from Blockscout."""
    if not pair_address:
        return None
    try:
        resp = await client.get(
            f"{BLOCKSCOUT_BASE}/api/v2/addresses/{pair_address}",
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def _score_holder_distribution(fifo_metrics: dict, briefing: dict, policy: dict) -> tuple[int, list[str]]:
    """Score holder distribution quality. Higher = healthier.

    Uses FIFO wallet categorization (leaders, herd, early exits, bagholders)
    and holder concentration metrics.
    """
    behavior_cfg = policy.get("behavior", {})
    red_flags: list[str] = []
    score = 70  # start at decent

    wallets = _normalize_wallets(fifo_metrics.get("wallets", []))
    total_wallets = len(wallets)

    if total_wallets == 0:
        # No data yet — neutral score (token is very new), not a penalty
        return 50, ["no_holder_data_yet"]

    # Check concentration: estimate top holder share from FIFO data
    balances = []
    for addr, data in wallets.items():
        bal = int(data.get("final_balance_raw", "0") or "0")
        balances.append(bal)

    if balances:
        balances.sort(reverse=True)
        total_supply = sum(balances)
        if total_supply > 0:
            top10_share = sum(balances[:10]) / total_supply
            top1_share = balances[0] / total_supply

            if top1_share > behavior_cfg.get("max_single_holder_share", 0.20):
                red_flags.append("single_holder_concentration")
                score -= 25
            if top10_share > behavior_cfg.get("max_top10_holder_share", 0.50):
                red_flags.append("top10_concentration")
                score -= 15

    # Unique holders check
    min_holders = behavior_cfg.get("min_unique_holders", 20)
    if total_wallets < min_holders:
        red_flags.append(f"low_holder_count_{total_wallets}")
        score -= 15

    # Briefing-based analysis
    summary = briefing.get("summary", {})
    leaders = summary.get("leaders", 0)
    early_exits = summary.get("early_exits", 0)
    bagholders = summary.get("bagholders", 0)

    # High early exit ratio is concerning
    if total_wallets > 0 and early_exits / max(total_wallets, 1) > 0.4:
        red_flags.append("high_early_exit_ratio")
        score -= 15

    return max(0, min(100, score)), red_flags


def _score_liquidity_stability(pool_info: dict | None, policy: dict) -> tuple[int, list[str]]:
    """Score LP stability. Higher = more stable."""
    behavior_cfg = policy.get("behavior", {})
    red_flags: list[str] = []

    if not pool_info:
        return 50, ["no_lp_data_yet"]

    # Basic check: does the pool exist and have activity
    score = 60

    coin_balance = pool_info.get("coin_balance", "0")
    try:
        eth_in_pool = int(coin_balance) / 1e18
    except (ValueError, TypeError):
        eth_in_pool = 0

    min_liq = behavior_cfg.get("min_liquidity_usd", 5000)
    # Rough ETH→USD estimate (we don't have a price feed here, use conservative 2000)
    est_liq_usd = eth_in_pool * 2000

    if est_liq_usd < min_liq:
        red_flags.append("low_liquidity")
        score -= 20

    if est_liq_usd > 50000:
        score += 15  # good liquidity
    elif est_liq_usd > 10000:
        score += 5

    return max(0, min(100, score)), red_flags


async def _compute_smart_money_participation(
    fifo_metrics: dict, launch_id: str, policy: dict
) -> tuple[int, int, list[dict]]:
    """Map early buyers to wallet tiers and compute participation scores.

    Returns (smart_money_score, whale_score, notable_participants).
    """
    behavior_cfg = policy.get("behavior", {})
    notable_cfg = policy.get("notable_participants", {})
    min_smart = behavior_cfg.get("min_smart_money_buyers", 2)

    wallets_data = _normalize_wallets(fifo_metrics.get("wallets", []))
    if not wallets_data:
        return 50, 50, []  # neutral — no data yet for new token

    # Get early entrants (entry_rank_pct <= 0.3 = first 30%)
    early_addrs = []
    for addr, data in wallets_data.items():
        rank = data.get("entry_rank_pct", 1.0)
        if rank <= 0.3:
            early_addrs.append(addr.lower())

    if not early_addrs:
        return 50, 50, []  # neutral — no early entrant data yet

    # Look up wallet profiles
    smart_count = 0
    whale_count = 0
    flagged_count = 0
    notables: list[dict] = []

    for addr in early_addrs[:50]:  # cap to avoid huge queries
        row = await db.fetchrow(
            """
            SELECT wallet_tier, wallet_value_score, wallet_performance_score,
                   cluster_id, cluster_tier, alpha_cohort_flag
            FROM wallets WHERE wallet = $1
            """,
            addr,
        )
        if not row:
            continue

        tier = row["wallet_tier"]
        if tier == "TIER_2_SMART_MONEY":
            smart_count += 1
        elif tier == "TIER_1_WHALE":
            whale_count += 1
        elif tier == "TIER_4_FLAGGED":
            flagged_count += 1

        # Build notable participants list
        if tier in ("TIER_1_WHALE", "TIER_2_SMART_MONEY") or row["alpha_cohort_flag"]:
            notables.append({
                "address": addr,
                "wallet_tier": tier,
                "cluster_id": row["cluster_id"],
                "cluster_tier": row["cluster_tier"],
                "alpha_cohort_flag": row["alpha_cohort_flag"],
                "win_rate": None,  # populated by wallet_profiler over time
            })

        # Record participant
        await db.execute(
            """
            INSERT INTO launch_participants (launch_id, wallet, role)
            VALUES ($1, $2, 'EARLY_BUYER')
            ON CONFLICT (launch_id, wallet, role) DO NOTHING
            """,
            launch_id, addr,
        )

    # Score smart money (0-100)
    if smart_count >= min_smart:
        sm_score = min(100, 50 + smart_count * 10)
    elif smart_count > 0:
        sm_score = 40 + smart_count * 5
    else:
        sm_score = 20

    # Penalty for flagged wallets
    if flagged_count > 3:
        sm_score = max(0, sm_score - flagged_count * 10)

    # Score whale participation
    if whale_count >= 2:
        whale_score = min(100, 50 + whale_count * 15)
    elif whale_count == 1:
        whale_score = 45
    else:
        whale_score = 20

    return sm_score, whale_score, notables


async def update_behavior(launch_id: str, token_address: str, pair_address: str | None) -> dict:
    """Full behavior update for a single launch."""
    policy = _load_policy()

    async with httpx.AsyncClient() as client:
        fifo_metrics = await _get_fifo_metrics(client, token_address) or {}
        briefing = await _get_fifo_briefing(client, token_address) or {}
        pool_info = await _get_pool_info(client, pair_address) if pair_address else None

    # 1. Holder distribution
    holder_score, holder_flags = _score_holder_distribution(fifo_metrics, briefing, policy)

    # 2. Liquidity stability
    liq_score, liq_flags = _score_liquidity_stability(pool_info, policy)

    # 3. Smart money & whale participation
    sm_score, whale_score, notables = await _compute_smart_money_participation(
        fifo_metrics, launch_id, policy
    )

    # 4. Compute wallet summary from FIFO data
    wallets_data = _normalize_wallets(fifo_metrics.get("wallets", []))
    tier_counts = {"tier1_whales": 0, "tier2_smart_money": 0, "tier3_retail": 0, "tier4_flagged": 0}
    for addr in list(wallets_data.keys())[:100]:
        row = await db.fetchrow("SELECT wallet_tier FROM wallets WHERE wallet = $1", addr.lower())
        if not row:
            tier_counts["tier3_retail"] += 1
            continue
        t = row["wallet_tier"]
        if t == "TIER_1_WHALE":
            tier_counts["tier1_whales"] += 1
        elif t == "TIER_2_SMART_MONEY":
            tier_counts["tier2_smart_money"] += 1
        elif t == "TIER_4_FLAGGED":
            tier_counts["tier4_flagged"] += 1
        else:
            tier_counts["tier3_retail"] += 1

    # Compute top holders share
    balances = []
    for data in wallets_data.values():
        bal = int(data.get("final_balance_raw", "0") or "0")
        balances.append(bal)
    total_supply = sum(balances) if balances else 0
    top_share = sum(sorted(balances, reverse=True)[:10]) / total_supply * 100 if total_supply > 0 else None

    # Update DB
    notes_patch = {
        "holder_distribution_red_flags": holder_flags,
        "liquidity_red_flags": liq_flags,
    }

    raw_patch = {
        "behavior_update": {
            "fifo_wallet_count": len(wallets_data),
            "briefing_summary": briefing.get("summary", {}),
            "behavior_version": BEHAVIOR_VERSION,
        }
    }

    await db.execute(
        """
        UPDATE launches SET
            holder_distribution = $1,
            liquidity_stability = $2,
            smart_money_participation = $3,
            whale_participation = $4,
            top_holders_share = $5,
            tier1_whales = $6,
            tier2_smart_money = $7,
            tier3_retail = $8,
            tier4_flagged = $9,
            notable_participants = $10::jsonb,
            behavior_version = $11,
            behavior_scored_at = now(),
            notes = notes || $12::jsonb,
            raw_signals = raw_signals || $13::jsonb
        WHERE launch_id = $14
        """,
        holder_score, liq_score, sm_score, whale_score,
        top_share,
        tier_counts["tier1_whales"], tier_counts["tier2_smart_money"],
        tier_counts["tier3_retail"], tier_counts["tier4_flagged"],
        json.dumps(notables),
        BEHAVIOR_VERSION,
        json.dumps(notes_patch),
        json.dumps(raw_patch),
        launch_id,
    )

    logger.info(
        "Behavior update: %s holder=%d liq=%d sm=%d whale=%d notables=%d",
        launch_id, holder_score, liq_score, sm_score, whale_score, len(notables),
    )

    return {
        "launch_id": launch_id,
        "holder_distribution": holder_score,
        "liquidity_stability": liq_score,
        "smart_money_participation": sm_score,
        "whale_participation": whale_score,
        "notable_participants_count": len(notables),
    }


async def run() -> dict:
    """Process launches awaiting behavior analysis (batch of 10 for responsiveness)."""
    window = await db.get_config("behavior_window_minutes") or 60

    rows = await db.fetch(
        """
        SELECT launch_id, token_address, pair_address FROM launches
        WHERE status = 'initial_scored'
          AND behavior_scored_at IS NULL
          AND detected_at > now() - ($1 || ' minutes')::interval
        ORDER BY detected_at ASC
        LIMIT 10
        """,
        str(window),
    )

    results = []
    for row in rows:
        result = await update_behavior(
            str(row["launch_id"]), row["token_address"], row["pair_address"]
        )
        results.append(result)

    return {"processed": len(results), "results": results}
