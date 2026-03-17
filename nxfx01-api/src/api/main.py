"""NXFX01 FastAPI Application — Launch Intelligence API for Hermes.

Endpoints:
  GET  /launches/recent         — recent launches
  GET  /launches/actionable     — tradable FAST launches
  GET  /launches/summary        — aggregate pipeline stats
  GET  /launches/{launch_id}    — full launch detail
  GET  /launches/outcomes       — past launch outcomes for learning
  GET  /wallets/{address}       — wallet profile
  POST /policy/suggest          — propose policy adjustment
  GET  /ops/latency             — pipeline latency stats
  POST /ops/run-cycle           — trigger an off-cycle pipeline run
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Security
from fastapi.security import APIKeyHeader

from src import db
from src.api.models import (
    ActionMode,
    DataCompleteness,
    DeRiskEventView,
    DeRiskTrigger,
    GraphRiskDetail,
    LaunchNotes,
    LaunchOutcomeView,
    LaunchScores,
    LaunchModes,
    LaunchStatus,
    LaunchSummary,
    LaunchView,
    LatencyStats,
    LiquidityDetail,
    NotableParticipant,
    PolicySuggestionIn,
    PolicySuggestionOut,
    PositionAction,
    SmartMoneyDetail,
    SocialDetail,
    SubScores,
    WalletSummary,
    WalletTierCounts,
    WalletView,
    WhaleBehaviorDetail,
)


# ---------------------------------------------------------------------------
# Lifespan – initialize and close DB pool
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.get_pool()
    yield
    await db.close_pool()


app = FastAPI(
    title="NXFX01 Launch Intelligence",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Auth – simple API key guard
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)


async def _verify_key(api_key: str | None = Security(_api_key_header)) -> str:
    expected = os.getenv("NXFX01_API_KEY")
    if not expected:
        # No key configured → allow (dev mode)
        return "dev"
    if api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_launch_view(row) -> LaunchView:
    """Convert a DB row to the full LaunchView model (v2)."""
    notes_raw = row["notes"] or {}
    if isinstance(notes_raw, str):
        notes_raw = json.loads(notes_raw)

    notable_raw = row.get("notable_participants") or []
    if isinstance(notable_raw, str):
        notable_raw = json.loads(notable_raw)

    wallet_summary_raw = row.get("wallet_summary") or {}
    if isinstance(wallet_summary_raw, str):
        wallet_summary_raw = json.loads(wallet_summary_raw)

    tiers_raw = wallet_summary_raw.get("tiers", {})

    # v2: parse detail JSONB fields
    sm_detail_raw = row.get("sm_detail") or {}
    if isinstance(sm_detail_raw, str):
        sm_detail_raw = json.loads(sm_detail_raw)

    whale_detail_raw = row.get("whale_detail") or {}
    if isinstance(whale_detail_raw, str):
        whale_detail_raw = json.loads(whale_detail_raw)

    graph_detail_raw = row.get("graph_detail") or {}
    if isinstance(graph_detail_raw, str):
        graph_detail_raw = json.loads(graph_detail_raw)

    social_detail_raw = row.get("social_detail") or {}
    if isinstance(social_detail_raw, str):
        social_detail_raw = json.loads(social_detail_raw)

    data_completeness_raw = row.get("data_completeness") or {}
    if isinstance(data_completeness_raw, str):
        data_completeness_raw = json.loads(data_completeness_raw)

    derisk_raw = row.get("derisk_triggers") or []
    if isinstance(derisk_raw, str):
        derisk_raw = json.loads(derisk_raw)

    return LaunchView(
        launch_id=row["launch_id"],
        token_address=row["token_address"],
        pair_address=row.get("pair_address"),
        deployer_address=row.get("deployer_address"),
        chain="base",
        dex_source=row.get("dex_source", "unknown"),
        timestamp=row["detected_at"],
        launch_type=row.get("launch_type", "unknown"),
        launch_type_confidence=row.get("launch_type_confidence", 0),
        launchpad_trust_level=row.get("launchpad_trust_level", "NONE"),
        status=row["status"],
        policy_version=row.get("policy_version"),
        scoring_version=row.get("scoring_version", "v1"),
        scores=LaunchScores(
            contract_safety=row.get("contract_safety"),
            deployer_reputation=row.get("deployer_reputation"),
            funding_risk=row.get("funding_risk"),
            holder_distribution=row.get("holder_distribution"),
            liquidity_stability=row.get("liquidity_stability"),
            smart_money_participation=row.get("smart_money_participation"),
            whale_participation=row.get("whale_participation"),
            overall_safety_initial=row.get("overall_safety_initial"),
            overall_safety_final=row.get("overall_safety_final"),
        ),
        sub_scores=SubScores(
            smart_money_alignment=row.get("smart_money_alignment"),
            whale_behavior=row.get("whale_behavior_score"),
            graph_risk=row.get("graph_risk_score"),
            rug_risk=row.get("rug_risk_score"),
            liquidity_quality=row.get("liquidity_quality_score"),
            social_quality=row.get("social_quality_score"),
            data_confidence=row.get("data_confidence_score"),
        ),
        smart_money_detail=SmartMoneyDetail(**sm_detail_raw) if sm_detail_raw else SmartMoneyDetail(),
        whale_behavior_detail=WhaleBehaviorDetail(**whale_detail_raw) if whale_detail_raw else WhaleBehaviorDetail(),
        graph_risk_detail=GraphRiskDetail(**graph_detail_raw) if graph_detail_raw else GraphRiskDetail(),
        liquidity_detail=LiquidityDetail(
            lp_usd=row.get("lp_usd"),
            rolling_volume_1h_usd=row.get("rolling_volume_1h_usd"),
            effective_spread_bp=row.get("effective_spread_bp"),
        ),
        social_detail=SocialDetail(**social_detail_raw) if social_detail_raw else SocialDetail(),
        modes=LaunchModes(
            action_initial=row.get("action_initial"),
            action_final=row.get("action_final"),
        ),
        wallet_summary=WalletSummary(
            top_holders_share=wallet_summary_raw.get("top_holders_share"),
            tiers=WalletTierCounts(
                tier1_whales=tiers_raw.get("tier1_whales", 0),
                tier2_smart_money=tiers_raw.get("tier2_smart_money", 0),
                tier3_retail=tiers_raw.get("tier3_retail", 0),
                tier4_flagged=tiers_raw.get("tier4_flagged", 0),
            ),
        ),
        notes=LaunchNotes(**{k: v for k, v in notes_raw.items() if k in LaunchNotes.model_fields}),
        notable_participants=[NotableParticipant(**p) for p in notable_raw],
        position_action=row.get("position_action", "NO_ENTRY"),
        derisk_triggers=[DeRiskTrigger(**t) for t in derisk_raw],
        data_completeness=DataCompleteness(**data_completeness_raw) if data_completeness_raw else DataCompleteness(),
        bytecode_hash=row.get("bytecode_hash"),
        deployer_launch_velocity_24h=row.get("deployer_launch_velocity_24h", 0),
        shadow=row.get("shadow", False),
        detected_at=row.get("detected_at"),
        initial_scored_at=row.get("initial_scored_at"),
        behavior_scored_at=row.get("behavior_scored_at"),
        first_surfaced_at=row.get("first_surfaced_at"),
    )


def _row_to_summary(row) -> LaunchSummary:
    action = row.get("action_final") or row.get("action_initial")
    safety = row.get("overall_safety_final") or row.get("overall_safety_initial")
    return LaunchSummary(
        launch_id=row["launch_id"],
        token_address=row["token_address"],
        chain="base",
        timestamp=row["detected_at"],
        status=row["status"],
        action=action,
        overall_safety=safety,
        smart_money_participation=row.get("smart_money_participation"),
        deployer_launch_velocity_24h=row.get("deployer_launch_velocity_24h", 0),
        shadow=row.get("shadow", False),
        position_action=row.get("position_action", "NO_ENTRY"),
        rug_risk=row.get("rug_risk_score"),
        data_confidence=row.get("data_confidence_score"),
        scoring_version=row.get("scoring_version", "v1"),
    )


_LAUNCH_COLUMNS = """
    launch_id, token_address, pair_address, deployer_address,
    launch_type, launch_type_confidence, launchpad_trust_level, dex_source,
    status, policy_version, scoring_version,
    contract_safety, deployer_reputation, funding_risk,
    holder_distribution, liquidity_stability,
    smart_money_participation, whale_participation,
    overall_safety_initial, overall_safety_final,
    action_initial, action_final,
    smart_money_alignment, whale_behavior_score, graph_risk_score,
    rug_risk_score, liquidity_quality_score, social_quality_score,
    data_confidence_score,
    sm_detail, whale_detail, graph_detail, social_detail,
    lp_usd, rolling_volume_1h_usd, effective_spread_bp,
    position_action, derisk_triggers, data_completeness,
    wallet_summary, notes, notable_participants,
    bytecode_hash, deployer_launch_velocity_24h, shadow,
    detected_at, initial_scored_at, behavior_scored_at, first_surfaced_at
"""

_SUMMARY_COLUMNS = """
    launch_id, token_address, status,
    action_initial, action_final,
    overall_safety_initial, overall_safety_final,
    smart_money_participation, deployer_launch_velocity_24h,
    shadow, detected_at,
    position_action, rug_risk_score, data_confidence_score, scoring_version
"""


# ---------------------------------------------------------------------------
# Routes — Launches
# ---------------------------------------------------------------------------

@app.get("/launches/recent", response_model=list[LaunchSummary])
async def get_recent_launches(
    limit: int = Query(default=20, ge=1, le=100),
    min_safety: int | None = Query(default=None, ge=0, le=100),
    _key: str = Security(_verify_key),
):
    """Recent launches ordered by detection time (newest first)."""
    if min_safety is not None:
        rows = await db.fetch(
            f"SELECT {_SUMMARY_COLUMNS} FROM launches "
            "WHERE COALESCE(overall_safety_final, overall_safety_initial, 0) >= $1 "
            "ORDER BY detected_at DESC LIMIT $2",
            min_safety, limit,
        )
    else:
        rows = await db.fetch(
            f"SELECT {_SUMMARY_COLUMNS} FROM launches ORDER BY detected_at DESC LIMIT $1",
            limit,
        )
    return [_row_to_summary(r) for r in rows]


@app.get("/launches/actionable", response_model=list[LaunchSummary])
async def get_actionable_launches(
    mode: ActionMode = Query(default=ActionMode.FAST),
    min_safety: int = Query(default=60, ge=0, le=100),
    limit: int = Query(default=10, ge=1, le=50),
    _key: str = Security(_verify_key),
):
    """Actionable launches filtered by mode and safety, highest safety first.

    In shadow mode, returns matches but they are flagged shadow=True.
    """
    rows = await db.fetch(
        f"SELECT {_SUMMARY_COLUMNS} FROM launches "
        "WHERE COALESCE(action_final, action_initial) = $1 "
        "  AND COALESCE(overall_safety_final, overall_safety_initial, 0) >= $2 "
        "ORDER BY COALESCE(overall_safety_final, overall_safety_initial, 0) DESC, "
        "         smart_money_participation DESC NULLS LAST, "
        "         detected_at DESC "
        "LIMIT $3",
        mode.value, min_safety, limit,
    )
    return [_row_to_summary(r) for r in rows]


@app.get("/launches/{launch_id}", response_model=LaunchView)
async def get_launch_details(
    launch_id: UUID,
    _key: str = Security(_verify_key),
):
    """Full launch view with all scores, notes, and participants."""
    row = await db.fetchrow(
        f"SELECT {_LAUNCH_COLUMNS} FROM launches WHERE launch_id = $1",
        launch_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Launch not found")
    return _row_to_launch_view(row)


@app.get("/launches/summary")
async def get_pipeline_summary(
    hours: int = Query(default=24, ge=1, le=168),
    _key: str = Security(_verify_key),
):
    """Aggregate pipeline stats over the given period."""
    stats = await db.fetchrow(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE action_final = 'FAST' OR (action_final IS NULL AND action_initial = 'FAST')) AS fast_count,
            COUNT(*) FILTER (WHERE action_final = 'WAIT' OR (action_final IS NULL AND action_initial = 'WAIT')) AS wait_count,
            COUNT(*) FILTER (WHERE action_final = 'BLOCK' OR (action_final IS NULL AND action_initial = 'BLOCK')) AS block_count,
            COUNT(*) FILTER (WHERE status = 'pending_initial') AS pending,
            COUNT(*) FILTER (WHERE status = 'initial_scored') AS initial_scored,
            COUNT(*) FILTER (WHERE status = 'behavior_scored') AS behavior_scored,
            COUNT(*) FILTER (WHERE status = 'outcome_scored') AS outcome_scored,
            AVG(EXTRACT(EPOCH FROM (initial_scored_at - detected_at))) AS avg_initial_latency_s,
            AVG(EXTRACT(EPOCH FROM (behavior_scored_at - detected_at))) AS avg_behavior_latency_s
        FROM launches
        WHERE detected_at > now() - make_interval(hours => $1)
        """,
        hours,
    )

    shadow = await db.is_shadow_mode()
    regime = await db.get_config("base_market_regime") or "NORMAL"

    return {
        "period_hours": hours,
        "shadow_mode": shadow,
        "market_regime": regime,
        "total_launches": stats["total"],
        "by_mode": {
            "FAST": stats["fast_count"],
            "WAIT": stats["wait_count"],
            "BLOCK": stats["block_count"],
        },
        "by_status": {
            "pending_initial": stats["pending"],
            "initial_scored": stats["initial_scored"],
            "behavior_scored": stats["behavior_scored"],
            "outcome_scored": stats["outcome_scored"],
        },
        "avg_latency": {
            "initial_score_s": round(stats["avg_initial_latency_s"] or 0, 2),
            "behavior_score_s": round(stats["avg_behavior_latency_s"] or 0, 2),
        },
    }


# ---------------------------------------------------------------------------
# Routes — Outcomes
# ---------------------------------------------------------------------------

@app.get("/launches/outcomes", response_model=list[LaunchOutcomeView])
async def get_past_launch_outcomes(
    since_days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
    _key: str = Security(_verify_key),
):
    """Past launch outcomes for self-learning analysis."""
    rows = await db.fetch(
        """
        SELECT l.launch_id, l.token_address, l.detected_at,
               l.overall_safety_initial, l.overall_safety_final,
               l.action_initial, l.action_final, l.policy_version,
               l.scoring_version, l.position_action,
               l.smart_money_alignment, l.whale_behavior_score,
               l.graph_risk_score, l.rug_risk_score,
               l.liquidity_quality_score, l.social_quality_score,
               l.data_confidence_score,
               lo.pnl_1h, lo.pnl_24h, lo.pnl_7d,
               lo.max_drawdown, lo.rugged, lo.final_status, lo.peak_mcap_usd
        FROM launches l
        JOIN launch_outcomes lo ON lo.launch_id = l.launch_id
        WHERE l.detected_at > now() - make_interval(days => $1)
        ORDER BY l.detected_at DESC
        LIMIT $2
        """,
        since_days, limit,
    )

    return [
        LaunchOutcomeView(
            launch_id=r["launch_id"],
            token_address=r["token_address"],
            timestamp=r["detected_at"],
            overall_safety_initial=r["overall_safety_initial"],
            overall_safety_final=r["overall_safety_final"],
            action_initial=r["action_initial"],
            action_final=r["action_final"],
            policy_version=r["policy_version"],
            scoring_version=r.get("scoring_version", "v1"),
            sub_scores=SubScores(
                smart_money_alignment=r.get("smart_money_alignment"),
                whale_behavior=r.get("whale_behavior_score"),
                graph_risk=r.get("graph_risk_score"),
                rug_risk=r.get("rug_risk_score"),
                liquidity_quality=r.get("liquidity_quality_score"),
                social_quality=r.get("social_quality_score"),
                data_confidence=r.get("data_confidence_score"),
            ),
            position_action=r.get("position_action", "NO_ENTRY"),
            pnl_1h=r["pnl_1h"],
            pnl_24h=r["pnl_24h"],
            pnl_7d=r["pnl_7d"],
            max_drawdown=r["max_drawdown"],
            rugged=r["rugged"],
            final_status=r["final_status"],
            peak_mcap_usd=r["peak_mcap_usd"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Routes — Wallets
# ---------------------------------------------------------------------------

@app.get("/wallets/{address}", response_model=WalletView)
async def get_wallet_profile(
    address: str,
    _key: str = Security(_verify_key),
):
    """Wallet/cluster profile."""
    row = await db.fetchrow(
        """
        SELECT w.address, w.wallet_tier, w.value_score, w.performance_score,
               w.cluster_id, w.alpha_cohort_flag, w.first_seen_at, w.last_seen_at,
               w.is_cex_funded, w.cex_funding_share, w.funding_cex_list,
               c.cluster_tier
        FROM wallets w
        LEFT JOIN clusters c ON c.cluster_id = w.cluster_id
        WHERE w.address = $1
        """,
        address.lower(),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Wallet not found")

    cex_list_raw = row.get("funding_cex_list") or []
    if isinstance(cex_list_raw, str):
        cex_list_raw = json.loads(cex_list_raw)

    return WalletView(
        wallet=row["address"],
        wallet_tier=row["wallet_tier"] or "UNKNOWN",
        wallet_value_score=row["value_score"] or 0,
        wallet_performance_score=row["performance_score"] or 0,
        cluster_id=str(row["cluster_id"]) if row["cluster_id"] else None,
        cluster_tier=row["cluster_tier"],
        alpha_cohort_flag=row["alpha_cohort_flag"] or False,
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        is_cex_funded=row.get("is_cex_funded"),
        cex_funding_share=row.get("cex_funding_share"),
        funding_cex_list=cex_list_raw,
    )


# ---------------------------------------------------------------------------
# Routes — Policy
# ---------------------------------------------------------------------------

@app.post("/policy/suggest", response_model=PolicySuggestionOut)
async def submit_policy_suggestion(
    body: PolicySuggestionIn,
    _key: str = Security(_verify_key),
):
    """Submit a policy adjustment suggestion (requires human approval)."""
    row = await db.fetchrow(
        """
        INSERT INTO policy_suggestions (patch, rationale, evidence_snapshot)
        VALUES ($1::jsonb, $2, $3::jsonb)
        RETURNING id, suggested_at, status
        """,
        json.dumps(body.patch),
        body.rationale,
        json.dumps(body.evidence_snapshot) if body.evidence_snapshot else None,
    )

    return PolicySuggestionOut(
        suggestion_id=row["id"],
        suggested_at=row["suggested_at"],
        status=row["status"],
        patch=body.patch,
        rationale=body.rationale,
        evidence_snapshot=body.evidence_snapshot,
    )


# ---------------------------------------------------------------------------
# Routes — Ops
# ---------------------------------------------------------------------------

@app.get("/ops/latency", response_model=list[LatencyStats])
async def get_latency_stats(
    hours: int = Query(default=24, ge=1, le=168),
    _key: str = Security(_verify_key),
):
    """Pipeline latency percentiles per stage."""
    results = []

    for stage, expr in [
        ("initial_score", "EXTRACT(EPOCH FROM (initial_scored_at - detected_at))"),
        ("behavior_score", "EXTRACT(EPOCH FROM (behavior_scored_at - detected_at))"),
        ("first_surfaced", "EXTRACT(EPOCH FROM (first_surfaced_at - detected_at))"),
    ]:
        row = await db.fetchrow(
            f"""
            SELECT
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {expr}) AS p50,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY {expr}) AS p95,
                COUNT(*) AS cnt
            FROM launches
            WHERE detected_at > now() - make_interval(hours => $1)
              AND {expr} IS NOT NULL
            """,
            hours,
        )
        results.append(LatencyStats(
            stage=stage,
            p50_seconds=round(row["p50"], 2) if row["p50"] else None,
            p95_seconds=round(row["p95"], 2) if row["p95"] else None,
            sample_count=row["cnt"],
        ))

    return results


# ---------------------------------------------------------------------------
# Ops — Manual pipeline trigger
# ---------------------------------------------------------------------------

@app.post("/ops/run-cycle")
async def run_cycle(
    _key: str = Security(_verify_key),
):
    """Trigger a full pipeline cycle: scan → contract → deployer → initial score."""
    import logging
    from src.workers import launch_scanner, contract_scanner, deployer_profiler
    from src.scoring import initial_scorer

    log = logging.getLogger("nxfx01.ops")
    results = {}

    for name, worker in [
        ("launch_scanner", launch_scanner),
        ("contract_scanner", contract_scanner),
        ("deployer_profiler", deployer_profiler),
        ("initial_scorer", initial_scorer),
    ]:
        try:
            results[name] = await worker.run()
        except Exception as exc:
            log.exception("Worker %s failed", name)
            results[name] = {"error": str(exc)}

    return {"status": "completed", "results": results}


# ---------------------------------------------------------------------------
# Alerts — agent-facing alert queue
# ---------------------------------------------------------------------------

@app.get("/alerts/pending")
async def get_pending_alerts(
    limit: int = Query(default=20, ge=1, le=100),
    _key: str = Security(_verify_key),
):
    """Return pending alerts for the agent, ordered by creation time."""
    rows = await db.fetch(
        """
        SELECT a.alert_id, a.launch_id, a.alert_type::text, a.alert_status::text,
               a.score, a.action_mode, a.message, a.context, a.created_at,
               l.token_address, l.pair_address
        FROM launch_alerts a
        JOIN launches l ON l.launch_id = a.launch_id
        WHERE a.alert_status = 'pending'
        ORDER BY a.created_at ASC
        LIMIT $1
        """,
        limit,
    )
    return [
        {
            "alert_id": str(row["alert_id"]),
            "launch_id": str(row["launch_id"]),
            "alert_type": row["alert_type"],
            "score": row["score"],
            "action_mode": row["action_mode"],
            "message": row["message"],
            "context": row["context"] if isinstance(row["context"], dict) else json.loads(row["context"] or "{}"),
            "token_address": row["token_address"],
            "pair_address": row["pair_address"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]


@app.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    _key: str = Security(_verify_key),
):
    """Mark an alert as acknowledged by the agent."""
    result = await db.execute(
        """
        UPDATE launch_alerts
        SET alert_status = 'acknowledged', acknowledged_at = now()
        WHERE alert_id = $1 AND alert_status IN ('pending', 'sent')
        """,
        alert_id,
    )
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail="Alert not found or already acknowledged")
    return {"status": "acknowledged", "alert_id": alert_id}


# ---------------------------------------------------------------------------
# Routes — De-Risk Events
# ---------------------------------------------------------------------------

@app.get("/launches/{launch_id}/derisk-events", response_model=list[DeRiskEventView])
async def get_derisk_events(
    launch_id: UUID,
    _key: str = Security(_verify_key),
):
    """De-risk trigger events for a specific launch."""
    rows = await db.fetch(
        """
        SELECT id, launch_id, trigger_type, severity::text, detail,
               resolved, created_at, resolved_at
        FROM derisk_events
        WHERE launch_id = $1
        ORDER BY created_at DESC
        """,
        launch_id,
    )
    results = []
    for r in rows:
        detail_raw = r.get("detail") or {}
        if isinstance(detail_raw, str):
            detail_raw = json.loads(detail_raw)
        results.append(DeRiskEventView(
            id=r["id"],
            launch_id=r["launch_id"],
            trigger_type=r["trigger_type"],
            severity=r["severity"],
            detail=detail_raw,
            resolved=r.get("resolved", False),
            created_at=r["created_at"],
            resolved_at=r.get("resolved_at"),
        ))
    return results
