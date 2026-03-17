---
name: Signal Publisher
description: Package, price, and sell blockchain intelligence signals via x402 micropayments.
version: 1.0.0
author: NXFX01
tags: [signals, x402, publishing, intelligence, revenue, blockchain]
---

# Signal Publisher Skill

## Purpose
Package blockchain intelligence from the Chain Watcher into sellable signals. Publish via x402 micropayment endpoints. Track signal accuracy over time and adjust pricing based on performance.

## Signal Format

Every published signal must follow this schema:

```json
{
  "signal_id": "sig_20260314_001",
  "timestamp": "2026-03-14T10:30:00Z",
  "chain": "base",
  "type": "BUY_SIGNAL|SELL_SIGNAL|ALERT|INFO",
  
  "token": {
    "address": "0x...",
    "symbol": "TOKEN",
    "name": "Token Name"
  },
  
  "direction": "BULLISH|BEARISH|NEUTRAL",
  "confidence": 78,
  "risk_level": "LOW|MEDIUM|HIGH",
  
  "evidence": [
    "Whale wallet 0xabc... accumulated $150k in last 2 hours",
    "Volume 7x above 24h average",
    "Contract verified, ownership renounced, 3% max tax",
    "Liquidity locked for 180 days"
  ],
  
  "anti_rug_score": "7/8",
  
  "price_usd": 0.50,
  "expires_at": "2026-03-14T11:30:00Z"
}
```

### Field Rules
- `confidence`: Must be ≥50 to publish. Below 50 is not actionable.
- `evidence`: Minimum 2 evidence points. Each must be factual and verifiable.
- `expires_at`: Signals expire after 1 hour for short-term, 24h for trend signals.
- `risk_level`: Derived from anti-rug checks and confidence score.

## Publishing Workflow

1. **Receive** signal from Chain Watcher (confidence ≥50)
2. **Validate** — re-check anti-rug score, verify evidence is current
3. **Package** — format into signal schema
4. **Price** — calculate price based on confidence and track record
5. **Publish** — push to x402 endpoint via NexFlow SMF
6. **Log** — record signal_id, publish_time, price, and initial market state
7. **Track** — monitor outcome at 1h, 24h, 7d intervals

## Pricing Logic

Base price is adjusted by confidence level and historical accuracy:

```
signal_price = base_price × confidence_multiplier × track_record_multiplier

base_price = $0.25 (initial, adjust over time)

confidence_multiplier:
  - 90-100: 3.0
  - 80-89:  2.0
  - 70-79:  1.5
  - 60-69:  1.0
  - 50-59:  0.5

track_record_multiplier:
  - Accuracy >80% (last 30 signals): 2.0
  - Accuracy 60-80%: 1.0
  - Accuracy 40-60%: 0.5
  - Accuracy <40%: 0.25 (consider pausing publishing)
  - Insufficient data (<10 signals): 1.0
```

### Price Bounds
- Minimum: $0.05 (below this isn't worth the transaction cost)
- Maximum: $5.00 (above this reduces volume too much early on)

## Quality Tracking

Maintain an accuracy ledger for every published signal:

```json
{
  "signal_id": "sig_20260314_001",
  "published_at": "2026-03-14T10:30:00Z",
  "confidence": 78,
  "direction": "BULLISH",
  "token_address": "0x...",
  "price_at_signal": 0.0045,
  "price_1h": 0.0052,
  "price_24h": 0.0061,
  "price_7d": 0.0038,
  "outcome_1h": "CORRECT",
  "outcome_24h": "CORRECT",
  "outcome_7d": "INCORRECT",
  "revenue_earned": 0.50,
  "buyers_count": 3
}
```

### Accuracy Definition
- **BULLISH signal**: CORRECT if price increased by ≥5% in the measured timeframe
- **BEARISH signal**: CORRECT if price decreased by ≥5% in the measured timeframe
- **NEUTRAL/INFO**: Not scored for directional accuracy

### Rolling Metrics (updated daily)
```
accuracy_30d = correct_signals / total_scored_signals (last 30 days)
avg_confidence = mean(confidence) of published signals
revenue_per_signal = total_revenue / total_signals
best_performing_type = type with highest accuracy
```

## Publishing Channels

### x402 Micropayment Endpoint
- Primary channel — signals are gated behind x402 payment
- Use NexFlow SMF for payment verification
- Deliver signal content only after payment confirms

### Free Tier (for building audience)
- Publish 1 signal per day for free with delayed delivery (30 min after paid subscribers)
- Include a teaser: token symbol + direction only, no evidence or address
- Goal: convert free followers to paid subscribers

## Revenue Reporting

After each signal sale:
```python
log_revenue(
    agent_id="NXF014",
    amount=signal_price,
    source="signal_sale",
    job_id=signal_id
)
```

## Safety Rules

- **Never recommend position sizes** — signals are information only
- **Never guarantee outcomes** — always include "Not financial advice" disclaimer
- **Don't publish signals for tokens that failed anti-rug check**
- **Pause publishing if accuracy drops below 40%** for 2 consecutive weeks
- **Rate limit**: Max 20 signals per day to maintain quality
- **No front-running**: Signal must be published simultaneously to all subscribers
