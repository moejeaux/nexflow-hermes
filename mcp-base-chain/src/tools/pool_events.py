"""
Pool event monitoring tools for Base chain.

Watches for new trading pair creation events on Uniswap V3 and other
Base DEXs. Tracks liquidity metrics for existing pools.
"""

from typing import Any

from src.providers import alchemy, bitquery

# Uniswap V3 Factory on Base — emits PoolCreated events
UNISWAP_V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"

# PoolCreated event signature: PoolCreated(address,address,uint24,int24,address)
POOL_CREATED_TOPIC = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"


async def monitor_new_pools() -> dict[str, Any]:
    """Watch for PairCreated / PoolCreated events on Base DEXs.

    Fetches recent pool creation events from Uniswap V3 Factory on Base,
    plus new pairs detected via Bitquery across all Base DEXs.
    """
    # On-chain: fetch PoolCreated logs from last ~256 blocks
    current_block = await alchemy.get_block_number()
    from_block = hex(max(current_block - 256, 0))

    logs = await alchemy.get_logs(
        address=UNISWAP_V3_FACTORY,
        topics=[POOL_CREATED_TOPIC],
        from_block=from_block,
        to_block="latest",
    )

    on_chain_pools = []
    for log in logs:
        topics = log.get("topics", [])
        on_chain_pools.append({
            "block_number": int(log.get("blockNumber", "0x0"), 16),
            "tx_hash": log.get("transactionHash"),
            "token0": "0x" + topics[1][-40:] if len(topics) > 1 else None,
            "token1": "0x" + topics[2][-40:] if len(topics) > 2 else None,
            "raw_data": log.get("data"),
        })

    # Analytics: new pairs from Bitquery (broader coverage across DEXs)
    new_pairs = await bitquery.get_new_pairs(hours=24)

    return {
        "on_chain_pool_events": on_chain_pools,
        "on_chain_count": len(on_chain_pools),
        "bitquery_new_pairs": new_pairs[:30],  # Cap output
        "bitquery_count": len(new_pairs),
        "factory_monitored": UNISWAP_V3_FACTORY,
    }


async def get_pool_liquidity(pair_address: str) -> dict[str, Any]:
    """Get current liquidity and volume stats for a trading pair.

    Reads on-chain pool state (reserves/liquidity) and combines with
    Bitquery volume data for the pair's base token.
    """
    pair_address = pair_address.strip()

    # Read pool's slot0 for current tick/price (Uniswap V3)
    # slot0() selector: 0x3850c7bd
    slot0_data = await alchemy.call_contract(pair_address, "0x3850c7bd")

    # Read liquidity(): 0x1a686502
    liquidity_data = await alchemy.call_contract(pair_address, "0x1a686502")

    # Parse liquidity from the hex response
    liquidity = int(liquidity_data, 16) if liquidity_data != "0x" else 0

    return {
        "pair_address": pair_address,
        "liquidity_raw": liquidity,
        "slot0_raw": slot0_data,
        "note": "Raw values from Uniswap V3 pool. Use token decimals to "
                "convert to human-readable amounts.",
    }
