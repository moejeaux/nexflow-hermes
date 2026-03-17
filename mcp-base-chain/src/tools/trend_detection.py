"""
Trend detection tools for Base chain.

Identifies trending tokens, volume anomalies, and smart money patterns
by combining on-chain data with Bitquery analytics.
"""

from typing import Any

from src.providers import bitquery


async def get_trending_tokens(timeframe: str = "24h") -> dict[str, Any]:
    """Get tokens with the highest rising volume/price on Base.

    Args:
        timeframe: "1h", "4h", "24h", or "7d"

    Returns tokens sorted by trade volume with buyer/seller counts
    to help identify genuine momentum vs wash trading.
    """
    valid_timeframes = {"1h", "4h", "24h", "7d"}
    if timeframe not in valid_timeframes:
        timeframe = "24h"

    tokens = await bitquery.get_trending_tokens(timeframe)

    # Enrich with basic analysis
    enriched = []
    for t in tokens:
        buyers = t.get("unique_buyers", 0) or 0
        sellers = t.get("unique_sellers", 0) or 0
        trade_count = t.get("trade_count", 0) or 0

        # Buyer/seller ratio — heavily skewed ratios suggest manipulation
        if sellers > 0:
            bs_ratio = round(buyers / sellers, 2)
        else:
            bs_ratio = float("inf") if buyers > 0 else 0

        # Trades per unique participant — high values suggest wash trading
        total_participants = buyers + sellers
        trades_per_participant = (
            round(trade_count / total_participants, 2)
            if total_participants > 0 else 0
        )

        t["buyer_seller_ratio"] = bs_ratio
        t["trades_per_participant"] = trades_per_participant

        # Flag potential wash trading
        if trades_per_participant > 20:
            t["warning"] = "High trades-per-participant — possible wash trading"
        elif bs_ratio > 10:
            t["warning"] = "Extreme buyer/seller ratio — review carefully"
        else:
            t["warning"] = None

        enriched.append(t)

    return {
        "timeframe": timeframe,
        "trending_tokens": enriched,
        "count": len(enriched),
    }


async def detect_volume_anomalies(token_address: str) -> dict[str, Any]:
    """Flag unusual volume spikes for a specific token.

    Compares short-term volume (1h) against longer-term baseline (24h)
    to detect sudden surges that might indicate insider activity,
    upcoming announcements, or manipulation.
    """
    token_address = token_address.strip()

    # Fetch volume at two timeframes for comparison
    vol_1h = await bitquery.get_token_volume(token_address, "1h")
    vol_24h = await bitquery.get_token_volume(token_address, "24h")

    volume_1h = vol_1h.get("volume_usd", 0) or 0
    volume_24h = vol_24h.get("volume_usd", 0) or 0

    # Expected hourly volume = 24h volume / 24
    expected_hourly = volume_24h / 24 if volume_24h > 0 else 0

    # Calculate anomaly multiplier
    if expected_hourly > 0:
        anomaly_multiplier = round(volume_1h / expected_hourly, 2)
    else:
        anomaly_multiplier = float("inf") if volume_1h > 0 else 0

    # Classify the anomaly
    if anomaly_multiplier >= 10:
        anomaly_level = "EXTREME"
        description = "Volume is 10x+ above expected — major event or manipulation"
    elif anomaly_multiplier >= 5:
        anomaly_level = "HIGH"
        description = "Volume is 5-10x above expected — significant surge"
    elif anomaly_multiplier >= 2:
        anomaly_level = "MODERATE"
        description = "Volume is 2-5x above expected — elevated activity"
    else:
        anomaly_level = "NORMAL"
        description = "Volume is within normal range"

    return {
        "token_address": token_address,
        "volume_1h_usd": volume_1h,
        "volume_24h_usd": volume_24h,
        "expected_hourly_usd": round(expected_hourly, 2),
        "anomaly_multiplier": anomaly_multiplier,
        "anomaly_level": anomaly_level,
        "description": description,
        "trades_1h": vol_1h.get("trade_count", 0),
        "trades_24h": vol_24h.get("trade_count", 0),
    }


async def cluster_wallet_behavior(addresses: list[str]) -> dict[str, Any]:
    """Identify smart money patterns by analyzing wallet cluster behavior.

    For each wallet in the cluster, pulls DEX trading activity and
    identifies common tokens traded, timing patterns, and coordination
    signals that might indicate coordinated smart money.
    """
    if not addresses:
        return {"error": "No addresses provided", "clusters": []}

    # Cap the number of wallets to avoid excessive API calls
    addresses = [a.strip() for a in addresses[:20]]

    wallet_profiles = []
    token_overlap: dict[str, int] = {}  # token -> how many wallets traded it

    for addr in addresses:
        activity = await bitquery.get_wallet_dex_activity(addr, days=7)

        tokens_traded = set()
        total_volume = 0.0

        for trade in activity:
            base = trade.get("baseCurrency", {})
            token_addr = base.get("address", "")
            tokens_traded.add(token_addr)
            total_volume += float(trade.get("tradeAmount", 0) or 0)

            # Track token overlap across wallets
            token_overlap[token_addr] = token_overlap.get(token_addr, 0) + 1

        wallet_profiles.append({
            "address": addr,
            "trade_count": len(activity),
            "unique_tokens": len(tokens_traded),
            "total_volume_usd": round(total_volume, 2),
            "tokens": list(tokens_traded)[:20],  # Cap output
        })

    # Find tokens traded by multiple wallets (coordination signal)
    shared_tokens = {
        token: count
        for token, count in token_overlap.items()
        if count >= 2  # At least 2 wallets traded this token
    }

    # Sort by overlap count
    shared_sorted = sorted(shared_tokens.items(), key=lambda x: x[1], reverse=True)

    coordination_score = 0
    if len(addresses) >= 2:
        # Ratio of shared tokens to total wallets analyzed
        max_possible_overlap = len(addresses)
        top_overlap = shared_sorted[0][1] if shared_sorted else 0
        coordination_score = round((top_overlap / max_possible_overlap) * 100, 1)

    return {
        "wallets_analyzed": len(addresses),
        "wallet_profiles": wallet_profiles,
        "shared_tokens": shared_sorted[:20],
        "coordination_score": coordination_score,
        "note": "Coordination score 0-100. Higher means more wallets are "
                "trading the same tokens — potential coordinated group.",
    }
