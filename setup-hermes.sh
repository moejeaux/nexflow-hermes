#!/usr/bin/env bash
# =============================================================================
# Hermes Agent Setup Script for NexFlow
# =============================================================================
# This script installs and configures Hermes Agent with Docker backend,
# messaging, cron, MCP, and Honcho (AI memory) extras.
#
# Prerequisites:
#   - macOS with Homebrew installed
#   - Docker Desktop for Mac (Apple Silicon) running
#   - Git installed
#   - Python 3.11+
#
# Usage:
#   chmod +x setup-hermes.sh && ./setup-hermes.sh
# =============================================================================

set -euo pipefail

HERMES_HOME="$HOME/.hermes"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log()   { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
echo "============================================="
echo "  Hermes Agent Setup for NexFlow"
echo "============================================="
echo ""

# Check Docker is running
if ! docker info &>/dev/null; then
  error "Docker is not running. Start Docker Desktop and re-run this script."
fi
log "Docker is running"

# Check Python 3.11+
if ! command -v python3 &>/dev/null; then
  error "Python3 not found. Install via: brew install python@3.11"
fi
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
log "Python $PY_VERSION detected"

# Check Git
if ! command -v git &>/dev/null; then
  error "Git not found. Install via: brew install git"
fi
log "Git available"

# ---------------------------------------------------------------------------
# Step 1: Install Hermes Agent (official installer)
# ---------------------------------------------------------------------------
echo ""
echo "--- Step 1: Installing Hermes Agent ---"

if command -v hermes &>/dev/null; then
  warn "Hermes is already installed. Skipping installation."
else
  log "Running official Hermes installer..."
  curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
  log "Hermes installed successfully"
fi

# ---------------------------------------------------------------------------
# Step 2: Create directory structure
# ---------------------------------------------------------------------------
echo ""
echo "--- Step 2: Creating directory structure ---"

mkdir -p "$HERMES_HOME"/{skills,memory,logs,mcp}
log "Created $HERMES_HOME/ directory tree"

# ---------------------------------------------------------------------------
# Step 3: Install Python extras (messaging, cron, mcp, honcho)
# ---------------------------------------------------------------------------
echo ""
echo "--- Step 3: Installing Hermes extras ---"

# Install extras if hermes is pip-installed
if pip3 show hermes-agent &>/dev/null 2>&1; then
  pip3 install "hermes-agent[messaging,cron,mcp,honcho]" --upgrade
  log "Installed extras: messaging, cron, mcp, honcho"
else
  warn "Hermes not found via pip — extras may need manual installation."
  warn "Try: pip3 install 'hermes-agent[messaging,cron,mcp,honcho]'"
fi

# ---------------------------------------------------------------------------
# Step 4: Copy configuration files
# ---------------------------------------------------------------------------
echo ""
echo "--- Step 4: Deploying configuration ---"

# Copy config.yaml to Hermes home
cp "$PROJECT_DIR/config.yaml" "$HERMES_HOME/config.yaml"
log "Copied config.yaml → $HERMES_HOME/config.yaml"

# Copy cron jobs
if [ -f "$PROJECT_DIR/cron-jobs.yaml" ]; then
  cp "$PROJECT_DIR/cron-jobs.yaml" "$HERMES_HOME/cron-jobs.yaml"
  log "Copied cron-jobs.yaml → $HERMES_HOME/cron-jobs.yaml"
fi

# Copy skill documents
if [ -d "$PROJECT_DIR/skills" ]; then
  cp "$PROJECT_DIR/skills/"*.md "$HERMES_HOME/skills/" 2>/dev/null || true
  log "Copied skill documents → $HERMES_HOME/skills/"
fi

# ---------------------------------------------------------------------------
# Step 5: Set up .env file
# ---------------------------------------------------------------------------
echo ""
echo "--- Step 5: Environment configuration ---"

if [ -f "$HERMES_HOME/.env" ]; then
  warn ".env already exists at $HERMES_HOME/.env — skipping to preserve secrets."
  warn "Compare with .env.template for any new variables."
else
  cp "$PROJECT_DIR/.env.template" "$HERMES_HOME/.env"
  log "Created $HERMES_HOME/.env from template"
  warn "IMPORTANT: Edit $HERMES_HOME/.env and fill in your API keys before starting Hermes."
fi

# ---------------------------------------------------------------------------
# Step 6: Set up MCP servers
# ---------------------------------------------------------------------------
echo ""
echo "--- Step 6: Setting up MCP servers ---"

# Install mcp-base-chain
if [ -d "$PROJECT_DIR/mcp-base-chain" ]; then
  echo "  Installing mcp-base-chain..."
  cd "$PROJECT_DIR/mcp-base-chain"
  pip3 install -e . 2>/dev/null || pip3 install -r requirements.txt 2>/dev/null || true
  log "mcp-base-chain installed"
fi

# Install mcp-nexflow
if [ -d "$PROJECT_DIR/mcp-nexflow" ]; then
  echo "  Installing mcp-nexflow..."
  cd "$PROJECT_DIR/mcp-nexflow"
  pip3 install -e . 2>/dev/null || pip3 install -r requirements.txt 2>/dev/null || true
  log "mcp-nexflow installed"
fi

# Install mcp-basescan (Node/TypeScript)
if [ -d "$PROJECT_DIR/mcp-basescan" ]; then
  echo "  Installing mcp-basescan..."
  if ! command -v node &>/dev/null; then
    warn "Node.js not found. Install via: brew install node"
  else
    cd "$PROJECT_DIR/mcp-basescan"
    npm install
    npm run build
    log "mcp-basescan installed and built"
  fi
fi

cd "$PROJECT_DIR"

# ---------------------------------------------------------------------------
# Step 7: Pull Docker image
# ---------------------------------------------------------------------------
echo ""
echo "--- Step 7: Pulling Docker base image ---"

docker pull python:3.11-slim
log "Docker image python:3.11-slim ready"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "============================================="
echo "  Setup Complete!"
echo "============================================="
echo ""
echo "Next steps:"
echo "  1. Edit $HERMES_HOME/.env with your API keys"
echo "  2. Start Hermes: hermes start"
echo "  3. Paste NEXFLOW_CONTEXT.md into the first conversation"
echo "  4. Hermes will begin autonomous operations per cron-jobs.yaml"
echo ""
echo "Directory: $HERMES_HOME"
echo "Config:    $HERMES_HOME/config.yaml"
echo "Skills:    $HERMES_HOME/skills/"
echo "Logs:      $HERMES_HOME/logs/"
echo ""
