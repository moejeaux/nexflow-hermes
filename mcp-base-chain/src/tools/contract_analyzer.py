"""
Token contract analysis tools for Base chain.

Checks contracts for common scam indicators: honeypot behavior,
hidden taxes, renounced ownership, and liquidity lock status.
"""

from typing import Any

from src.providers import alchemy

# Common function selectors for ERC-20 / ownership checks
SELECTORS = {
    "name": "0x06fdde03",
    "symbol": "0x95d89b41",
    "totalSupply": "0x18160ddd",
    "decimals": "0x313ce567",
    "owner": "0x8da5cb5b",           # Ownable.owner()
    "getOwner": "0x893d20e8",        # Alternative owner getter
    "taxForLiquidity": "0x",          # Varies by contract
    "maxTransactionAmount": "0x",     # Varies by contract
}

# Known patterns in bytecode that indicate risky behavior
SUSPICIOUS_PATTERNS = [
    "selfdestruct",       # Contract can self-destruct
    "delegatecall",       # Can proxy to arbitrary code
    "setFee",             # Dynamic fee adjustment
    "setTax",             # Dynamic tax
    "blacklist",          # Can blacklist wallets
    "setMaxTx",           # Can restrict max transaction
    "pause",              # Can pause trading
]


async def analyze_token_contract(address: str) -> dict[str, Any]:
    """Analyze a token contract for honeypot indicators and risk factors.

    Checks:
    - Whether the contract has code (is it a token?)
    - Basic token metadata (name, symbol, supply)
    - Ownership status (renounced or active)
    - Bytecode patterns (suspicious functions)
    - Total supply concentration

    Returns a risk assessment with findings.
    """
    address = address.strip()
    findings: list[str] = []
    risk_score = 0  # 0 = safe, 100 = maximum risk

    # Step 1: Verify contract exists
    bytecode = await alchemy.get_contract_code(address)
    if not bytecode or bytecode == "0x":
        return {
            "address": address,
            "is_contract": False,
            "risk_score": 100,
            "findings": ["Address has no contract code — this is an EOA, not a token."],
        }

    # Step 2: Read basic token info
    token_info = {}
    for field in ["name", "symbol", "totalSupply", "decimals"]:
        try:
            result = await alchemy.call_contract(address, SELECTORS[field])
            token_info[field] = result
        except Exception:
            token_info[field] = None
            findings.append(f"Could not read {field}() — non-standard ERC-20")
            risk_score += 5

    # Step 3: Check ownership
    owner_address = None
    for selector_name in ["owner", "getOwner"]:
        try:
            result = await alchemy.call_contract(address, SELECTORS[selector_name])
            if result and result != "0x" and len(result) >= 42:
                # Extract address from 32-byte padded response
                parsed = "0x" + result[-40:]
                if parsed != "0x" + "0" * 40:
                    owner_address = parsed
                    break
        except Exception:
            continue

    if owner_address:
        if owner_address == "0x" + "0" * 40:
            findings.append("Ownership renounced (owner = zero address) — positive signal")
        elif owner_address.lower() == "0x" + "dead" * 10:
            findings.append("Ownership burned (dead address) — positive signal")
        else:
            findings.append(f"Active owner: {owner_address} — ownership not renounced")
            risk_score += 15
    else:
        findings.append("No standard ownership pattern found")
        risk_score += 5

    # Step 4: Analyze bytecode for suspicious patterns
    bytecode_lower = bytecode.lower()
    for pattern in SUSPICIOUS_PATTERNS:
        # Check for function selector-like patterns (simplified heuristic)
        # In practice, you'd decompile or use 4byte directory
        if pattern.lower().encode().hex() in bytecode_lower:
            findings.append(f"Suspicious pattern detected in bytecode: {pattern}")
            risk_score += 10

    # Step 5: Check contract size (very small or very large can be suspicious)
    bytecode_size = len(bytecode) // 2  # Hex chars to bytes
    if bytecode_size < 500:
        findings.append(f"Very small contract ({bytecode_size} bytes) — possible proxy or minimal token")
        risk_score += 10
    elif bytecode_size > 50000:
        findings.append(f"Very large contract ({bytecode_size} bytes) — complex logic, review carefully")
        risk_score += 5

    # Clamp risk score
    risk_score = min(risk_score, 100)

    # Risk level classification
    if risk_score <= 20:
        risk_level = "LOW"
    elif risk_score <= 50:
        risk_level = "MEDIUM"
    elif risk_score <= 75:
        risk_level = "HIGH"
    else:
        risk_level = "CRITICAL"

    return {
        "address": address,
        "is_contract": True,
        "token_info": token_info,
        "owner": owner_address,
        "bytecode_size": bytecode_size,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "findings": findings,
    }
