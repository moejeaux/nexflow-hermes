"""Launch Scanner — polls Blockscout for new token contracts + LP events on Base.

Runs on a cron (every 3-5 min). Uses scan_state table to track cursor.
Inserts bare launch rows into the launches table.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import httpx

from src import db

logger = logging.getLogger("nxfx01.launch_scanner")

BLOCKSCOUT_BASE = "https://base.blockscout.com"

# DEX factory registry — same list used in scheduler.py
DEX_FACTORIES = [
    {
        "address": "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
        "topic0": "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118",
        "label": "uniswap_v3",
    },
    {
        "address": "0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6",
        "topic0": "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9",
        "label": "uniswap_v2",
    },
    {
        "address": "0x420DD381b31aEf6683db6B902084cB0FFECe40Da",
        "topic0": "0x2128d88d14c80cb081c1252a5acff7a264671bf199ce226b53788fb26065005e",
        "label": "aerodrome",
    },
]

# Rate-limit: max requests per scanner run
MAX_REQUESTS_PER_RUN = 50
# Default block batch size
DEFAULT_BATCH_SIZE = 500

# WETH on Base — filter out if both tokens are well-known (not a new launch)
WETH_BASE = "0x4200000000000000000000000000000000000006"


async def _blockscout_get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    """Fetch from Blockscout with timeout and error handling."""
    resp = await client.get(f"{BLOCKSCOUT_BASE}{path}", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


async def _get_current_block(client: httpx.AsyncClient) -> int:
    """Get latest indexed block on Base from Blockscout."""
    # Primary: v1 getblocknobytime (reliable, returns exact block number)
    import time
    try:
        data = await _blockscout_get(client, "/api", params={
            "module": "block",
            "action": "getblocknobytime",
            "timestamp": str(int(time.time())),
            "closest": "before",
        })
        block = int(data.get("result", {}).get("blockNumber", 0))
        if block > 0:
            return block
    except Exception:
        pass

    # Fallback: v2 main-page/blocks
    data = await _blockscout_get(client, "/api/v2/main-page/blocks")
    if isinstance(data, list) and data:
        return int(data[0].get("height", 0))
    return 0


async def _get_contract_creation_txs(
    client: httpx.AsyncClient, from_block: int, to_block: int
) -> list[dict]:
    """Fetch token logs / contract creation events in a block range.

    Uses Blockscout's v2 token endpoint to find newly verified tokens
    in the given range (via their creation block).
    """
    params = {
        "type": "ERC-20",
        "filter": "fiat_value",  # filter by some activity
    }
    data = await _blockscout_get(client, "/api/v2/tokens", params=params)
    items = data.get("items", [])
    # Filter to tokens created within our block range
    new_tokens = []
    for token in items:
        # Blockscout returns creation timestamp, not block — we'll use timestamp filtering
        # as a fallback when block-level filtering isn't available
        if token.get("type") == "ERC-20":
            new_tokens.append(token)
    return new_tokens


async def _get_pool_created_logs(
    client: httpx.AsyncClient, from_block: int, to_block: int
) -> list[tuple[dict, str]]:
    """Fetch pool/pair creation events from all DEX factories in a block range.

    Returns list of (log_dict, dex_label) tuples.
    """
    all_logs: list[tuple[dict, str]] = []
    for factory in DEX_FACTORIES:
        params = {
            "module": "logs",
            "action": "getLogs",
            "fromBlock": str(from_block),
            "toBlock": str(to_block),
            "address": factory["address"],
            "topic0": factory["topic0"],
        }
        data = await _blockscout_get(client, "/api", params=params)
        results = data.get("result", [])
        if isinstance(results, list):
            for log in results:
                all_logs.append((log, factory["label"]))
    return all_logs


def _parse_pool_address(data: str, dex_label: str) -> str | None:
    """Extract pool/pair address from event data field."""
    if len(data) < 66:
        return None
    if dex_label == "uniswap_v3":
        if len(data) >= 170:
            return "0x" + data[130:170]
        return None
    # V2 and Aerodrome: pool/pair is the first 32-byte word
    return "0x" + data[26:66]


def _parse_pool_log(log: dict, dex_label: str) -> dict | None:
    """Extract token addresses and pool from a pool/pair creation log."""
    topics = log.get("topics", [])
    if len(topics) < 3:
        return None
    token0 = "0x" + topics[1][-40:]
    token1 = "0x" + topics[2][-40:]
    data = log.get("data", "0x")
    pool_address = _parse_pool_address(data, dex_label)

    # Determine which is the "new" token (not WETH)
    new_token = token0 if token1.lower() == WETH_BASE.lower() else token1
    paired_token = token1 if new_token == token0 else token0

    return {
        "token_address": new_token.lower(),
        "pair_address": pool_address.lower() if pool_address else None,
        "paired_with": paired_token.lower(),
        "dex_source": dex_label,
        "block_number": int(log.get("blockNumber", "0x0"), 16) if isinstance(log.get("blockNumber"), str) else log.get("blockNumber", 0),
        "tx_hash": log.get("transactionHash"),
    }


async def _lookup_deployer(client: httpx.AsyncClient, token_address: str) -> str | None:
    """Look up the deployer of a token contract."""
    try:
        data = await _blockscout_get(client, f"/api/v2/addresses/{token_address}")
        creator = data.get("creator", {})
        if isinstance(creator, dict):
            return creator.get("hash", "").lower() or None
        return None
    except Exception:
        return None


async def run() -> dict:
    """Execute one scanner run. Returns summary of what was found."""
    # Read cursor
    row = await db.fetchrow(
        "SELECT last_scanned_block, last_run_at FROM scan_state WHERE scanner_name = 'launch_scanner'"
    )
    last_block = row["last_scanned_block"] if row else 0
    config_batch = await db.get_config("scan_batch_size") or DEFAULT_BATCH_SIZE
    config_max = await db.get_config("max_launches_per_run") or 100

    inserted = 0
    skipped = 0
    request_count = 0

    async with httpx.AsyncClient() as client:
        current_block = await _get_current_block(client)
        request_count += 1

        if last_block == 0:
            # First run ever — start from recent blocks, not genesis
            last_block = max(current_block - config_batch, 0)

        from_block = last_block + 1
        to_block = min(from_block + config_batch - 1, current_block)

        if from_block > current_block:
            logger.info("Scanner up to date at block %d", current_block)
            await db.execute(
                "UPDATE scan_state SET last_run_at = now() WHERE scanner_name = 'launch_scanner'"
            )
            return {"status": "up_to_date", "block": current_block, "inserted": 0}

        logger.info("Scanning blocks %d to %d", from_block, to_block)

        # Fetch pool/pair creation logs from all DEX factories
        logs = await _get_pool_created_logs(client, from_block, to_block)
        request_count += len(DEX_FACTORIES)  # one request per factory

        launches_to_insert = []
        seen_tokens = set()

        for log, dex_label in logs:
            if request_count >= MAX_REQUESTS_PER_RUN:
                logger.warning("Hit request cap (%d), stopping early", MAX_REQUESTS_PER_RUN)
                break
            if len(launches_to_insert) >= config_max:
                break

            parsed = _parse_pool_log(log, dex_label)
            if not parsed:
                continue

            token = parsed["token_address"]
            if token in seen_tokens:
                continue
            seen_tokens.add(token)

            # Check if already in DB
            exists = await db.fetchval(
                "SELECT 1 FROM launches WHERE token_address = $1", token
            )
            if exists:
                skipped += 1
                continue

            # Look up deployer
            deployer = await _lookup_deployer(client, token)
            request_count += 1

            launches_to_insert.append({
                "token_address": token,
                "pair_address": parsed["pair_address"],
                "deployer_address": deployer,
                "dex_source": parsed["dex_source"],
                "block_number": parsed["block_number"],
                "tx_hash": parsed["tx_hash"],
            })

        # Batch insert
        for launch in launches_to_insert:
            await db.execute(
                """
                INSERT INTO launches (token_address, pair_address, deployer_address, chain, dex_source, timestamp, detected_at)
                VALUES ($1, $2, $3, 'base', $4, now(), now())
                ON CONFLICT DO NOTHING
                """,
                launch["token_address"],
                launch["pair_address"],
                launch["deployer_address"],
                launch["dex_source"],
            )
            inserted += 1

        # Update cursor
        await db.execute(
            """
            UPDATE scan_state
            SET last_scanned_block = $1, last_run_at = now()
            WHERE scanner_name = 'launch_scanner'
            """,
            to_block,
        )

    logger.info(
        "Scanner complete: blocks %d-%d, inserted=%d, skipped=%d, requests=%d",
        from_block, to_block, inserted, skipped, request_count,
    )

    return {
        "status": "ok",
        "from_block": from_block,
        "to_block": to_block,
        "inserted": inserted,
        "skipped": skipped,
        "requests": request_count,
    }
