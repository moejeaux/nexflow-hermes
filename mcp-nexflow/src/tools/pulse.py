"""
Pulse (CAAS) tools — Cron-as-a-Service job management.

Create, list, delete, and inspect scheduled jobs via the NexFlow Pulse API.
"""

from typing import Any

from src.client import nexflow


async def create_pulse_job(
    schedule: str,
    webhook_url: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a recurring scheduled job in NexFlow Pulse.

    Args:
        schedule: Cron expression (e.g. "*/15 * * * *")
        webhook_url: URL to call when the job fires
        payload: JSON payload to include in the webhook request
    """
    body = {
        "schedule": schedule,
        "webhook_url": webhook_url,
        "payload": payload or {},
        "enabled": True,
    }
    return await nexflow.post("/api/v1/jobs", json_body=body)


async def list_pulse_jobs() -> dict[str, Any]:
    """Get all active scheduled jobs from NexFlow Pulse."""
    return await nexflow.get("/api/v1/jobs")


async def delete_pulse_job(job_id: str) -> dict[str, Any]:
    """Remove a scheduled job from NexFlow Pulse.

    Args:
        job_id: The unique identifier of the job to delete
    """
    return await nexflow.delete(f"/api/v1/jobs/{job_id}")


async def get_job_history(job_id: str) -> dict[str, Any]:
    """Get execution history and status for a specific Pulse job.

    Args:
        job_id: The unique identifier of the job
    """
    return await nexflow.get(f"/api/v1/jobs/{job_id}/history")
