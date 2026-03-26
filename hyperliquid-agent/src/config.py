"""Configuration loader — env vars (secrets) + YAML (strategy params)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _ROOT / "strategy_config.yaml"


# ── env-var helpers ──────────────────────────────────────────────────────────

def _env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_float(key: str, default: float = 0.0) -> float:
    return float(os.getenv(key, str(default)))


def _env_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


# ── secrets from env ─────────────────────────────────────────────────────────

# ACP CLI directory (openclaw-acp installation)
ACP_CLI_DIR: str = os.path.expanduser(_env_str("ACP_CLI_DIR", "~/nexflow-virtuals/openclaw-acp"))

# ACP credentials (kept for reference, no longer used by Python SDK)
ACP_WALLET_PRIVATE_KEY: str = _env_str("ACP_WALLET_PRIVATE_KEY")
ACP_WALLET_ADDRESS: str = _env_str("ACP_WALLET_ADDRESS")
ACP_ENTITY_ID: str = _env_str("ACP_ENTITY_ID")

# G.A.M.E. SDK
GAME_API_KEY: str = _env_str("GAME_API_KEY")
GAME_AGENT_ID: str = _env_str("GAME_AGENT_ID")

# Hyperliquid read-only
HL_WALLET_ADDRESS: str = _env_str("HL_WALLET_ADDRESS")
HL_API_URL: str = _env_str("HL_API_URL", "https://api.hyperliquid.xyz")
HL_KILL_SWITCH: bool = _env_bool("HL_KILL_SWITCH", False)

# Degen Claw managed equity (HL subaccount balance not queryable directly)
INITIAL_EQUITY: float = _env_float("INITIAL_EQUITY", 0.0)

# Agent step interval (seconds) — 30s keeps within GAME free tier (10 calls / 5 min)
STEP_INTERVAL: int = _env_int("STEP_INTERVAL", 30)

# Notifications
TELEGRAM_BOT_TOKEN: str = _env_str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str = _env_str("TELEGRAM_CHAT_ID")


def validate_required_env() -> list[str]:
    """Check that all required env vars are set. Returns list of missing vars."""
    required = {
        "GAME_API_KEY": GAME_API_KEY,
        "HL_WALLET_ADDRESS": HL_WALLET_ADDRESS,
    }
    missing = [k for k, v in required.items() if not v]
    for k in missing:
        logger.error("Missing required env var: %s", k)

    # ACP CLI check
    if not os.path.isdir(ACP_CLI_DIR):
        logger.warning(
            "ACP_CLI_DIR=%s not found — agent will run in DRY-RUN mode",
            ACP_CLI_DIR,
        )

    return missing


# ── strategy config from YAML ────────────────────────────────────────────────

@dataclass
class FundingCarryConfig:
    enabled: bool = True
    min_funding_rate_hourly: float = 0.0011
    delta_neutral: bool = True
    prefer_maker: bool = True
    exit_after_funding_window: bool = True


@dataclass
class MomentumConfig:
    enabled: bool = True
    btc_regime_gate: bool = True
    entry_on_pullback: bool = True
    allow_breakout_entry: bool = False
    min_rr_ratio: float = 3.0


@dataclass
class SmartMoneyConfig:
    enabled: bool = True
    confirmation_only: bool = True
    max_freshness_minutes: int = 15


@dataclass
class RwaConfig:
    enabled: bool = False
    macro_window_required: bool = True
    max_holding_hours: int = 8
    risk_cap_multiplier: float = 0.5


@dataclass
class RiskConfig:
    max_leverage_per_asset: float = 5.0
    risk_per_trade_pct: float = 0.02
    max_daily_loss_pct: float = 0.05
    max_drawdown_soft_pct: float = 0.08
    max_drawdown_hard_pct: float = 0.15
    max_concurrent_positions: int = 3


@dataclass
class BtcRegimeConfig:
    timeframe: str = "4h"
    trend_ema_fast: int = 20
    trend_ema_slow: int = 50


@dataclass
class AllowedMarkets:
    perps: list[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL", "DOGE"])
    rwa: list[str] = field(default_factory=lambda: ["OIL", "GOLD", "SPX"])

    @property
    def all(self) -> set[str]:
        return set(self.perps) | set(self.rwa)


@dataclass
class StrategyConfig:
    """Top-level configuration assembled from YAML + env."""

    allowed_markets: AllowedMarkets = field(default_factory=AllowedMarkets)
    funding_carry: FundingCarryConfig = field(default_factory=FundingCarryConfig)
    momentum: MomentumConfig = field(default_factory=MomentumConfig)
    smart_money: SmartMoneyConfig = field(default_factory=SmartMoneyConfig)
    rwa: RwaConfig = field(default_factory=RwaConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    btc_regime: BtcRegimeConfig = field(default_factory=BtcRegimeConfig)


def _build_dataclass(cls, raw: dict[str, Any] | None):
    """Build a dataclass from a dict, ignoring unknown keys."""
    if not raw:
        return cls()
    valid = {k: v for k, v in raw.items() if k in {f.name for f in cls.__dataclass_fields__.values()}}
    return cls(**valid)


def load_strategy_config(path: Path | None = None) -> StrategyConfig:
    """Load strategy_config.yaml and return a typed StrategyConfig."""
    path = path or _CONFIG_PATH
    if not path.exists():
        logger.warning("No strategy_config.yaml found at %s — using defaults", path)
        return StrategyConfig()

    raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    strategies = raw.get("strategies", {})

    config = StrategyConfig(
        allowed_markets=_build_dataclass(AllowedMarkets, raw.get("allowed_markets")),
        funding_carry=_build_dataclass(FundingCarryConfig, strategies.get("funding_carry")),
        momentum=_build_dataclass(MomentumConfig, strategies.get("momentum")),
        smart_money=_build_dataclass(SmartMoneyConfig, strategies.get("smart_money")),
        rwa=_build_dataclass(RwaConfig, strategies.get("rwa")),
        risk=_build_dataclass(RiskConfig, raw.get("risk")),
        btc_regime=_build_dataclass(BtcRegimeConfig, raw.get("btc_regime")),
    )

    logger.info(
        "Loaded config: %d perps, %d RWA, max_leverage=%.1f, max_dd=%.0f%%",
        len(config.allowed_markets.perps),
        len(config.allowed_markets.rwa),
        config.risk.max_leverage_per_asset,
        config.risk.max_drawdown_hard_pct * 100,
    )
    return config
