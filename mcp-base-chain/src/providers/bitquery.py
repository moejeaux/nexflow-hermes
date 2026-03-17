"""
Bitquery provider — on-chain analytics GraphQL API.

Used for aggregated analytics: trending tokens, volume data, DEX trades,
and wallet clustering. Bitquery provides higher-level data than raw RPC.
"""

import os
from typing import Any

import httpx

from src.cache import cached

BITQUERY_ENDPOINT = "https://graphql.bitquery.io"
BASE_NETWORK = "base"


def _get_headers() -> dict[str, str]:
    api_key = os.environ.get("BITQUERY_API_KEY", "")
    if not api_key:
        raise RuntimeError("BITQUERY_API_KEY environment variable is not set")
    return {
        "Content-Type": "application/json",
        "X-API-KEY": api_key,
    }


async def _query(graphql: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a GraphQL query against Bitquery."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        payload: dict[str, Any] = {"query": graphql}
        if variables:
            payload["variables"] = variables
        resp = await client.post(
            BITQUERY_ENDPOINT,
            headers=_get_headers(),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Bitquery GraphQL errors: {data['errors']}")
        return data.get("data", {})


# ---- Trending / Volume Analytics ----

@cached("bitquery")
async def get_trending_tokens(timeframe: str = "24h") -> list[dict[str, Any]]:
    """Get tokens with the highest trade volume on Base DEXs.

    Args:
        timeframe: "1h", "4h", "24h", or "7d"
    """
    # Map timeframe to Bitquery date filter
    since_map = {"1h": "1 hour", "4h": "4 hours", "24h": "1 day", "7d": "7 days"}
    since = since_map.get(timeframe, "1 day")

    query = """
    {
      ethereum(network: base) {
        dexTrades(
          options: {limit: 50, desc: "tradeAmount"}
          date: {since: "%s"}
        ) {
          baseCurrency {
            address
            name
            symbol
          }
          tradeAmount(in: USD)
          trades: count
          buyers: count(uniq: buyers)
          sellers: count(uniq: sellers)
          maximum_price: quotePrice(calculate: maximum)
          minimum_price: quotePrice(calculate: minimum)
        }
      }
    }
    """ % since

    data = await _query(query)
    trades = data.get("ethereum", {}).get("dexTrades", [])
    return [
        {
            "token_address": t["baseCurrency"]["address"],
            "name": t["baseCurrency"]["name"],
            "symbol": t["baseCurrency"]["symbol"],
            "volume_usd": t["tradeAmount"],
            "trade_count": t["trades"],
            "unique_buyers": t["buyers"],
            "unique_sellers": t["sellers"],
            "price_high": t["maximum_price"],
            "price_low": t["minimum_price"],
        }
        for t in trades
    ]


@cached("bitquery")
async def get_token_volume(token_address: str, timeframe: str = "24h") -> dict[str, Any]:
    """Get detailed volume data for a specific token on Base."""
    since_map = {"1h": "1 hour", "4h": "4 hours", "24h": "1 day", "7d": "7 days"}
    since = since_map.get(timeframe, "1 day")

    query = """
    {
      ethereum(network: base) {
        dexTrades(
          baseCurrency: {is: "%s"}
          date: {since: "%s"}
        ) {
          tradeAmount(in: USD)
          trades: count
          buyers: count(uniq: buyers)
          sellers: count(uniq: sellers)
        }
      }
    }
    """ % (token_address, since)

    data = await _query(query)
    trades = data.get("ethereum", {}).get("dexTrades", [])
    if not trades:
        return {"token_address": token_address, "volume_usd": 0, "trades": 0}
    t = trades[0]
    return {
        "token_address": token_address,
        "volume_usd": t["tradeAmount"],
        "trade_count": t["trades"],
        "unique_buyers": t["buyers"],
        "unique_sellers": t["sellers"],
    }


# ---- Wallet Intelligence ----

@cached("bitquery")
async def get_wallet_dex_activity(address: str, days: int = 7) -> list[dict[str, Any]]:
    """Get DEX trading activity for a wallet on Base."""
    query = """
    {
      ethereum(network: base) {
        dexTrades(
          txSender: {is: "%s"}
          date: {since: "%d days"}
          options: {limit: 100, desc: "block.timestamp.iso8601"}
        ) {
          block {
            timestamp {
              iso8601
            }
          }
          baseCurrency {
            address
            symbol
          }
          quoteCurrency {
            address
            symbol
          }
          side
          tradeAmount(in: USD)
          quotePrice
          transaction {
            hash
          }
        }
      }
    }
    """ % (address, days)

    data = await _query(query)
    return data.get("ethereum", {}).get("dexTrades", [])


@cached("bitquery")
async def get_new_pairs(hours: int = 24) -> list[dict[str, Any]]:
    """Get recently created trading pairs on Base DEXs."""
    query = """
    {
      ethereum(network: base) {
        dexTrades(
          options: {limit: 100, desc: "block.timestamp.iso8601"}
          date: {since: "%d hours"}
          tradeIndex: {is: 0}
        ) {
          block {
            timestamp {
              iso8601
            }
          }
          baseCurrency {
            address
            name
            symbol
          }
          quoteCurrency {
            address
            symbol
          }
          exchange {
            fullName
          }
          tradeAmount(in: USD)
        }
      }
    }
    """ % hours

    data = await _query(query)
    return data.get("ethereum", {}).get("dexTrades", [])


@cached("bitquery")
async def get_large_transfers(min_value_usd: float = 50000) -> list[dict[str, Any]]:
    """Detect whale movements — large value transfers on Base."""
    query = """
    {
      ethereum(network: base) {
        transfers(
          options: {limit: 50, desc: "block.timestamp.iso8601"}
          amount: {gteq: %f}
          date: {since: "1 hour"}
        ) {
          block {
            timestamp {
              iso8601
            }
          }
          sender {
            address
          }
          receiver {
            address
          }
          amount
          currency {
            address
            symbol
          }
          amount_usd: amount(in: USD)
          transaction {
            hash
          }
        }
      }
    }
    """ % min_value_usd

    data = await _query(query)
    return data.get("ethereum", {}).get("transfers", [])
