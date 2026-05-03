"""Probe Inderes MCP to see what fields are in tool responses.

Helps us figure out whether responses include slug/URL fields we can
use to build links, or whether we need to slugify titles ourselves.

Run:
    uv run python scripts/probe_mcp_response.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from inderes_agent.mcp.oauth import get_inderes_access_token  # noqa: E402

MCP_URL = "https://mcp.inderes.com"
CLIENT_ID = "inderes-mcp"


async def call_mcp_tool(name: str, arguments: dict, token: str) -> dict:
    """Call an MCP tool over streamable_http transport, return parsed JSON result."""
    # MCP spec: initialize → call_tool, but for one-off probes we can shortcut
    # by talking directly to the tools/call endpoint. The streamable_http
    # transport multiplexes everything over POSTs to the base URL.
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-11-25",
    }
    # First initialize
    async with httpx.AsyncClient(timeout=30) as client:
        init_resp = await client.post(
            MCP_URL,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "probe-script", "version": "0.1"},
                },
            },
        )
        # Capture session ID from response headers
        session_id = init_resp.headers.get("mcp-session-id")
        if session_id:
            headers["mcp-session-id"] = session_id

        # Send initialized notification
        await client.post(
            MCP_URL,
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        # Call the tool
        call_resp = await client.post(
            MCP_URL,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        # Response is SSE-formatted text
        text = call_resp.text
        # Find data: line(s) and parse JSON
        for line in text.splitlines():
            if line.startswith("data:"):
                data = json.loads(line[len("data:") :].strip())
                return data
        return {"raw_text": text}


async def main() -> None:
    token = get_inderes_access_token(resource_url=MCP_URL, client_id=CLIENT_ID)
    print(f"Got token (prefix: {token[:24]}…)\n")

    print("=" * 70)
    print("PROBE 1: search-companies(query='Sampo')")
    print("=" * 70)
    r = await call_mcp_tool("search-companies", {"query": "Sampo"}, token)
    print(json.dumps(r, indent=2, ensure_ascii=False)[:2000])

    print("\n" + "=" * 70)
    print("PROBE 2: list-content(companyId=..., first=2)")
    print("=" * 70)
    # Try Sammon ID — may or may not be 258 from earlier convo
    r = await call_mcp_tool(
        "list-content",
        {"companyId": "COMPANY:258", "first": 2},
        token,
    )
    print(json.dumps(r, indent=2, ensure_ascii=False)[:3000])

    print("\n" + "=" * 70)
    print("PROBE 3: list-company-documents(companyId=..., first=2)")
    print("=" * 70)
    r = await call_mcp_tool(
        "list-company-documents",
        {"companyId": "COMPANY:258", "first": 2},
        token,
    )
    print(json.dumps(r, indent=2, ensure_ascii=False)[:3000])


if __name__ == "__main__":
    asyncio.run(main())
