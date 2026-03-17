"""
SMF / x402 payment tools.

Tools for interacting with NexFlow's Smart Meta-Facilitator for payment
routing, verification, and settlement via the x402 protocol.
"""

from typing import Any

from src.client import nexflow


async def get_facilitator_quote(amount: float, chain: str = "base") -> dict[str, Any]:
    """Get a payment routing quote from NexFlow SMF.

    Args:
        amount: USD amount to route
        chain: Target chain (default "base")
    """
    params = {"amount": amount, "chain": chain}
    return await nexflow.get("/api/v1/smf/quote", params=params)


async def verify_x402_payment(payment_hash: str) -> dict[str, Any]:
    """Verify that an x402 payment was received and settled.

    Args:
        payment_hash: The transaction or payment hash to verify
    """
    return await nexflow.get(f"/api/v1/smf/verify/{payment_hash}")


async def list_active_facilitators() -> dict[str, Any]:
    """List all available payment facilitators in the NexFlow network."""
    return await nexflow.get("/api/v1/smf/facilitators")
