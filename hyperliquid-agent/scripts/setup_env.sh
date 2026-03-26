#!/usr/bin/env bash
# Setup NXFH01 agent environment on Mac mini
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=== NXFH01 Setup ==="

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | sed 's/Python //' | cut -d. -f1,2)
MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 11 ]; }; then
    echo "ERROR: Python >= 3.11 required (found $PYTHON_VERSION)"
    exit 1
fi
echo "Python version: $PYTHON_VERSION"

# Create venv if not exists
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
fi

echo "Activating venv..."
source .venv/bin/activate

echo "Installing dependencies..."
pip install -e ".[dev]"

# Create data directory for SQLite
mkdir -p data

# Validate .env
if [ ! -f ".env" ]; then
    echo "WARNING: No .env file found. Copying template..."
    cp .env.template .env
    echo ""
    echo ">>> EDIT .env with your keys before running! <<<"
    echo ""
    echo "Required:"
    echo "  GAME_API_KEY        — from console.game.virtuals.io"
    echo "  HL_WALLET_ADDRESS   — Hyperliquid wallet for read-only data"
    echo ""
    echo "For LIVE trading (optional, runs in DRY-RUN without):"
    echo "  ACP_WALLET_PRIVATE_KEY — dev wallet private key (no 0x prefix)"
    echo "  ACP_WALLET_ADDRESS     — agent smart wallet address"
    echo "  ACP_ENTITY_ID          — agent entity ID from Virtuals"
fi

# Validate strategy config
if [ ! -f "strategy_config.yaml" ]; then
    echo "ERROR: strategy_config.yaml not found"
    exit 1
fi

# Quick import check
echo "Verifying imports..."
python3 -c "
from src.config import load_strategy_config, validate_required_env
from src.acp.degen_claw import DegenClawAcp
from src.market.data_feed import MarketDataFeed
from src.skill.functions import SKILL_FUNCTIONS
print(f'  {len(SKILL_FUNCTIONS)} skill functions loaded')
config = load_strategy_config()
print(f'  {len(config.allowed_markets.perps)} perp markets, {len(config.allowed_markets.rwa)} RWA markets')
print('  All imports OK')
"

echo ""
echo "=== Setup complete ==="
echo "Run: ./scripts/start.sh"
