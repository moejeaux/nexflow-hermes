# mcp-base-chain

MCP server providing Base chain intelligence tools for Hermes Agent. Exposes wallet monitoring, pool event tracking, token analysis, and trend detection via the MCP protocol.

## Data Sources

| Provider | Purpose | Rate Limits |
|----------|---------|-------------|
| **Alchemy** | Base chain RPC — transactions, logs, contract calls | Varies by plan |
| **Bitquery** | On-chain analytics — trending tokens, volume, DEX data | 10 req/min (free) |

## Tools

### Wallet Monitoring
- `watch_wallet(address)` — Start tracking a wallet's transactions in real-time
- `get_wallet_history(address, days)` — Pull transaction history for an address
- `detect_whale_movements(min_value_usd)` — Flag large transfers on Base

### Token / Pool Events
- `monitor_new_pools()` — Watch for PairCreated events on Uniswap V3 / Base DEXs
- `analyze_token_contract(address)` — Check for honeypot indicators, tax, ownership, liquidity
- `get_pool_liquidity(pair_address)` — Current liquidity and volume stats

### Trend Detection
- `get_trending_tokens(timeframe)` — Tokens with rising volume/price on Base
- `detect_volume_anomalies(token_address)` — Flag unusual volume spikes
- `cluster_wallet_behavior(addresses)` — Identify smart money movement patterns

## Setup

```bash
# From the mcp-base-chain directory
pip install -e .
```

## Required Environment Variables

```bash
ALCHEMY_API_KEY=your_alchemy_key_here
BITQUERY_API_KEY=your_bitquery_key_here
```

## Running Standalone (for testing)

```bash
python -m src.server
```

## Example Tool Calls (MCP JSON-RPC)

### Watch a wallet
```json
{
  "method": "tools/call",
  "params": {
    "name": "watch_wallet",
    "arguments": {
      "address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
    }
  }
}
```

### Get trending tokens (24h)
```json
{
  "method": "tools/call",
  "params": {
    "name": "get_trending_tokens",
    "arguments": {
      "timeframe": "24h"
    }
  }
}
```

### Analyze token contract
```json
{
  "method": "tools/call",
  "params": {
    "name": "analyze_token_contract",
    "arguments": {
      "address": "0x..."
    }
  }
}
```

## Architecture

```
src/
├── server.py           # MCP server entry point, tool registration
├── cache.py            # TTL caching + rate limiting
├── providers/
│   ├── alchemy.py      # Base chain RPC (web3 + httpx)
│   └── bitquery.py     # GraphQL analytics API
└── tools/
    ├── wallet_monitor.py
    ├── pool_events.py
    ├── contract_analyzer.py
    └── trend_detection.py
```

Rate limiting and caching are applied at the provider layer to prevent burning API credits on redundant calls.
