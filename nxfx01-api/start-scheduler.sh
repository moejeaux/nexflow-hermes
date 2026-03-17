#!/usr/bin/env bash
# PM2 wrapper for nxfx01-scheduler
cd /Users/nexflow/hermes-nexflow/nxfx01-api
set -a
source /Users/nexflow/.hermes/.env
set +a
exec .venv/bin/python -m src.scheduler
