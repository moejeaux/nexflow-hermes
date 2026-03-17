"""
MCP Server — NexFlow API Integration

Entry point for the MCP server that exposes NexFlow tools (Pulse, SMF,
Agent Stats) to Hermes Agent. Communicates with NexFlow only via HTTP.

Run standalone: python -m src.server
"""

import asyncio
import json
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from src.tools import pulse, smf, agent_stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("mcp-nexflow")

server = Server("nexflow")

# ---- Tool Definitions ----

TOOLS = [
    # Pulse CAAS tools
    Tool(
        name="create_pulse_job",
        description=(
            "Create a recurring scheduled job in NexFlow Pulse (Cron-as-a-Service). "
            "The job will call the specified webhook URL on the given cron schedule."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "schedule": {
                    "type": "string",
                    "description": "Cron expression (e.g. '*/15 * * * *' for every 15 min)",
                },
                "webhook_url": {
                    "type": "string",
                    "description": "URL to call when the job fires",
                },
                "payload": {
                    "type": "object",
                    "description": "Optional JSON payload to send with the webhook",
                },
            },
            "required": ["schedule", "webhook_url"],
        },
    ),
    Tool(
        name="list_pulse_jobs",
        description="List all active scheduled jobs in NexFlow Pulse.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="delete_pulse_job",
        description="Remove a scheduled job from NexFlow Pulse.",
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The unique identifier of the job to delete",
                },
            },
            "required": ["job_id"],
        },
    ),
    Tool(
        name="get_job_history",
        description="Get execution history and status for a specific Pulse job.",
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The unique identifier of the job",
                },
            },
            "required": ["job_id"],
        },
    ),

    # SMF / x402 tools
    Tool(
        name="get_facilitator_quote",
        description="Get a payment routing quote from NexFlow SMF for a given amount and chain.",
        inputSchema={
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "description": "USD amount to route",
                },
                "chain": {
                    "type": "string",
                    "description": "Target blockchain (default 'base')",
                    "default": "base",
                },
            },
            "required": ["amount"],
        },
    ),
    Tool(
        name="verify_x402_payment",
        description="Verify that an x402 payment was received and settled on-chain.",
        inputSchema={
            "type": "object",
            "properties": {
                "payment_hash": {
                    "type": "string",
                    "description": "Transaction or payment hash to verify",
                },
            },
            "required": ["payment_hash"],
        },
    ),
    Tool(
        name="list_active_facilitators",
        description="List all available payment facilitators in the NexFlow network.",
        inputSchema={"type": "object", "properties": {}},
    ),

    # Agent stats tools
    Tool(
        name="report_agent_stats",
        description=(
            "Push performance metrics for an agent to NXFX01's tracking system. "
            "Used by child agents to report their work."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier (e.g. 'NXF011')",
                },
                "metrics": {
                    "type": "object",
                    "description": "Key-value pairs of metrics to report",
                },
            },
            "required": ["agent_id", "metrics"],
        },
    ),
    Tool(
        name="get_agent_stats",
        description="Pull current performance stats for any agent in the NexFlow ecosystem.",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier (e.g. 'NXF011', 'NXFX01')",
                },
            },
            "required": ["agent_id"],
        },
    ),
    Tool(
        name="log_revenue",
        description=(
            "Log a revenue event attributed to a specific agent. "
            "Tracks earnings by source for P&L analysis."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent that generated the revenue",
                },
                "amount": {
                    "type": "number",
                    "description": "USD amount earned",
                },
                "source": {
                    "type": "string",
                    "description": "Revenue source (e.g. 'upwork_contract', 'signal_sale')",
                },
                "job_id": {
                    "type": "string",
                    "description": "Optional job ID for traceability",
                },
            },
            "required": ["agent_id", "amount", "source"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route tool calls to the appropriate handler."""
    logger.info(f"Tool call: {name} with args: {json.dumps(arguments, default=str)[:200]}")

    try:
        # Pulse CAAS
        if name == "create_pulse_job":
            result = await pulse.create_pulse_job(
                arguments["schedule"],
                arguments["webhook_url"],
                arguments.get("payload"),
            )
        elif name == "list_pulse_jobs":
            result = await pulse.list_pulse_jobs()
        elif name == "delete_pulse_job":
            result = await pulse.delete_pulse_job(arguments["job_id"])
        elif name == "get_job_history":
            result = await pulse.get_job_history(arguments["job_id"])

        # SMF / x402
        elif name == "get_facilitator_quote":
            result = await smf.get_facilitator_quote(
                arguments["amount"],
                arguments.get("chain", "base"),
            )
        elif name == "verify_x402_payment":
            result = await smf.verify_x402_payment(arguments["payment_hash"])
        elif name == "list_active_facilitators":
            result = await smf.list_active_facilitators()

        # Agent stats
        elif name == "report_agent_stats":
            result = await agent_stats.report_agent_stats(
                arguments["agent_id"],
                arguments["metrics"],
            )
        elif name == "get_agent_stats":
            result = await agent_stats.get_agent_stats(arguments["agent_id"])
        elif name == "log_revenue":
            result = await agent_stats.log_revenue(
                arguments["agent_id"],
                arguments["amount"],
                arguments["source"],
                arguments.get("job_id"),
            )
        else:
            result = {"error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.error(f"Tool {name} failed: {e}", exc_info=True)
        result = {"error": str(e)}

    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def main():
    logger.info("Starting MCP NexFlow server...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
