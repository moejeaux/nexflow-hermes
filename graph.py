"""NXFX01 LangGraph Orchestration Layer.

Five-node graph (smart_money_node is a no-op until wired):

  intel_node → smart_money_node → risk_node → policy_node → execution_node → END

Each node delegates all real work to external functions. The graph
manages state flow only — no hardcoded scores, no dummy logic.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Literal, Optional, List, Dict, Any

import requests
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END

logger = logging.getLogger(__name__)


# =====================================================================
# Configuration helpers
# =====================================================================


def _env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int = 0) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("env %s=%r is not a valid int, using default %d", key, raw, default)
        return default


def _env_float(key: str, default: float = 0.0) -> float:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("env %s=%r is not a valid float, using default %f", key, raw, default)
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes")


# =====================================================================
# Configuration — override via environment variables
# =====================================================================

NXFX01_BASE_URL = _env_str("NXFX01_BASE_URL", "http://localhost:8000")
NXFX01_API_KEY = _env_str("NXFX01_API_KEY", "")
NXFX01_TIMEOUT_S = _env_int("NXFX01_TIMEOUT_S", 10)

EXECUTOR_URL = _env_str("EXECUTOR_URL", "http://localhost:9000")
EXECUTOR_TIMEOUT_S = _env_int("EXECUTOR_TIMEOUT_S", 15)

EXECUTION_ENABLED = _env_bool("EXECUTION_ENABLED", False)

# Retry settings for HTTP calls
HTTP_MAX_RETRIES = _env_int("HTTP_MAX_RETRIES", 2)
HTTP_RETRY_BACKOFF_S = _env_float("HTTP_RETRY_BACKOFF_S", 1.0)

# Risk thresholds (0–1 scale)
RISK_LAUNCH_SAFETY_MIN = _env_float("RISK_LAUNCH_SAFETY_MIN", 0.4)
RISK_LIQUIDITY_MIN = _env_float("RISK_LIQUIDITY_MIN", 0.3)
RISK_RUG_RISK_MAX = _env_float("RISK_RUG_RISK_MAX", 0.7)
RISK_DEPLOYER_REP_MIN = _env_float("RISK_DEPLOYER_REP_MIN", 0.3)
RISK_DATA_CONFIDENCE_MIN = _env_float("RISK_DATA_CONFIDENCE_MIN", 0.4)
RISK_DEPLOYER_VELOCITY_MAX = _env_int("RISK_DEPLOYER_VELOCITY_MAX", 5)
RISK_SMART_MONEY_MIN = _env_float("RISK_SMART_MONEY_MIN", 0.15)

# Trade policy thresholds
TRADE_COMPOSITE_MIN = _env_float("TRADE_COMPOSITE_MIN", 0.6)
TRADE_BASE_SIZE_USD = _env_float("TRADE_BASE_SIZE_USD", 100.0)
TRADE_MAX_SIZE_USD = _env_float("TRADE_MAX_SIZE_USD", 500.0)
TRADE_MAX_SLIPPAGE_BPS = _env_int("TRADE_MAX_SLIPPAGE_BPS", 250)
TRADE_DEFAULT_TTL_SEC = _env_int("TRADE_DEFAULT_TTL_SEC", 600)


# =====================================================================
# Observability
# =====================================================================


def log_state_snapshot(state: "GraphState", stage: str) -> None:
    """Log key GraphState fields at a given pipeline stage.

    Truncates token_address to first 10 chars to keep log lines
    readable. Never logs raw context (may contain large payloads).
    """
    token = (
        state.current_launch.token_address[:10] + "..."
        if state.current_launch and state.current_launch.token_address
        else "n/a"
    )
    logger.info(
        "[%s] token=%s  safety=%s  liq=%s  sm=%s  composite=%s  "
        "hard_reject=%s  decision=%s  executor=%s  tx=%s",
        stage,
        token,
        state.scores.launch_safety,
        state.scores.liquidity,
        state.scores.smart_money,
        state.scores.composite,
        state.risk.hard_reject,
        state.decision.status,
        state.decision.executor,
        state.decision.executed_tx_hash,
    )


# =====================================================================
# HTTP retry wrapper
# =====================================================================


def _http_with_retry(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json: dict | None = None,
    timeout: int = 10,
    max_retries: int = HTTP_MAX_RETRIES,
    backoff_s: float = HTTP_RETRY_BACKOFF_S,
) -> requests.Response:
    """Execute an HTTP request with deterministic retry + linear backoff.

    Retries only on connection errors and 5xx status codes. 4xx errors
    (bad request, not found, auth) are raised immediately — retrying
    them would be pointless.
    """
    last_exc: Exception | None = None
    for attempt in range(1 + max_retries):
        try:
            resp = requests.request(
                method, url, headers=headers, json=json, timeout=timeout,
            )
            if resp.status_code >= 500 and attempt < max_retries:
                logger.warning(
                    "HTTP %s %s returned %d (attempt %d/%d), retrying...",
                    method, url, resp.status_code, attempt + 1, 1 + max_retries,
                )
                time.sleep(backoff_s * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp
        except requests.ConnectionError as exc:
            last_exc = exc
            if attempt < max_retries:
                logger.warning(
                    "HTTP %s %s connection failed (attempt %d/%d): %s",
                    method, url, attempt + 1, 1 + max_retries, exc,
                )
                time.sleep(backoff_s * (attempt + 1))
                continue
            raise
    raise last_exc  # type: ignore[misc]


# =====================================================================
# 1. Canonical state schema (Pydantic, LangGraph-native)
# =====================================================================


class LaunchMetadata(BaseModel):
    chain: Literal["base"] = "base"
    token_address: str = ""
    pair_address: Optional[str] = None
    symbol: Optional[str] = None
    name: Optional[str] = None
    deployer: Optional[str] = None
    created_at: Optional[str] = None  # ISO timestamp
    nxfx01_launch_id: Optional[str] = None


class Scores(BaseModel):
    launch_safety: Optional[float] = None       # 0–1
    smart_money: Optional[float] = None         # 0–1
    obfuscation: Optional[float] = None         # 0–1 (risk/alpha modifier)
    liquidity: Optional[float] = None           # 0–1
    regime: Optional[float] = None              # 0–1 (macro/market regime fit)
    composite: Optional[float] = None           # 0–1 overall score


class RiskFlags(BaseModel):
    hard_reject: bool = False
    contract_risky: bool = False
    deployer_risky: bool = False
    rug_risk_high: bool = False
    wash_trading_suspected: bool = False
    liquidity_too_low: bool = False
    position_size_capped: bool = False
    notes: Optional[str] = None


class ProposedTrade(BaseModel):
    symbol: str
    chain: Literal["base"] = "base"
    token_address: str
    side: Literal["long", "short"] = "long"
    size_usd: float
    max_slippage_bps: int
    entry_type: Literal["FAST", "SLOW"] = "FAST"
    take_profit_targets: List[float] = Field(default_factory=list)
    stop_loss_price: Optional[float] = None
    time_in_force_sec: Optional[int] = None


class Decision(BaseModel):
    status: Literal["pending", "approved", "rejected"] = "pending"
    reason: Optional[str] = None
    executor: Optional[Literal["executor", "manual", "disabled"]] = None
    executed_tx_hash: Optional[str] = None


class GraphState(BaseModel):
    current_launch: Optional[LaunchMetadata] = None
    scores: Scores = Field(default_factory=Scores)
    risk: RiskFlags = Field(default_factory=RiskFlags)
    proposed_trade: Optional[ProposedTrade] = None
    decision: Decision = Field(default_factory=Decision)
    context: Dict[str, Any] = Field(default_factory=dict)
