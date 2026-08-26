from __future__ import annotations

import json
from typing import Any

import anyio
import mcp_types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server

from app.olist.service import OlistService, format_after_sales_report, format_order_status

olist_service = OlistService()


def get_order_status(order_id: str) -> str:
    """Return deterministic Olist order status, delivery, payment, and review facts."""
    return format_order_status(olist_service.get_order_status(order_id))


def search_category_risk(query: str) -> list[dict[str, object]]:
    """Retrieve category-level logistics and review risk insights from Olist records."""
    return olist_service.category_insights(query)


def draft_escalation(order_id: str) -> dict[str, object]:
    """Draft a support escalation for delayed, canceled, or low-review orders."""
    draft = olist_service.escalation_draft(order_id)
    if draft is None:
        return {"found": False, "reason": "order_not_found"}
    return {"found": True, **draft}


def generate_after_sales_priority_report(query: str = "") -> dict[str, object]:
    """Generate a read-only after-sales operations decision report."""
    return olist_service.after_sales_priority_report(query)


async def list_tools(
    ctx: ServerRequestContext[Any],
    params: types.PaginatedRequestParams | None,
) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="get_order_status",
                description="Return Olist order status, delivery, payment, and review facts.",
                input_schema=_object_schema({"order_id": "32-character Olist order id"}, ["order_id"]),
            ),
            types.Tool(
                name="search_category_risk",
                description="Retrieve category-level logistics and review risk insights.",
                input_schema=_object_schema({"query": "Category or natural-language risk query"}, ["query"]),
            ),
            types.Tool(
                name="draft_escalation",
                description="Draft a support escalation for delayed, canceled, or low-review orders.",
                input_schema=_object_schema({"order_id": "32-character Olist order id"}, ["order_id"]),
            ),
            types.Tool(
                name="generate_after_sales_priority_report",
                description="Generate high-risk category and priority after-sales order recommendations.",
                input_schema=_object_schema({"query": "Natural-language operations decision request"}, []),
            ),
        ]
    )


async def call_tool(
    ctx: ServerRequestContext[Any],
    params: types.CallToolRequestParams,
) -> types.CallToolResult:
    args = params.arguments or {}
    if params.name == "get_order_status":
        structured = {"answer": get_order_status(str(args.get("order_id", "")))}
    elif params.name == "search_category_risk":
        structured = {"insights": search_category_risk(str(args.get("query", "")))}
    elif params.name == "draft_escalation":
        structured = draft_escalation(str(args.get("order_id", "")))
    elif params.name == "generate_after_sales_priority_report":
        report = generate_after_sales_priority_report(str(args.get("query", "")))
        structured = {"report": report, "answer": format_after_sales_report(report)}
    else:
        return types.CallToolResult(
            content=[types.TextContent(text=f"Unknown tool: {params.name}")],
            is_error=True,
        )

    return types.CallToolResult(
        content=[types.TextContent(text=json.dumps(structured, ensure_ascii=False))],
        structured_content=structured,
    )


def create_server() -> Server:
    return Server(
        "olist-marketplace-support",
        version="0.2.0",
        description="MCP tools for the Olist marketplace support Agent.",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def _object_schema(properties: dict[str, str], required: list[str]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            name: {"type": "string", "description": description}
            for name, description in properties.items()
        },
        "required": required,
    }


async def run_server() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(run_server)
