"""
Alchemy provider — Base chain RPC client.

Handles all direct blockchain interactions: reading transactions, logs,
contract state, and balance queries via Alchemy's enhanced API on Base mainnet.
"""

import os
from typing import Any

import httpx
from web3 import AsyncWeb3, AsyncHTTPProvider

from src.cache import cached

# Base mainnet chain ID
BASE_CHAIN_ID = 8453


def _get_rpc_url() -> str:
    api_key = os.environ.get("ALCHEMY_API_KEY", "")
    if not api_key:
        raise RuntimeError("ALCHEMY_API_KEY environment variable is not set")
    return f"https://base-mainnet.g.alchemy.com/v2/{api_key}"


def _get_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_get_rpc_url(),
        timeout=30.0,
        headers={"Content-Type": "application/json"},
    )


def _get_web3() -> AsyncWeb3:
    return AsyncWeb3(AsyncHTTPProvider(_get_rpc_url()))


# ---- Wallet Operations ----

@cached("alchemy")
async def get_transactions(address: str, page_key: str | None = None) -> dict[str, Any]:
    """Fetch transactions for an address using Alchemy's enhanced API.

    Uses alchemy_getAssetTransfers for comprehensive tx history including
    ERC-20, ERC-721, and internal transfers.
    """
    params: dict[str, Any] = {
        "fromBlock": "0x0",
        "toBlock": "latest",
        "category": ["external", "internal", "erc20", "erc721", "erc1155"],
        "withMetadata": True,
        "order": "desc",
        "maxCount": "0x64",  # 100 results
    }
    # Search both sent and received — two separate calls
    results = {"sent": [], "received": []}

    async with _get_http_client() as client:
        # Outgoing transactions
        sent_params = {**params, "fromAddress": address}
        if page_key:
            sent_params["pageKey"] = page_key
        resp = await client.post("", json={
            "jsonrpc": "2.0", "id": 1,
            "method": "alchemy_getAssetTransfers",
            "params": [sent_params],
        })
        resp.raise_for_status()
        data = resp.json()
        results["sent"] = data.get("result", {}).get("transfers", [])

        # Incoming transactions
        recv_params = {**params, "toAddress": address}
        if page_key:
            recv_params["pageKey"] = page_key
        resp = await client.post("", json={
            "jsonrpc": "2.0", "id": 2,
            "method": "alchemy_getAssetTransfers",
            "params": [recv_params],
        })
        resp.raise_for_status()
        data = resp.json()
        results["received"] = data.get("result", {}).get("transfers", [])

    return results


@cached("alchemy")
async def get_balance(address: str) -> dict[str, Any]:
    """Get ETH balance and token balances for an address on Base."""
    w3 = _get_web3()
    eth_balance = await w3.eth.get_balance(address)

    # Also fetch token balances via Alchemy enhanced API
    async with _get_http_client() as client:
        resp = await client.post("", json={
            "jsonrpc": "2.0", "id": 1,
            "method": "alchemy_getTokenBalances",
            "params": [address],
        })
        resp.raise_for_status()
        token_data = resp.json()

    return {
        "eth_balance_wei": str(eth_balance),
        "eth_balance": str(w3.from_wei(eth_balance, "ether")),
        "token_balances": token_data.get("result", {}).get("tokenBalances", []),
    }


# ---- Contract / Log Operations ----

@cached("alchemy")
async def get_logs(
    address: str | None = None,
    topics: list[str] | None = None,
    from_block: str = "latest",
    to_block: str = "latest",
) -> list[dict[str, Any]]:
    """Fetch event logs from Base chain. Used for monitoring pool creation events etc."""
    params: dict[str, Any] = {
        "fromBlock": from_block,
        "toBlock": to_block,
    }
    if address:
        params["address"] = address
    if topics:
        params["topics"] = topics

    async with _get_http_client() as client:
        resp = await client.post("", json={
            "jsonrpc": "2.0", "id": 1,
            "method": "eth_getLogs",
            "params": [params],
        })
        resp.raise_for_status()
        data = resp.json()
    return data.get("result", [])


@cached("alchemy")
async def get_contract_code(address: str) -> str:
    """Get the bytecode deployed at an address. Empty string if EOA."""
    w3 = _get_web3()
    code = await w3.eth.get_code(address)
    return code.hex()


@cached("alchemy")
async def call_contract(
    address: str,
    data: str,
    block: str = "latest",
) -> str:
    """Execute a read-only contract call (eth_call)."""
    async with _get_http_client() as client:
        resp = await client.post("", json={
            "jsonrpc": "2.0", "id": 1,
            "method": "eth_call",
            "params": [{"to": address, "data": data}, block],
        })
        resp.raise_for_status()
        result = resp.json()
    return result.get("result", "0x")


@cached("alchemy")
async def get_block_number() -> int:
    """Get the latest block number on Base."""
    w3 = _get_web3()
    return await w3.eth.block_number
