"""
Agent stats tools — performance tracking for the NXFX01 ecosystem.

Push metrics, pull stats, and log revenue events for individual agents.
Used by the NXFX01 orchestrator to monitor and evaluate child agents.
"""

from typing import Any

from src.client import nexflow


async def report_agent_stats(agent_id: str, metrics: dict[str, Any]) -> dict[str, Any]:
    """Push performance metrics for an agent.

    Args:
        agent_id: Agent identifier (e.g. "NXF011", "NXFX01")
        metrics: Dict of metric key-value pairs to report
    """
    body = {
        "agent_id": agent_id,
        "metrics": metrics,
    }
    return await nexflow.post("/api/v1/agents/stats", json_body=body)


async def get_agent_stats(agent_id: str) -> dict[str, Any]:
    """Pull current stats for any agent in the ecosystem.

    Args:
        agent_id: Agent identifier (e.g. "NXF011", "NXFX01")
    """
    return await nexflow.get(f"/api/v1/agents/{agent_id}/stats")


async def log_revenue(
    agent_id: str,
    amount: float,
    source: str,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Log a revenue event attributed to a specific agent.

    Args:
        agent_id: Agent identifier that generated the revenue
        amount: USD amount earned
        source: Revenue source (e.g. "upwork_contract", "signal_sale", "x402_payment")
        job_id: Optional job identifier for traceability
    """
    body = {
        "agent_id": agent_id,
        "amount": amount,
        "source": source,
    }
    if job_id:
        body["job_id"] = job_id
    return await nexflow.post("/api/v1/agents/revenue", json_body=body)
