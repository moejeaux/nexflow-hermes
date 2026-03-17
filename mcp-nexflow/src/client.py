"""
NexFlow HTTP client.

Shared HTTP client for all NexFlow API interactions. Handles authentication,
base URL configuration, retries, and error formatting.

IMPORTANT: This client only communicates with NexFlow via HTTP APIs.
It never reads or writes NexFlow source files.
"""

import os
from typing import Any

import httpx


def _get_base_url() -> str:
    url = os.environ.get("NEXFLOW_BASE_URL", "https://api.nexflowapp.app")
    return url.rstrip("/")


def _get_api_key() -> str:
    key = os.environ.get("NEXFLOW_API_KEY", "")
    if not key:
        raise RuntimeError("NEXFLOW_API_KEY environment variable is not set")
    return key


class NexFlowClient:
    """Async HTTP client for the NexFlow REST API."""

    def __init__(self):
        self._base_url = _get_base_url()
        self._api_key = _get_api_key()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": "hermes-mcp-nexflow/0.1.0",
        }

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a GET request to NexFlow API."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self._base_url}{path}",
                headers=self._headers(),
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def post(self, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a POST request to NexFlow API."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base_url}{path}",
                headers=self._headers(),
                json=json_body or {},
            )
            resp.raise_for_status()
            return resp.json()

    async def delete(self, path: str) -> dict[str, Any]:
        """Send a DELETE request to NexFlow API."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(
                f"{self._base_url}{path}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            # Some endpoints may return 204 No Content
            if resp.status_code == 204:
                return {"status": "deleted"}
            return resp.json()


# Singleton instance — created on first import
nexflow = NexFlowClient()
