"""
MCP Server — NXFX01 Launch Intelligence

Entry point for the MCP server that exposes NXFX01 launch analysis
tools to Hermes Agent. Communicates with nxfx01-api via HTTP.

Run standalone: python -m src.server
"""

import asyncio
import json
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from src.client import Nxfx01Client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("mcp-nxfx01")

server = Server("nxfx01")

# ---- Tool Definitions ----

TOOLS = [
    Tool(
        name="get_recent_launches",
        description=(
            "Get recent Base token launches sorted by detection time (newest first). "
            "Returns lightweight summaries with mode, safety score, and shadow flag."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max number of launches to return (default 20, max 100)",
                    "default": 20,
                },
                "min_overall_safety_initial": {
                    "type": "integer",
                    "description": "Minimum safety score filter (0-100)",
                },
            },
        },
    ),
    Tool(
        name="get_actionable_launches",
        description=(
            "Get tradable Base token launches filtered by action mode and minimum safety. "
            "Sorted by safety score (descending), then smart money participation, then recency. "
            "In shadow mode, launches are returned but flagged shadow=True."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "Action mode filter: FAST, WAIT, or BLOCK (default FAST)",
                    "default": "FAST",
                    "enum": ["FAST", "WAIT", "BLOCK"],
                },
                "min_safety": {
                    "type": "integer",
                    "description": "Minimum overall safety score (default 60)",
                    "default": 60,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10, max 50)",
                    "default": 10,
                },
            },
        },
    ),
    Tool(
        name="get_launch_details",
        description=(
            "Get the full launch view for a specific token launch, including all scores, "
            "red flag notes, wallet distribution, notable participants, and latency timestamps."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "launch_id": {
                    "type": "string",
                    "description": "UUID of the launch to retrieve",
                },
            },
            "required": ["launch_id"],
        },
    ),
    Tool(
        name="get_wallet_profile",
        description=(
            "Get the tier, value score, performance score, cluster membership, "
            "and alpha cohort flag for a wallet address on Base."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "wallet_address": {
                    "type": "string",
                    "description": "Ethereum address of the wallet",
                },
            },
            "required": ["wallet_address"],
        },
    ),
    Tool(
        name="get_past_launch_outcomes",
        description=(
            "Get historical launch outcomes with PnL, drawdown, rug status, and the scores "
            "that were assigned at launch time. Used for self-learning and policy refinement."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "since_days": {
                    "type": "integer",
                    "description": "Look back this many days (default 7, max 90)",
                    "default": 7,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 50, max 200)",
                    "default": 50,
                },
            },
        },
    ),
    Tool(
        name="update_launch_policy_suggestion",
        description=(
            "Propose a scoring policy adjustment based on observed patterns. "
            "Requires a patch (JSON of changes), rationale (why), and optional evidence_snapshot. "
            "All suggestions require human approval before taking effect."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "policy_patch": {
                    "type": "object",
                    "description": "JSON patch of proposed changes to scoring policy",
                },
                "rationale": {
                    "type": "string",
                    "description": "Why this change is recommended, referencing observed data",
                },
                "evidence_snapshot": {
                    "type": "object",
                    "description": "Optional supporting data (outcome stats, score distributions, etc.)",
                },
            },
            "required": ["policy_patch", "rationale"],
        },
    ),
    Tool(
        name="get_pending_alerts",
        description=(
            "Get pending alerts from the NXFX01 scheduler. Alerts are created when: "
            "BUY_TRIGGER — a launch scored >= 85 (initial or final); "
            "EVALUATE — a launch needs further analysis; "
            "UPGRADE/DOWNGRADE — mode changed after behavior scoring; "
            "RUG_WARNING — a previously FAST launch was rugged. "
            "Always acknowledge alerts after processing them."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max alerts to return (default 20)",
                    "default": 20,
                },
            },
        },
    ),
    Tool(
        name="acknowledge_alert",
        description=(
            "Acknowledge an alert after processing it, removing it from the pending queue. "
            "Call this after acting on or reviewing each alert."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "alert_id": {
                    "type": "string",
                    "description": "UUID of the alert to acknowledge",
                },
            },
            "required": ["alert_id"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route tool calls to the NXFX01 API."""
    logger.info(f"Tool call: {name} with args: {json.dumps(arguments, default=str)[:200]}")

    client = Nxfx01Client()

    try:
        if name == "get_recent_launches":
            params = {"limit": arguments.get("limit", 20)}
            if "min_overall_safety_initial" in arguments:
                params["min_safety"] = arguments["min_overall_safety_initial"]
            result = await client.get("/launches/recent", params=params)

        elif name == "get_actionable_launches":
            result = await client.get("/launches/actionable", params={
                "mode": arguments.get("mode", "FAST"),
                "min_safety": arguments.get("min_safety", 60),
                "limit": arguments.get("limit", 10),
            })

        elif name == "get_launch_details":
            result = await client.get(f"/launches/{arguments['launch_id']}")

        elif name == "get_wallet_profile":
            result = await client.get(f"/wallets/{arguments['wallet_address']}")

        elif name == "get_past_launch_outcomes":
            result = await client.get("/launches/outcomes", params={
                "since_days": arguments.get("since_days", 7),
                "limit": arguments.get("limit", 50),
            })

        elif name == "update_launch_policy_suggestion":
            result = await client.post("/policy/suggest", json_body={
                "patch": arguments["policy_patch"],
                "rationale": arguments["rationale"],
                "evidence_snapshot": arguments.get("evidence_snapshot"),
            })

        elif name == "get_pending_alerts":
            result = await client.get("/alerts/pending", params={
                "limit": arguments.get("limit", 20),
            })

        elif name == "acknowledge_alert":
            result = await client.post(f"/alerts/{arguments['alert_id']}/acknowledge")

        else:
            result = {"error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.error(f"Tool {name} failed: {e}", exc_info=True)
        result = {"error": str(e)}

    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def main():
    logger.info("Starting MCP NXFX01 server...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
