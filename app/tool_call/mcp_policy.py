"""Allowlisted MCP policy transport used by the product tool manager."""

import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from app.mcp_client import ExternalMcpToolClient, RemoteMcpToolClient


class PolicySection(BaseModel):
    source: str
    source_type: str
    section_title: str
    text: str
    score: float


class PolicyResult(BaseModel):
    sections: list[PolicySection] = Field(max_length=20)


def policy_handler_from_env():
    transport = os.environ.get("POLICY_TOOL_TRANSPORT", "local")
    if transport == "local":
        return None
    if transport == "mcp_stdio":
        client = ExternalMcpToolClient(
            sys.executable, ["-m", "app.mcp_server"], Path(__file__).resolve().parents[2]
        )
    elif transport == "mcp_http":
        url = os.environ["POLICY_MCP_URL"]
        if not url.startswith("https://"):
            raise ValueError("Remote policy MCP requires HTTPS")
        token = os.environ["POLICY_MCP_TOKEN"]
        client = RemoteMcpToolClient(url, {"Authorization": f"Bearer {token}"})
    else:
        raise ValueError(f"Unsupported policy transport: {transport}")

    async def call(args, context):
        # Endpoints and allowed tools are deployment configuration, never model output.
        async with client.session() as session:
            discovered = await session.list_tools()
            tool = next((tool for tool in discovered.tools if tool.name == "search_policy_knowledge"), None)
            if tool is None or "query" not in tool.input_schema.get("properties", {}):
                raise ValueError("Incompatible policy MCP tool schema")
            result = await session.call_tool(tool.name, {"query": args["query"], "k": args["k"]})
            if result.is_error:
                raise RuntimeError("Policy MCP returned tool error")
            return PolicyResult.model_validate(result.structured_content).model_dump()

    return call
