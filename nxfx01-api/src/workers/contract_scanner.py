"""Contract Scanner — analyzes token contracts for safety scoring.

Picks launches in 'pending_initial' status where contract_safety IS NULL.
Runs contract analysis (bytecode heuristics + GoPlus honeypot check),
computes bytecode_hash for fingerprinting, checks against bad_templates.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx

from src import db

logger = logging.getLogger("nxfx01.contract_scanner")

BLOCKSCOUT_BASE = "https://base.blockscout.com"
GOPLUS_BASE = "https://api.gopluslabs.io"
BASE_CHAIN_ID = "8453"

# Suspicious bytecode patterns (text fragments in hex-encoded bytecode)
SUSPICIOUS_PATTERNS = {
    "selfdestruct": 30,
    "delegatecall": 10,
    "setFee": 10,
    "setTax": 10,
    "blacklist": 15,
    "setMaxTx": 10,
    "pause": 8,
    "mint": 5,       # only if combined with active owner
}

# Patterns that are critical — auto-flag
CRITICAL_PATTERNS = {"selfdestruct"}


async def _get_bytecode(client: httpx.AsyncClient, address: str) -> str | None:
    """Fetch contract bytecode from Blockscout."""
    try:
        resp = await client.get(
            f"{BLOCKSCOUT_BASE}/api/v2/smart-contracts/{address}",
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return data.get("deployed_bytecode") or data.get("bytecode")
    except Exception as e:
        logger.warning("Failed to fetch bytecode for %s: %s", address, e)
        return None


def _normalize_bytecode_hash(bytecode: str) -> str:
    """Strip constructor args and hash the deployed bytecode for fingerprinting."""
    # Remove 0x prefix
    code = bytecode.removeprefix("0x")
    # Constructor args are at the end; a rough heuristic is to take the first 80%
    # of bytecode (metadata/constructor is typically appended at the end).
    # For a more precise approach, look for the CBOR-encoded metadata marker (a264).
    cbor_marker = code.rfind("a264")
    if cbor_marker > 0:
        code = code[:cbor_marker]
    elif len(code) > 100:
        code = code[: int(len(code) * 0.8)]
    return hashlib.sha256(code.encode()).hexdigest()


def _analyze_bytecode(bytecode: str) -> tuple[int, list[str], list[str]]:
    """Analyze bytecode for suspicious patterns.

    Returns (penalty_score, findings, red_flags).
    """
    bytecode_lower = bytecode.lower()
    penalty = 0
    findings: list[str] = []
    red_flags: list[str] = []

    for pattern, weight in SUSPICIOUS_PATTERNS.items():
        if pattern.lower().encode().hex() in bytecode_lower:
            findings.append(f"Suspicious pattern: {pattern}")
            penalty += weight
            if pattern in CRITICAL_PATTERNS:
                red_flags.append(f"selfdestruct_detected")

    # Size checks
    bytecode_size = len(bytecode) // 2
    if bytecode_size < 500:
        findings.append(f"Very small contract ({bytecode_size} bytes)")
        penalty += 10
    elif bytecode_size > 50000:
        findings.append(f"Very large contract ({bytecode_size} bytes)")
        penalty += 5

    return penalty, findings, red_flags


async def _goplus_check(client: httpx.AsyncClient, address: str) -> dict[str, Any]:
    """Query GoPlus for honeypot and token safety data. Advisory only."""
    try:
        resp = await client.get(
            f"{GOPLUS_BASE}/api/v1/token_security/{BASE_CHAIN_ID}",
            params={"contract_addresses": address},
            timeout=10,
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
        result = data.get("result", {}).get(address.lower(), {})
        return result
    except Exception as e:
        logger.warning("GoPlus check failed for %s: %s", address, e)
        return {}


async def _check_ownership(client: httpx.AsyncClient, address: str) -> tuple[str | None, int, list[str]]:
    """Check contract ownership via Blockscout read methods."""
    penalty = 0
    findings: list[str] = []
    owner = None

    try:
        resp = await client.get(
            f"{BLOCKSCOUT_BASE}/api/v2/smart-contracts/{address}/methods-read",
            timeout=10,
        )
        if resp.status_code == 200:
            methods = resp.json()
            for method in methods if isinstance(methods, list) else []:
                if method.get("name") in ("owner", "getOwner"):
                    outputs = method.get("outputs", [])
                    if outputs and outputs[0].get("value"):
                        owner = outputs[0]["value"].lower()
                        break
    except Exception:
        pass

    if owner:
        zero = "0x" + "0" * 40
        dead = "0x" + "dead" * 10
        if owner == zero or owner == dead:
            findings.append("Ownership renounced — positive signal")
        else:
            findings.append(f"Active owner: {owner}")
            penalty += 15
    else:
        findings.append("No standard ownership pattern detected")
        penalty += 5

    return owner, penalty, findings


async def scan_contract(launch_id: str, token_address: str) -> dict[str, Any]:
    """Full contract safety analysis for a single launch."""
    total_penalty = 0
    all_findings: list[str] = []
    red_flags: list[str] = []

    async with httpx.AsyncClient() as client:
        # 1. Fetch and analyze bytecode
        bytecode = await _get_bytecode(client, token_address)
        bytecode_hash = None

        if not bytecode or bytecode == "0x":
            all_findings.append("No contract code found — possible EOA or unverified")
            total_penalty += 50
            red_flags.append("no_contract_code")
        else:
            bytecode_hash = _normalize_bytecode_hash(bytecode)
            penalty, findings, flags = _analyze_bytecode(bytecode)
            total_penalty += penalty
            all_findings.extend(findings)
            red_flags.extend(flags)

            # Check against bad_templates
            is_bad = await db.fetchval(
                "SELECT label FROM bad_templates WHERE bytecode_hash = $1",
                bytecode_hash,
            )
            if is_bad:
                red_flags.append(f"bad_template_match:{is_bad}")
                all_findings.append(f"Bytecode matches known bad template: {is_bad}")
                total_penalty += 40  # bad_template_penalty from policy

        # 2. Check ownership
        owner, own_penalty, own_findings = await _check_ownership(client, token_address)
        total_penalty += own_penalty
        all_findings.extend(own_findings)

        # 3. GoPlus honeypot check — penalize bad signals, reward clean ones
        goplus = await _goplus_check(client, token_address)
        goplus_clean = False
        if goplus:
            is_honeypot = goplus.get("is_honeypot") == "1"
            is_mintable = goplus.get("is_mintable") == "1"
            can_reclaim = goplus.get("can_take_back_ownership") == "1"
            buy_tax = float(goplus.get("buy_tax", "0") or "0")
            sell_tax = float(goplus.get("sell_tax", "0") or "0")

            if is_honeypot:
                red_flags.append("confirmed_honeypot")
                all_findings.append("GoPlus: confirmed honeypot")
                total_penalty += 50

            if is_mintable and owner:
                red_flags.append("mint_authority_active")
                all_findings.append("GoPlus: token is mintable with active owner")
                total_penalty += 20

            if can_reclaim:
                all_findings.append("GoPlus: ownership can be reclaimed")
                total_penalty += 15

            if buy_tax > 0.05 or sell_tax > 0.05:
                all_findings.append(f"GoPlus: high taxes (buy={buy_tax:.1%}, sell={sell_tax:.1%})")
                total_penalty += 15
            if sell_tax > 0.50:
                red_flags.append("confirmed_honeypot")
                total_penalty += 30

            # Junk-token filters: not actually trading or dead
            is_in_dex = goplus.get("is_in_dex") == "1"
            holder_count = int(goplus.get("holder_count", "0") or "0")

            if not is_in_dex:
                red_flags.append("not_in_dex")
                all_findings.append("GoPlus: token is NOT in any DEX — not tradeable")
                total_penalty += 60

            if holder_count <= 1:
                all_findings.append(f"GoPlus: only {holder_count} holder(s) — dead/test token")
                total_penalty += 20

            # Positive signal: GoPlus confirms clean token (only if actually trading)
            if (is_in_dex and not is_honeypot and not is_mintable and not can_reclaim
                    and buy_tax <= 0.05 and sell_tax <= 0.05):
                goplus_clean = True
                all_findings.append("GoPlus: clean token (no honeypot, no mint, low taxes)")
                total_penalty -= 25  # offset no_contract_code penalty for unverified but clean tokens

    # Compute contract_safety score (inverted: 100 = safe, 0 = dangerous)
    contract_safety = max(0, min(100, 100 - total_penalty))

    # Store raw signals for debugging / re-sim
    raw_signals = {
        "bytecode_hash": bytecode_hash,
        "goplus": goplus,
        "owner": owner,
        "penalty_breakdown": total_penalty,
    }

    # Update DB
    notes_patch = {
        "contract_red_flags": red_flags,
    }

    await db.execute(
        """
        UPDATE launches
        SET contract_safety = $1,
            bytecode_hash = $2,
            notes = notes || $3::jsonb,
            raw_signals = raw_signals || $4::jsonb
        WHERE launch_id = $5
        """,
        contract_safety,
        bytecode_hash,
        __import__("json").dumps(notes_patch),
        __import__("json").dumps({"contract_scan": raw_signals}),
        launch_id,
    )

    logger.info(
        "Contract scan: %s safety=%d, red_flags=%s",
        token_address, contract_safety, red_flags,
    )

    return {
        "launch_id": launch_id,
        "contract_safety": contract_safety,
        "bytecode_hash": bytecode_hash,
        "findings": all_findings,
        "red_flags": red_flags,
    }


async def run() -> dict:
    """Process all launches pending contract analysis."""
    rows = await db.fetch(
        """
        SELECT launch_id, token_address FROM launches
        WHERE status = 'pending_initial' AND contract_safety IS NULL
        ORDER BY detected_at ASC
        LIMIT 50
        """
    )

    results = []
    for row in rows:
        result = await scan_contract(str(row["launch_id"]), row["token_address"])
        results.append(result)

    return {"processed": len(results), "results": results}
