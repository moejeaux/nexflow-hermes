#!/usr/bin/env bash
# Start NXFH01 agent in tmux
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SESSION="nxfh01"

cd "$PROJECT_DIR"

# Check for tmux
if ! command -v tmux &>/dev/null; then
    echo "tmux not found — running directly..."
    source .venv/bin/activate
    python -m src.main
    exit 0
fi

# Create or attach to tmux session
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session $SESSION already exists. Attaching..."
    tmux attach -t "$SESSION"
else
    echo "Starting $SESSION in tmux..."
    tmux new-session -d -s "$SESSION" -n agent "cd $PROJECT_DIR && source .venv/bin/activate && python -m src.main; read"
    tmux attach -t "$SESSION"
fi
