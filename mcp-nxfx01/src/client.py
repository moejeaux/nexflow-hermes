"""
NXFX01 HTTP client.

Shared HTTP client for all NXFX01 API interactions.
Talks to the nxfx01-api FastAPI backend.
"""

import os
from typing import Any

import httpx


def _get_base_url() -> str:
    url = os.environ.get("NXFX01_API_URL", "http://localhost:8100")
    return url.rstrip("/")


def _get_api_key() -> str | None:
    return os.environ.get("NXFX01_API_KEY")


class Nxfx01Client:
    """Async HTTP client for the NXFX01 REST API."""

    def __init__(self):
        self._base_url = _get_base_url()
        self._api_key = _get_api_key()

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "hermes-mcp-nxfx01/0.1.0",
        }
        if self._api_key:
            h["x-api-key"] = self._api_key
        return h

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self._base_url}{path}",
                headers=self._headers(),
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def post(self, path: str, json_body: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base_url}{path}",
                headers=self._headers(),
                json=json_body or {},
            )
            resp.raise_for_status()
            return resp.json()
