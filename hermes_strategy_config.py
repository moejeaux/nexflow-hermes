#!/usr/bin/env python3
"""
hermes_strategy_config.py — Hermes-level strategy configuration manager.

Selects high-performing wallets from base_wallet_performance_window,
filters out contracts/bots/CEX via base_wallet_tiers, and writes the
wallet_follow_v1 strategy config into nxfx01_strategy_config.

Hermes decides WHO to follow, WHERE (which pools), and HOW BIG (sizing).
Sub-agents read this config and execute.

Dependencies: psycopg2-binary, python-dotenv
    pip install psycopg2-binary python-dotenv

Usage:
    # Select top wallets and update strategy config
    python hermes_strategy_config.py \\
        --min-trades 10 --min-win-rate 0.55 --min-pnl 100 \\
        --pools 0xabc...,0xdef... \\
        --top-n 5 --enable

    # Dry run (show selected wallets without updating DB)
    python hermes_strategy_config.py --min-trades 5 --dry-run

    # Disable the strategy
    python hermes_strategy_config.py --disable
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("hermes_config")

STRATEGY_NAME = "wallet_follow_v1"


def select_follow_wallets(
    conn,
    min_trades: int = 10,
    min_win_rate: float = 0.55,
    min_pnl: float = 100.0,
    perf_tiers: list[str] | None = None,
    top_n: int = 5,
) -> list[dict]:
    """
    Query base_wallet_performance_window for wallets meeting thresholds,
    excluding known contracts, CEXes, bridges, and routers.
    Returns list of dicts with wallet info, ordered by realized PnL.
    """
    if perf_tiers is None:
        perf_tiers = ["SMART", "ELITE"]

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                p.wallet,
                p.total_trades,
                p.winning_trades,
                p.realized_pnl_usd,
                p.win_rate,
                p.profit_factor,
                p.perf_tier,
                p.window_start,
                p.window_days
            FROM base_wallet_performance_window p
            LEFT JOIN base_wallet_tiers t ON t.wallet = p.wallet
            WHERE p.total_trades >= %s
              AND p.win_rate >= %s
              AND p.realized_pnl_usd >= %s
              AND (p.perf_tier = ANY(%s) OR p.perf_tier IS NULL)
              -- Exclude non-human wallets.
              AND COALESCE(t.is_contract, FALSE) = FALSE
              AND COALESCE(t.is_cex_candidate, FALSE) = FALSE
              AND COALESCE(t.is_bridge_candidate, FALSE) = FALSE
              AND COALESCE(t.is_router_candidate, FALSE) = FALSE
            ORDER BY p.window_start DESC, p.realized_pnl_usd DESC
            LIMIT %s
        """, (min_trades, min_win_rate, min_pnl, perf_tiers, top_n))

        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    return rows


def update_strategy_config(
    conn,
    wallets: list[str],
    pools: list[str],
    size_fraction: float = 0.2,
    min_leader_usd: float = 50.0,
    max_trade_usd: float = 200.0,
    max_capital_usd: float = 1000.0,
    target_win_rate: float = 0.55,
    max_drawdown_pct: float = 0.15,
    enable: bool = False,
):
    """Upsert wallet_follow_v1 strategy config."""
    params = {
        "wallets": wallets,
        "pools": pools,
        "size_fraction": size_fraction,
        "min_leader_usd": min_leader_usd,
    }
    kpis = {
        "target_win_rate": target_win_rate,
        "max_drawdown_pct": max_drawdown_pct,
        "review_cadence_hours": 24,
    }

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO nxfx01_strategy_config
                (strategy_name, enabled, max_capital_usd, max_trade_usd,
                 target_win_rate, max_drawdown_pct, params_json, kpis_json,
                 updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, now())
            ON CONFLICT (strategy_name) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                max_capital_usd = EXCLUDED.max_capital_usd,
                max_trade_usd = EXCLUDED.max_trade_usd,
                target_win_rate = EXCLUDED.target_win_rate,
                max_drawdown_pct = EXCLUDED.max_drawdown_pct,
                params_json = EXCLUDED.params_json,
                kpis_json = EXCLUDED.kpis_json,
                updated_at = now()
        """, (
            STRATEGY_NAME,
            enable,
            max_capital_usd,
            max_trade_usd,
            target_win_rate,
            max_drawdown_pct,
            json.dumps(params),
            json.dumps(kpis),
        ))
    conn.commit()


def main():
    parser = argparse.ArgumentParser(
        description="Hermes wallet_follow_v1 strategy config manager"
    )
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument("--min-win-rate", type=float, default=0.55)
    parser.add_argument("--min-pnl", type=float, default=100.0)
    parser.add_argument(
        "--perf-tiers", type=str, default="SMART,ELITE",
        help="Comma-separated perf tiers (default: SMART,ELITE)"
    )
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument(
        "--pools", type=str, default="",
        help="Comma-separated pool addresses for the allowlist"
    )
    parser.add_argument("--size-fraction", type=float, default=0.2)
    parser.add_argument("--min-leader-usd", type=float, default=50.0)
    parser.add_argument("--max-trade-usd", type=float, default=200.0)
    parser.add_argument("--max-capital-usd", type=float, default=1000.0)
    parser.add_argument("--target-win-rate", type=float, default=0.55)
    parser.add_argument("--max-drawdown-pct", type=float, default=0.15)
    parser.add_argument("--enable", action="store_true", help="Enable the strategy")
    parser.add_argument("--disable", action="store_true", help="Disable the strategy")
    parser.add_argument("--dry-run", action="store_true", help="Show selection without updating DB")
    args = parser.parse_args()

    load_dotenv()

    db_url = os.environ.get("NXFX01_DATABASE_URL")
    if not db_url:
        log.error("NXFX01_DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(db_url)

    try:
        if args.disable:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE nxfx01_strategy_config SET enabled = FALSE, updated_at = now() "
                    "WHERE strategy_name = %s",
                    (STRATEGY_NAME,),
                )
            conn.commit()
            log.info(f"Disabled {STRATEGY_NAME}.")
            return

        # Select wallets.
        perf_tiers = [t.strip() for t in args.perf_tiers.split(",")]
        wallets = select_follow_wallets(
            conn,
            min_trades=args.min_trades,
            min_win_rate=args.min_win_rate,
            min_pnl=args.min_pnl,
            perf_tiers=perf_tiers,
            top_n=args.top_n,
        )

        log.info(f"Selected {len(wallets)} wallets:")
        for w in wallets:
            def _f(v):
                return float(v) if v is not None else 0
            log.info(
                f"  {w['wallet'][:12]}... | "
                f"trades={w['total_trades']} "
                f"wr={_f(w['win_rate']):.2%} "
                f"pnl=${_f(w['realized_pnl_usd']):,.0f} "
                f"tier={w.get('perf_tier', '?')}"
            )

        pools = [p.strip().lower() for p in args.pools.split(",") if p.strip()]
        wallet_addrs = [w["wallet"] for w in wallets]

        if args.dry_run:
            log.info("Dry run — not updating DB.")
            log.info(f"Would set wallets={wallet_addrs}")
            log.info(f"Would set pools={pools}")
            return

        # Update config.
        update_strategy_config(
            conn,
            wallets=wallet_addrs,
            pools=pools,
            size_fraction=args.size_fraction,
            min_leader_usd=args.min_leader_usd,
            max_trade_usd=args.max_trade_usd,
            max_capital_usd=args.max_capital_usd,
            target_win_rate=args.target_win_rate,
            max_drawdown_pct=args.max_drawdown_pct,
            enable=args.enable,
        )

        status = "ENABLED" if args.enable else "DISABLED"
        log.info(
            f"Updated {STRATEGY_NAME}: {len(wallet_addrs)} wallets, "
            f"{len(pools)} pools, status={status}"
        )

    finally:
        conn.close()

    log.info("Done.")


if __name__ == "__main__":
    main()
