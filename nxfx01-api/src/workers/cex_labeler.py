"""CEX Wallet Labeler — identifies CEX-funded wallets by tracing funding sources.

For each deployer and early buyer wallet, inspects funding sources over N hops
to determine if the wallet is funded from known CEX hot wallets (Binance,
Coinbase, MEXC, OKX, Bybit, Kraken, etc.).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from src import db

logger = logging.getLogger("nxfx01.workers.cex_labeler")

BLOCKSCOUT_BASE = "https://base.blockscout.com"
MAX_FUNDING_HOPS = 2  # configurable via policy


async def _get_cex_wallets() -> dict[str, str]:
    """Load known CEX hot wallet addresses from DB.

    Returns {address: exchange_name}.
    """
    rows = await db.fetch(
        "SELECT address, exchange_name FROM cex_hot_wallets WHERE confidence >= 0.5"
    )
    return {r["address"]: r["exchange_name"] for r in rows}


async def _get_inbound_transfers(
    client: httpx.AsyncClient,
    address: str,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Fetch inbound transfers to address from Blockscout."""
    try:
        resp = await client.get(
            f"{BLOCKSCOUT_BASE}/api/v2/addresses/{address}/transactions",
            params={"filter": "to"},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("items", [])[:limit]
    except Exception as e:
        logger.warning("Failed to fetch transfers for %s: %s", address, e)
        return []


async def label_wallet(
    client: httpx.AsyncClient,
    wallet_address: str,
    cex_wallets: dict[str, str],
    max_hops: int = MAX_FUNDING_HOPS,
) -> dict[str, Any]:
    """Trace funding sources of a wallet to identify CEX origins.

    Args:
        client: httpx async client.
        wallet_address: The wallet to analyze.
        cex_wallets: Preloaded {address: exchange_name} map.
        max_hops: Maximum depth to trace (default 2).

    Returns dict with:
        is_cex_funded (bool), cex_funding_share (float 0-1),
        funding_cex_list (list[str]), cex_funding_detail (dict).
    """
    result: dict[str, Any] = {
        "is_cex_funded": False,
        "cex_funding_share": 0.0,
        "funding_cex_list": [],
        "cex_funding_detail": {},
    }

    addr = wallet_address.lower()

    # Check if wallet itself is a known CEX wallet
    if addr in cex_wallets:
        result["is_cex_funded"] = True
        result["cex_funding_share"] = 1.0
        result["funding_cex_list"] = [cex_wallets[addr]]
        return result

    # Trace inbound transfers (hop 1)
    transfers = await _get_inbound_transfers(client, addr)
    if not transfers:
        return result

    total_value = 0.0
    cex_value = 0.0
    cex_names: set[str] = set()
    cex_detail: dict[str, float] = {}

    for tx in transfers:
        value_str = tx.get("value", "0")
        try:
            value_wei = int(value_str)
            value_eth = value_wei / 1e18
        except (ValueError, TypeError):
            continue

        total_value += value_eth
        from_addr = (tx.get("from", {}).get("hash", "") or "").lower()

        # Direct CEX match (hop 1)
        if from_addr in cex_wallets:
            exchange = cex_wallets[from_addr]
            cex_value += value_eth
            cex_names.add(exchange)
            cex_detail[exchange] = cex_detail.get(exchange, 0) + value_eth * 2000  # rough USD
            continue

        # Hop 2: check if the sender was CEX-funded
        if max_hops >= 2:
            hop2_transfers = await _get_inbound_transfers(client, from_addr, limit=10)
            for tx2 in hop2_transfers:
                from_addr2 = (tx2.get("from", {}).get("hash", "") or "").lower()
                if from_addr2 in cex_wallets:
                    # Attribute partial value (discounted for hop distance)
                    exchange = cex_wallets[from_addr2]
                    attributed = value_eth * 0.5  # 50% attribution for 2-hop
                    cex_value += attributed
                    cex_names.add(exchange)
                    cex_detail[exchange] = cex_detail.get(exchange, 0) + attributed * 2000
                    break

    if total_value > 0:
        share = cex_value / total_value
        result["cex_funding_share"] = round(min(1.0, share), 4)
        result["is_cex_funded"] = share >= 0.30  # configurable threshold
        result["funding_cex_list"] = sorted(cex_names)
        result["cex_funding_detail"] = {k: round(v, 2) for k, v in cex_detail.items()}

    return result


async def label_and_persist(wallet_address: str) -> dict[str, Any]:
    """Label a wallet for CEX funding and save to DB."""
    cex_wallets = await _get_cex_wallets()

    async with httpx.AsyncClient() as client:
        result = await label_wallet(client, wallet_address, cex_wallets)

    # Upsert to wallets table
    await db.execute(
        """
        UPDATE wallets SET
            is_cex_funded = $1,
            cex_funding_share = $2,
            funding_cex_list = $3,
            cex_funding_detail = $4::jsonb,
            last_seen_at = now()
        WHERE wallet = $5
        """,
        result["is_cex_funded"],
        result["cex_funding_share"],
        result["funding_cex_list"],
        json.dumps(result["cex_funding_detail"]),
        wallet_address.lower(),
    )

    logger.debug(
        "CEX label: %s → cex=%s share=%.2f exchanges=%s",
        wallet_address, result["is_cex_funded"],
        result["cex_funding_share"], result["funding_cex_list"],
    )

    return result


async def run() -> dict:
    """Label all deployer wallets and recent early buyers that haven't been labeled."""
    # Deployers first (highest priority)
    deployers = await db.fetch(
        """
        SELECT DISTINCT deployer_address FROM launches
        WHERE deployer_address IS NOT NULL
          AND detected_at > now() - interval '7 days'
          AND deployer_address NOT IN (
              SELECT wallet FROM wallets WHERE is_cex_funded IS NOT NULL
          )
        LIMIT 50
        """
    )

    results = []
    for row in deployers:
        # Ensure wallet exists before labeling
        await db.execute(
            """
            INSERT INTO wallets (wallet) VALUES ($1)
            ON CONFLICT (wallet) DO NOTHING
            """,
            row["deployer_address"].lower(),
        )
        r = await label_and_persist(row["deployer_address"])
        results.append({"wallet": row["deployer_address"], **r})

    return {"processed": len(results), "results": results}
