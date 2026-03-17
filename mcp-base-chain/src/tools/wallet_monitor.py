"""
Wallet monitoring tools for Base chain.

Tracks wallet transactions, balances, and detects whale movements.
"""

import json
from typing import Any

from src.providers import alchemy, bitquery


async def watch_wallet(address: str) -> dict[str, Any]:
    """Start tracking a wallet by fetching its current state.

    Returns the wallet's current ETH balance, token balances, and
    recent transaction summary. The caller (Hermes) can schedule
    periodic re-checks via cron to detect changes.
    """
    address = address.strip()
    balance = await alchemy.get_balance(address)
    recent_txs = await alchemy.get_transactions(address)

    sent_count = len(recent_txs.get("sent", []))
    received_count = len(recent_txs.get("received", []))

    return {
        "address": address,
        "status": "watching",
        "eth_balance": balance["eth_balance"],
        "token_count": len(balance.get("token_balances", [])),
        "recent_sent": sent_count,
        "recent_received": received_count,
        "note": "Use get_wallet_history() for detailed transaction data. "
                "Schedule periodic calls to detect new activity.",
    }


async def get_wallet_history(address: str, days: int = 7) -> dict[str, Any]:
    """Pull transaction history for an address.

    Combines on-chain transfer data from Alchemy with DEX trading
    activity from Bitquery for a comprehensive view.
    """
    address = address.strip()
    days = min(max(days, 1), 90)  # Clamp to 1-90 days

    # Fetch chain transfers and DEX activity in parallel-friendly way
    transfers = await alchemy.get_transactions(address)
    dex_activity = await bitquery.get_wallet_dex_activity(address, days)

    return {
        "address": address,
        "period_days": days,
        "transfers": {
            "sent": transfers.get("sent", [])[:50],  # Cap output size
            "received": transfers.get("received", [])[:50],
        },
        "dex_trades": dex_activity[:50],
        "summary": {
            "total_sent": len(transfers.get("sent", [])),
            "total_received": len(transfers.get("received", [])),
            "dex_trade_count": len(dex_activity),
        },
    }


async def detect_whale_movements(min_value_usd: float = 50000) -> dict[str, Any]:
    """Flag large-value transfers on Base chain.

    Queries Bitquery for transfers exceeding the minimum USD value
    within the last hour. Useful for tracking smart money flows.
    """
    min_value_usd = max(min_value_usd, 1000)  # Floor at $1k to avoid noise

    transfers = await bitquery.get_large_transfers(min_value_usd)

    movements = []
    for t in transfers:
        movements.append({
            "timestamp": t.get("block", {}).get("timestamp", {}).get("iso8601"),
            "from": t.get("sender", {}).get("address"),
            "to": t.get("receiver", {}).get("address"),
            "amount": t.get("amount"),
            "currency": t.get("currency", {}).get("symbol"),
            "value_usd": t.get("amount_usd"),
            "tx_hash": t.get("transaction", {}).get("hash"),
        })

    return {
        "min_value_usd": min_value_usd,
        "whale_movements": movements,
        "count": len(movements),
    }
