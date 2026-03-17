# mcp-nexflow

MCP server that connects Hermes Agent to NexFlow services via HTTP APIs. Provides tools for Pulse (Cron-as-a-Service), SMF/x402 payment routing, and agent performance tracking.

## Important

This MCP server talks to NexFlow **only via HTTP APIs**. It never touches NexFlow source code or files directly.

## Tools

### Pulse (CAAS) — Scheduled Job Management
- `create_pulse_job(schedule, webhook_url, payload)` — Create a recurring scheduled job
- `list_pulse_jobs()` — Get all active jobs
- `delete_pulse_job(job_id)` — Remove a scheduled job
- `get_job_history(job_id)` — Execution history and status

### SMF / x402 — Payment Routing
- `get_facilitator_quote(amount, chain)` — Get a payment routing quote
- `verify_x402_payment(payment_hash)` — Verify a payment was settled
- `list_active_facilitators()` — Available payment facilitators

### Agent Stats — Performance Tracking
- `report_agent_stats(agent_id, metrics)` — Push performance metrics
- `get_agent_stats(agent_id)` — Pull current stats for any agent
- `log_revenue(agent_id, amount, source, job_id)` — Log a revenue event

## Setup

```bash
pip install -e .
```

## Required Environment Variables

```bash
NEXFLOW_API_KEY=your_api_key
NEXFLOW_BASE_URL=https://api.nexflowapp.app
```

## Running Standalone (for testing)

```bash
python -m src.server
```

## Example Tool Calls

### Create a Pulse Job
```json
{
  "method": "tools/call",
  "params": {
    "name": "create_pulse_job",
    "arguments": {
      "schedule": "*/15 * * * *",
      "webhook_url": "https://example.com/webhook",
      "payload": {"task": "scan_jobs"}
    }
  }
}
```

### Get Agent Stats
```json
{
  "method": "tools/call",
  "params": {
    "name": "get_agent_stats",
    "arguments": {
      "agent_id": "NXF011"
    }
  }
}
```

### Log Revenue
```json
{
  "method": "tools/call",
  "params": {
    "name": "log_revenue",
    "arguments": {
      "agent_id": "NXF011",
      "amount": 150.00,
      "source": "upwork_contract",
      "job_id": "job_abc123"
    }
  }
}
```

## Architecture

```
src/
├── server.py       # MCP server entry point, tool registration
├── client.py       # NexFlow HTTP client (shared across tools)
└── tools/
    ├── pulse.py        # Pulse CAAS tools
    ├── smf.py          # SMF/x402 tools
    └── agent_stats.py  # Agent metrics tools
```
