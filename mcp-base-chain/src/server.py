"""
MCP Server — Base Chain Intelligence

Entry point for the MCP server that exposes Base chain monitoring tools
to Hermes Agent. Registers all tools from the tools/ directory and handles
MCP protocol communication.

Run standalone: python -m src.server
"""

import asyncio
import json
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from src.tools import wallet_monitor, pool_events, contract_analyzer, trend_detection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("mcp-base-chain")

# Create the MCP server instance
server = Server("base-chain")


# ---- Tool Definitions ----

TOOLS = [
    Tool(
        name="watch_wallet",
        description=(
            "Start tracking a wallet's transactions on Base chain. "
            "Returns current balance and recent activity summary."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Ethereum address to watch (0x-prefixed)",
                },
            },
            "required": ["address"],
        },
    ),
    Tool(
        name="get_wallet_history",
        description=(
            "Pull transaction history for a wallet including transfers "
            "and DEX trading activity on Base chain."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Ethereum address (0x-prefixed)",
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days of history to fetch (1-90, default 7)",
                    "default": 7,
                },
            },
            "required": ["address"],
        },
    ),
    Tool(
        name="detect_whale_movements",
        description=(
            "Flag large-value transfers on Base chain within the last hour. "
            "Useful for tracking smart money and whale activity."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "min_value_usd": {
                    "type": "number",
                    "description": "Minimum USD value to flag (default 50000)",
                    "default": 50000,
                },
            },
        },
    ),
    Tool(
        name="monitor_new_pools",
        description=(
            "Watch for new trading pair creation events on Uniswap V3 and "
            "other Base DEXs. Returns both on-chain events and Bitquery data."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="analyze_token_contract",
        description=(
            "Analyze a token contract for honeypot indicators, ownership status, "
            "hidden taxes, and other risk factors. Returns a risk score 0-100."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Token contract address to analyze (0x-prefixed)",
                },
            },
            "required": ["address"],
        },
    ),
    Tool(
        name="get_pool_liquidity",
        description=(
            "Get current liquidity and state for a Uniswap V3 pool on Base chain."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pair_address": {
                    "type": "string",
                    "description": "Pool/pair contract address (0x-prefixed)",
                },
            },
            "required": ["pair_address"],
        },
    ),
    Tool(
        name="get_trending_tokens",
        description=(
            "Get tokens with the highest trade volume on Base DEXs. "
            "Includes buyer/seller analysis and wash trading detection."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "timeframe": {
                    "type": "string",
                    "description": "Time window: '1h', '4h', '24h', or '7d' (default '24h')",
                    "enum": ["1h", "4h", "24h", "7d"],
                    "default": "24h",
                },
            },
        },
    ),
    Tool(
        name="detect_volume_anomalies",
        description=(
            "Flag unusual volume spikes for a specific token by comparing "
            "short-term vs long-term volume. Detects potential insider or manipulation activity."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "token_address": {
                    "type": "string",
                    "description": "Token contract address to check (0x-prefixed)",
                },
            },
            "required": ["token_address"],
        },
    ),
    Tool(
        name="cluster_wallet_behavior",
        description=(
            "Analyze a group of wallets to identify smart money patterns and "
            "coordination signals. Checks for common tokens traded across wallets."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "addresses": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of wallet addresses to analyze (max 20)",
                },
            },
            "required": ["addresses"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route tool calls to the appropriate handler function."""
    logger.info(f"Tool call: {name} with args: {json.dumps(arguments, default=str)[:200]}")

    try:
        # Wallet monitoring tools
        if name == "watch_wallet":
            result = await wallet_monitor.watch_wallet(arguments["address"])
        elif name == "get_wallet_history":
            result = await wallet_monitor.get_wallet_history(
                arguments["address"],
                arguments.get("days", 7),
            )
        elif name == "detect_whale_movements":
            result = await wallet_monitor.detect_whale_movements(
                arguments.get("min_value_usd", 50000),
            )

        # Pool event tools
        elif name == "monitor_new_pools":
            result = await pool_events.monitor_new_pools()
        elif name == "get_pool_liquidity":
            result = await pool_events.get_pool_liquidity(arguments["pair_address"])

        # Contract analysis tools
        elif name == "analyze_token_contract":
            result = await contract_analyzer.analyze_token_contract(arguments["address"])

        # Trend detection tools
        elif name == "get_trending_tokens":
            result = await trend_detection.get_trending_tokens(
                arguments.get("timeframe", "24h"),
            )
        elif name == "detect_volume_anomalies":
            result = await trend_detection.detect_volume_anomalies(
                arguments["token_address"],
            )
        elif name == "cluster_wallet_behavior":
            result = await trend_detection.cluster_wallet_behavior(
                arguments["addresses"],
            )
        else:
            result = {"error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.error(f"Tool {name} failed: {e}", exc_info=True)
        result = {"error": str(e)}

    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def main():
    logger.info("Starting MCP Base Chain server...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
