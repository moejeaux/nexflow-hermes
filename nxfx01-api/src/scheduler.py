"""NXFX01 Scheduler — Real-time launch detection + timed background workers.

Architecture:
  1. Alchemy WebSocket listener for PoolCreated events (real-time push)
  2. 10-second Blockscout polling fallback (catches anything WS misses)
  3. Immediate Stage 1 pipeline on every new launch
  4. Score-based routing:
     - >= buy_trigger_threshold (85): → BUY_TRIGGER alert to agent
     - >= evaluate_threshold (60):    → Immediate Stage 2 evaluation
     - < 60:                          → Standard WAIT/BLOCK pipeline
  5. Timed background loops:
     - behavior_updater: every 5 min
     - outcome_tracker:  every 60 min
     - regime_check:     every 6 hr
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone

import httpx
import websockets

from src import db
from src.workers import launch_scanner, contract_scanner, deployer_profiler
from src.workers import behavior_updater, outcome_tracker
from src.scoring import initial_scorer, final_scorer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("nxfx01.scheduler")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WETH_BASE = "0x4200000000000000000000000000000000000006"
BLOCKSCOUT_BASE = "https://base.blockscout.com"

# DEX factory registry: (address, topic0, label)
# Each entry watches a different pool factory for new pair/pool creation events.
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

# Build lookup maps for fast event routing
_FACTORY_BY_ADDR = {f["address"].lower(): f for f in DEX_FACTORIES}
_TOPIC0_SET = {f["topic0"] for f in DEX_FACTORIES}

POLL_INTERVAL = 10        # seconds between Blockscout fallback polls
BEHAVIOR_INTERVAL = 300   # 5 min
OUTCOME_INTERVAL = 3600   # 60 min
REGIME_INTERVAL = 21600   # 6 hr
WS_RECONNECT_DELAY = 5    # seconds before WS reconnect attempt

# ---------------------------------------------------------------------------
# Alert system
# ---------------------------------------------------------------------------

async def _create_alert(
    launch_id: str,
    alert_type: str,
    score: int,
    action_mode: str,
    message: str,
    context: dict | None = None,
) -> None:
    """Insert an alert row for the agent to consume."""
    await db.execute(
        """
        INSERT INTO launch_alerts (launch_id, alert_type, score, action_mode, message, context)
        VALUES ($1, $2::alert_type, $3, $4, $5, $6::jsonb)
        """,
        launch_id,
        alert_type,
        score,
        action_mode,
        message,
        json.dumps(context or {}),
    )
    logger.info("ALERT [%s] launch=%s score=%d mode=%s: %s",
                alert_type, launch_id[:8], score, action_mode, message)


async def _get_threshold(key: str, default: int) -> int:
    val = await db.get_config(key)
    return int(val) if val is not None else default


# ---------------------------------------------------------------------------
# Stage 1 pipeline — run on every new launch
# ---------------------------------------------------------------------------

async def _run_stage1_for_launch(launch_id: str, token_address: str) -> dict | None:
    """Run contract scan → deployer profile → initial score for one launch."""
    try:
        cs = await contract_scanner.scan_contract(launch_id, token_address)
        logger.info("Stage1 contract: %s safety=%s", launch_id[:8], cs.get("contract_safety"))
    except Exception:
        logger.exception("Contract scan failed for %s", launch_id[:8])
        return None

    # Deployer profiler — always run (handles unknown deployer with neutral defaults)
    row = await db.fetchrow(
        "SELECT deployer_address FROM launches WHERE launch_id = $1", launch_id
    )
    deployer = row["deployer_address"] if row else None
    try:
        dp = await deployer_profiler.profile_deployer(launch_id, deployer)
        logger.info("Stage1 deployer: %s rep=%s fund=%s",
                    launch_id[:8], dp.get("deployer_reputation"), dp.get("funding_risk"))
    except Exception:
        logger.exception("Deployer profiler failed for %s", launch_id[:8])

    # Initial scorer
    try:
        result = await initial_scorer.score_launch(launch_id)
        logger.info("Stage1 score: %s overall=%s mode=%s",
                    launch_id[:8], result.get("overall_safety_initial"), result.get("action_initial"))
        return result
    except Exception:
        logger.exception("Initial scorer failed for %s", launch_id[:8])
        return None


# ---------------------------------------------------------------------------
# Score routing — decides what happens after Stage 1
# ---------------------------------------------------------------------------

async def _route_scored_launch(launch_id: str, score_result: dict) -> None:
    """Route a scored launch based on thresholds."""
    score = score_result.get("overall_safety_initial", 0)
    action = score_result.get("action_initial", "BLOCK")

    buy_trigger = await _get_threshold("buy_trigger_threshold", 85)
    evaluate_min = await _get_threshold("evaluate_threshold", 60)

    shadow = await db.is_shadow_mode()

    if score >= buy_trigger:
        # High confidence — send directly to agent for buy trigger
        msg = (f"HIGH SCORE LAUNCH: {score}/100 ({action}). "
               f"Contract clean, deployer solid. "
               f"{'[SHADOW MODE — no execution]' if shadow else 'Ready for buy evaluation.'}")

        row = await db.fetchrow(
            "SELECT token_address, pair_address, deployer_address FROM launches WHERE launch_id = $1",
            launch_id,
        )
        await _create_alert(
            launch_id=launch_id,
            alert_type="BUY_TRIGGER",
            score=score,
            action_mode=action,
            message=msg,
            context={
                "token_address": row["token_address"] if row else None,
                "pair_address": row["pair_address"] if row else None,
                "stage": "initial",
                "shadow": shadow,
            },
        )

    elif score >= evaluate_min:
        # Mid-range — immediately run Stage 2 behavior analysis
        logger.info("Score %d >= %d evaluate threshold — triggering immediate Stage 2 for %s",
                    score, evaluate_min, launch_id[:8])
        await _run_immediate_stage2(launch_id, buy_trigger, shadow)

    else:
        # Low score — standard WAIT/BLOCK path, no immediate action
        logger.info("Score %d below evaluate threshold (%d) — standard pipeline for %s",
                    score, evaluate_min, launch_id[:8])


async def _run_immediate_stage2(launch_id: str, buy_trigger: int, shadow: bool) -> None:
    """Run behavior analysis + final scoring immediately, then alert if upgraded."""
    row = await db.fetchrow(
        "SELECT token_address, pair_address FROM launches WHERE launch_id = $1",
        launch_id,
    )
    if not row:
        return

    # Run behavior updater for this specific launch
    try:
        beh = await behavior_updater.update_behavior(
            launch_id, row["token_address"], row["pair_address"]
        )
        logger.info("Stage2 behavior: %s holder=%s liq=%s smart=%s",
                    launch_id[:8],
                    beh.get("holder_distribution"),
                    beh.get("liquidity_stability"),
                    beh.get("smart_money_participation"))
    except Exception:
        logger.exception("Behavior updater failed for %s — will retry in background", launch_id[:8])
        # Create evaluate alert so agent knows this needs attention
        await _create_alert(
            launch_id=launch_id,
            alert_type="EVALUATE",
            score=0,
            action_mode="WAIT",
            message=f"Behavior analysis failed for promising launch (initial score >= evaluate threshold). Needs manual review.",
            context={"stage": "behavior_failed", "shadow": shadow},
        )
        return

    # Run final scorer
    try:
        final = await final_scorer.score_launch(launch_id)
        final_score = final.get("overall_safety_final", 0)
        final_action = final.get("action_final", "WAIT")
        initial_action = final.get("action_initial", "WAIT")

        logger.info("Stage2 final: %s score=%d mode=%s (was %s)",
                    launch_id[:8], final_score, final_action, initial_action)

        buy_threshold_final = await _get_threshold("buy_threshold_final", 85)

        if final_score >= buy_threshold_final and final_action == "FAST":
            # Score increased or maintained above buy threshold — send buy trigger
            await _create_alert(
                launch_id=launch_id,
                alert_type="BUY_TRIGGER",
                score=final_score,
                action_mode=final_action,
                message=(f"UPGRADED LAUNCH: final score {final_score}/100 ({final_action}). "
                         f"Behavior confirmed positive. "
                         f"{'[SHADOW MODE]' if shadow else 'Ready for buy evaluation.'}"),
                context={
                    "token_address": row["token_address"],
                    "pair_address": row["pair_address"],
                    "stage": "final",
                    "transition": f"{initial_action}→{final_action}",
                    "shadow": shadow,
                },
            )
        elif final_action == "FAST":
            # FAST but below buy threshold — notify for monitoring
            await _create_alert(
                launch_id=launch_id,
                alert_type="EVALUATE",
                score=final_score,
                action_mode=final_action,
                message=(f"FAST launch below buy threshold: {final_score}/100. "
                         f"Worth monitoring. {'[SHADOW]' if shadow else ''}"),
                context={
                    "token_address": row["token_address"],
                    "stage": "final",
                    "shadow": shadow,
                },
            )
        elif final_action != initial_action:
            # Mode changed (likely downgrade) — log it
            await _create_alert(
                launch_id=launch_id,
                alert_type="DOWNGRADE" if final_action == "BLOCK" else "EVALUATE",
                score=final_score,
                action_mode=final_action,
                message=f"Mode changed {initial_action}→{final_action}, score={final_score}",
                context={"stage": "final", "shadow": shadow},
            )

    except Exception:
        logger.exception("Final scorer failed for %s", launch_id[:8])


# ---------------------------------------------------------------------------
# WebSocket listener — real-time PoolCreated events from Alchemy
# ---------------------------------------------------------------------------

def _parse_pool_address(data: str, dex_label: str) -> str | None:
    """Extract pool/pair address from event data field.

    V3:  data = fee(32) + tickSpacing(32) + pool(32)  → pool at bytes 64..84 (chars 130..170)
    V2:  data = pair(32) + allPairsLength(32)          → pair at bytes 0..20  (chars 26..66)
    Aero: data = pool(32) + count(32)                  → pool at bytes 0..20  (chars 26..66)
    """
    if len(data) < 66:
        return None
    if dex_label == "uniswap_v3":
        if len(data) >= 170:
            return "0x" + data[130:170]
        return None
    # V2 and Aerodrome both have pool/pair as first word
    return "0x" + data[26:66]


async def _process_pool_event(log: dict) -> None:
    """Process a pool/pair creation event from any supported DEX factory."""
    topics = log.get("topics", [])
    if len(topics) < 3:
        return

    # Identify which DEX emitted this event
    emitter = (log.get("address") or "").lower()
    factory = _FACTORY_BY_ADDR.get(emitter)
    if not factory:
        # Try matching by topic0 (WS events may not include address)
        topic0 = topics[0] if topics else None
        for f in DEX_FACTORIES:
            if f["topic0"] == topic0:
                factory = f
                break
    if not factory:
        return

    dex_label = factory["label"]

    token0 = "0x" + topics[1][-40:]
    token1 = "0x" + topics[2][-40:]

    # Determine new token (not WETH)
    new_token = token0 if token1.lower() == WETH_BASE.lower() else token1

    # Skip if already tracked
    exists = await db.fetchval(
        "SELECT launch_id FROM launches WHERE token_address = $1", new_token.lower()
    )
    if exists:
        return

    # Look up deployer
    deployer = await _lookup_deployer(new_token.lower())

    # Extract pool/pair address from data field
    data = log.get("data", "0x")
    pool_address = _parse_pool_address(data, dex_label)

    # Insert bare launch row
    launch_id = await db.fetchval(
        """
        INSERT INTO launches (token_address, pair_address, deployer_address, chain, dex_source, timestamp, detected_at)
        VALUES ($1, $2, $3, 'base', $4, now(), now())
        ON CONFLICT (token_address) DO NOTHING
        RETURNING launch_id
        """,
        new_token.lower(),
        pool_address.lower() if pool_address else None,
        deployer,
        dex_label,
    )

    if not launch_id:
        return  # Already existed (race condition)

    launch_id_str = str(launch_id)
    logger.info("NEW LAUNCH detected: %s token=%s dex=%s", launch_id_str[:8], new_token[:10], dex_label)

    # Run full Stage 1 pipeline immediately
    result = await _run_stage1_for_launch(launch_id_str, new_token.lower())
    if result:
        await _route_scored_launch(launch_id_str, result)


async def _lookup_deployer(token_address: str) -> str | None:
    """Look up deployer via Blockscout."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BLOCKSCOUT_BASE}/api/v2/addresses/{token_address}",
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            creator = data.get("creator", {})
            if isinstance(creator, dict):
                return creator.get("hash", "").lower() or None
    except Exception:
        logger.debug("Deployer lookup failed for %s", token_address[:10])
    return None


async def _ws_listener() -> None:
    """Subscribe to PoolCreated events via Alchemy WebSocket. Auto-reconnects."""
    api_key = os.getenv("ALCHEMY_API_KEY")
    if not api_key:
        logger.warning("ALCHEMY_API_KEY not set — WebSocket listener disabled, using poll-only mode")
        return

    ws_url = f"wss://base-mainnet.g.alchemy.com/v2/{api_key}"

    factory_addrs = [f["address"] for f in DEX_FACTORIES]
    factory_topics = list(_TOPIC0_SET)

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=30, ping_timeout=10) as ws:
                # Subscribe to pool/pair creation events from all DEX factories
                sub_request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_subscribe",
                    "params": [
                        "logs",
                        {
                            "address": factory_addrs,
                            "topics": [factory_topics],
                        },
                    ],
                }
                await ws.send(json.dumps(sub_request))
                sub_response = json.loads(await ws.recv())
                sub_id = sub_response.get("result")
                logger.info(
                    "WebSocket connected — subscription ID: %s, watching %d factories",
                    sub_id, len(DEX_FACTORIES),
                )

                async for raw_msg in ws:
                    msg = json.loads(raw_msg)
                    if msg.get("method") == "eth_subscription":
                        log_data = msg.get("params", {}).get("result", {})
                        try:
                            await _process_pool_event(log_data)
                        except Exception:
                            logger.exception("Error processing WS event")

        except websockets.ConnectionClosed:
            logger.warning("WebSocket closed — reconnecting in %ds", WS_RECONNECT_DELAY)
        except Exception:
            logger.exception("WebSocket error — reconnecting in %ds", WS_RECONNECT_DELAY)

        await asyncio.sleep(WS_RECONNECT_DELAY)


# ---------------------------------------------------------------------------
# Blockscout polling fallback — catches anything WS misses
# ---------------------------------------------------------------------------

async def _poll_loop() -> None:
    """Poll Blockscout every POLL_INTERVAL seconds as a safety net."""
    logger.info("Poll loop started — interval=%ds", POLL_INTERVAL)

    while True:
        try:
            # Use the launch_scanner's existing logic (handles cursor, batching, rate limits)
            result = await launch_scanner.run()
            status = result.get("status", "unknown")
            inserted = result.get("inserted", 0)

            if inserted > 0:
                logger.info("Poll found %d new launches (blocks %s-%s)",
                            inserted, result.get("from_block"), result.get("to_block"))

                # Run Stage 1 for any newly inserted launches that don't have scores yet
                pending = await db.fetch(
                    """
                    SELECT launch_id, token_address FROM launches
                    WHERE overall_safety_initial IS NULL
                      AND contract_safety IS NULL
                    ORDER BY detected_at DESC
                    LIMIT 20
                    """
                )
                for row in pending:
                    lid = str(row["launch_id"])
                    result = await _run_stage1_for_launch(lid, row["token_address"])
                    if result:
                        await _route_scored_launch(lid, result)
            elif status != "up_to_date":
                logger.debug("Poll: %s", status)

        except Exception:
            logger.exception("Poll loop error")

        await asyncio.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Background timed workers
# ---------------------------------------------------------------------------

async def _behavior_loop() -> None:
    """Run behavior updater + final scorer every BEHAVIOR_INTERVAL seconds."""
    logger.info("Behavior loop started — interval=%ds", BEHAVIOR_INTERVAL)
    while True:
        await asyncio.sleep(BEHAVIOR_INTERVAL)
        try:
            beh_result = await behavior_updater.run()
            processed = beh_result.get("processed", 0)
            if processed > 0:
                logger.info("Behavior updater processed %d launches", processed)

            # Run final scorer for anything with behavior data
            final_result = await final_scorer.run()
            scored = final_result.get("processed", 0)
            if scored > 0:
                logger.info("Final scorer processed %d launches", scored)

                # Check for any newly-FAST launches that hit buy threshold
                buy_threshold = await _get_threshold("buy_threshold_final", 85)
                shadow = await db.is_shadow_mode()

                new_fast = await db.fetch(
                    """
                    SELECT launch_id, token_address, pair_address,
                           overall_safety_final, action_final
                    FROM launches
                    WHERE action_final = 'FAST'
                      AND overall_safety_final >= $1
                      AND behavior_scored_at > now() - interval '10 minutes'
                      AND launch_id NOT IN (
                          SELECT launch_id FROM launch_alerts
                          WHERE alert_type = 'BUY_TRIGGER'
                      )
                    """,
                    buy_threshold,
                )
                for row in new_fast:
                    await _create_alert(
                        launch_id=str(row["launch_id"]),
                        alert_type="BUY_TRIGGER",
                        score=row["overall_safety_final"],
                        action_mode=row["action_final"],
                        message=(f"Background scoring found BUY candidate: "
                                 f"{row['overall_safety_final']}/100 FAST. "
                                 f"{'[SHADOW]' if shadow else 'Ready for buy.'}"),
                        context={
                            "token_address": row["token_address"],
                            "pair_address": row["pair_address"],
                            "stage": "background_final",
                            "shadow": shadow,
                        },
                    )
        except Exception:
            logger.exception("Behavior loop error")


async def _outcome_loop() -> None:
    """Run outcome tracker every OUTCOME_INTERVAL seconds."""
    logger.info("Outcome loop started — interval=%ds", OUTCOME_INTERVAL)
    while True:
        await asyncio.sleep(OUTCOME_INTERVAL)
        try:
            result = await outcome_tracker.run()
            processed = result.get("processed", 0)
            if processed > 0:
                logger.info("Outcome tracker processed %d launches", processed)

                # Check for rug warnings on previously FAST launches
                rugged = await db.fetch(
                    """
                    SELECT l.launch_id, l.token_address, l.action_final, o.final_status
                    FROM launch_outcomes o
                    JOIN launches l ON l.launch_id = o.launch_id
                    WHERE o.final_status = 'RUGGED'
                      AND o.last_checked_at > now() - interval '2 hours'
                      AND l.action_final = 'FAST'
                      AND l.launch_id NOT IN (
                          SELECT launch_id FROM launch_alerts WHERE alert_type = 'RUG_WARNING'
                      )
                    """
                )
                for row in rugged:
                    await _create_alert(
                        launch_id=str(row["launch_id"]),
                        alert_type="RUG_WARNING",
                        score=0,
                        action_mode="BLOCK",
                        message=f"RUG DETECTED on FAST launch {row['token_address'][:10]}... — LP drained",
                        context={"token_address": row["token_address"]},
                    )
        except Exception:
            logger.exception("Outcome loop error")


async def _regime_loop() -> None:
    """Run regime detection every REGIME_INTERVAL seconds."""
    logger.info("Regime loop started — interval=%ds", REGIME_INTERVAL)
    while True:
        await asyncio.sleep(REGIME_INTERVAL)
        try:
            # Regime detection: count launches and rug rate in last 24h
            stats = await db.fetchrow(
                """
                SELECT
                    COUNT(*) AS total_24h,
                    COUNT(*) FILTER (WHERE action_initial = 'BLOCK' OR action_final = 'BLOCK') AS blocked_24h
                FROM launches
                WHERE detected_at > now() - interval '24 hours'
                """
            )
            total = stats["total_24h"] or 0
            blocked = stats["blocked_24h"] or 0
            rug_rate = blocked / total if total > 0 else 0

            if total > 50 and rug_rate < 0.3:
                regime = "HOT"
            elif total < 10 or rug_rate > 0.5:
                regime = "COLD"
            else:
                regime = "NORMAL"

            current = await db.get_config("base_market_regime") or "NORMAL"
            if regime != current:
                await db.set_config("base_market_regime", regime)
                logger.info("Regime changed: %s → %s (total=%d, rug_rate=%.1f%%)",
                            current, regime, total, rug_rate * 100)
                await db.execute(
                    """
                    INSERT INTO base_market_regime_log (regime, launch_count_24h, rug_rate_24h)
                    VALUES ($1, $2, $3)
                    """,
                    regime, total, round(rug_rate, 4),
                )
            else:
                logger.debug("Regime unchanged: %s (total=%d, rug_rate=%.1f%%)",
                             regime, total, rug_rate * 100)
        except Exception:
            logger.exception("Regime loop error")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    """Start all scheduler loops."""
    logger.info("=" * 60)
    logger.info("NXFX01 Scheduler starting")
    logger.info("  Poll interval:     %ds", POLL_INTERVAL)
    logger.info("  Behavior interval: %ds", BEHAVIOR_INTERVAL)
    logger.info("  Outcome interval:  %ds", OUTCOME_INTERVAL)
    logger.info("  Regime interval:   %ds", REGIME_INTERVAL)
    logger.info("=" * 60)

    # Initialize DB pool
    await db.get_pool()

    shadow = await db.is_shadow_mode()
    logger.info("Mode: %s", "SHADOW (observe only)" if shadow else "LIVE")

    buy_trigger = await _get_threshold("buy_trigger_threshold", 85)
    evaluate_min = await _get_threshold("evaluate_threshold", 60)
    logger.info("Thresholds: buy_trigger=%d, evaluate=%d", buy_trigger, evaluate_min)

    # Launch all concurrent loops
    tasks = [
        asyncio.create_task(_ws_listener(), name="ws_listener"),
        asyncio.create_task(_poll_loop(), name="poll_loop"),
        asyncio.create_task(_behavior_loop(), name="behavior_loop"),
        asyncio.create_task(_outcome_loop(), name="outcome_loop"),
        asyncio.create_task(_regime_loop(), name="regime_loop"),
    ]

    # Handle graceful shutdown
    stop = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        stop.set()
        for t in tasks:
            t.cancel()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass  # Windows

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        pass
    finally:
        await db.close_pool()
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    asyncio.run(main())
