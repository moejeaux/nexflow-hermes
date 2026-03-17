---
name: Chain Watcher
description: Base chain monitoring, pattern recognition, and anti-rug analysis for blockchain intelligence.
version: 1.0.0
author: NXFX01
tags: [blockchain, base, monitoring, defi, anti-rug, patterns]
---

# Chain Watcher Skill

## Purpose
Monitor Base chain for actionable intelligence: new pool creation, whale movements, volume anomalies, contract deployments, and emerging patterns. All monitoring is **read-only** — no trades are executed.

## What to Monitor

### Priority 1 — Continuous (every 60 seconds)
- **New pool creation** on Uniswap V3 and major Base DEXs
- **Whale movements** — transfers >$50k in ETH, USDC, or major tokens
- **Volume anomalies** — tokens with 5x+ normal volume spike

### Priority 2 — Frequent (every 5 minutes)
- **Tracked whale wallets** — known smart money addresses
- **Token contract deployments** — new ERC-20s deployed on Base
- **Liquidity events** — large add/remove liquidity transactions

### Priority 3 — Periodic (every hour)
- **Cross-reference** whale movements with new pool activity
- **Update** trending token rankings
- **Cluster analysis** on active wallet groups

## Anti-Rug Checklist

Before scoring any token as a potential signal, run this checklist:

| Check | Method | Pass Criteria |
|-------|--------|---------------|
| Contract verified? | Analyze bytecode | Source verified on Basescan, or clean decompilation |
| Ownership | `owner()` call | Renounced (zero address) or timelocked |
| Hidden taxes | Simulate buy/sell | Tax ≤5% in each direction |
| Honeypot test | Simulate sell after buy | Sell succeeds, slippage < 10% |
| Liquidity locked | Check LP token holder | Locked in known locker for ≥30 days |
| Wallet diversity | Top holder analysis | Top 10 holders control <50% of supply |
| Contract patterns | Bytecode analysis | No selfdestruct, no dynamic fee setters |
| Age | Block timestamp | Contract deployed >2 hours ago |

**Minimum 6/8 checks must pass to generate a signal.**

## Signal Scoring

Each detected event gets a confidence score:

```
confidence = base_score × quality_multiplier × time_decay

base_score:
  - New pool + verified contract + locked liquidity = 80
  - Whale accumulation of known token = 70
  - Volume anomaly on established token = 60
  - New unverified token = 30

quality_multiplier:
  - Anti-rug checklist 8/8 = 1.2
  - Anti-rug checklist 7/8 = 1.0
  - Anti-rug checklist 6/8 = 0.8
  - Below 6/8 = DO NOT SIGNAL

time_decay:
  - Detected within 5 min = 1.0
  - 5-30 min = 0.9
  - 30-60 min = 0.7
  - >1 hour = 0.5
```

### Risk Assessment Categories
- **HIGH CONFIDENCE (≥70)**: Strong pattern, multiple confirming signals
- **MODERATE (50-69)**: Single strong signal or multiple weak ones
- **LOW (30-49)**: Interesting but insufficient evidence — monitor only
- **SKIP (<30)**: Not actionable

## Alerting Rules

- **Confidence ≥70**: Send Telegram alert immediately with full analysis
- **Confidence 50-69**: Log and include in next hourly summary
- **Confidence <50**: Log only, no notification

## Pattern Logging Format

Every observation gets logged for machine learning over time:

```json
{
  "event_id": "evt_20260314_001",
  "timestamp": "2026-03-14T10:15:00Z",
  "event_type": "new_pool|whale_move|volume_spike|contract_deploy",
  "token_address": "0x...",
  "token_symbol": "TOKEN",
  "details": {
    "pool_address": "0x...",
    "initial_liquidity_usd": 50000,
    "whale_address": "0x...",
    "transfer_amount_usd": 150000
  },
  "anti_rug_result": {
    "checks_passed": 7,
    "checks_total": 8,
    "failed_checks": ["liquidity_lock"],
    "details": {}
  },
  "confidence_score": 72,
  "risk_level": "HIGH_CONFIDENCE",
  "outcome": null,
  "outcome_24h": null,
  "outcome_7d": null
}
```

**Important:** Fill in `outcome_24h` and `outcome_7d` retroactively to build a training dataset for future pattern recognition improvements.

## Tracked Whale Wallets

Maintain a dynamic list of whale wallets (start with publicly known smart money):
- Add wallets that consistently appear in profitable early trades
- Remove wallets that go dormant for >30 days
- Tag wallets with behavior labels: "early buyer", "liquidity provider", "arbitrageur"

## Safety Rules

- **NO TRADES**: This skill is observation-only. No buy/sell execution.
- **No position recommendations with dollar amounts** until 30 days of pattern data collected
- **Rate limit API calls**: Use cached data when possible, never exceed provider limits
- **Log everything**: Every observation must be persisted for future analysis
