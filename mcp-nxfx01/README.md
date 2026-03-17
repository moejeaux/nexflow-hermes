# mcp-nxfx01

MCP server that exposes NXFX01 Launch Intelligence tools to Hermes.

Communicates with the nxfx01-api FastAPI backend via HTTP.

## Tools

| Tool | Description |
|------|-------------|
| `get_recent_launches` | Recent launches on Base (newest first) |
| `get_actionable_launches` | Tradable launches filtered by mode & safety |
| `get_launch_details` | Full launch view with scores, notes, participants |
| `get_wallet_profile` | Wallet/cluster profile and tier info |
| `get_past_launch_outcomes` | Historical outcomes for self-learning |
| `update_launch_policy_suggestion` | Propose scoring policy adjustments |

## Setup

```bash
pip install -e .
```

Set environment variables:
- `NXFX01_API_URL` — Base URL (default: `http://localhost:8100`)
- `NXFX01_API_KEY` — API key for auth

## Run

```bash
python -m src.server
```
