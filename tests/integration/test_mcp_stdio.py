"""End-to-end stdio test for the MCP server.

Launches `clerk mcp serve` as a subprocess and drives it through the official
MCP Python client over stdio. Verifies:
  - The server initializes cleanly
  - All expected tools are advertised
  - A simple tool call (accounts_list) succeeds and returns JSON-serialisable data
  - Missing-token startup is enforced (subprocess exits non-zero)

Gated by CLERK_INTEGRATION=1 since the running server reads the user's real
config dir and real Keychain. A throwaway capability token is created for the
test and revoked at the end.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from clerk import tokens
from clerk.config import load_config

pytestmark = pytest.mark.integration

CLERK_BIN = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "clerk"


@pytest.fixture(autouse=True)
def _gate():
    if os.environ.get("CLERK_INTEGRATION") != "1":
        pytest.skip("set CLERK_INTEGRATION=1 to run integration tests")
    if not CLERK_BIN.exists():
        pytest.skip(f"clerk binary not found at {CLERK_BIN}")


# ---------- helpers ----------


def _make_token() -> tuple[str, str]:
    """Create a temporary read-only token, return (raw, id)."""
    created = tokens.create(
        ["mail:read", "calendar:read"], note="mcp-stdio-integration-test"
    )
    return created.raw, created.id


async def _drive_server(token: str) -> dict:
    """Connect, initialise, list tools, call accounts_list, return diagnostics."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = {**os.environ, "CLERK_TOKEN": token}
    params = StdioServerParameters(
        command=str(CLERK_BIN),
        args=["mcp", "serve"],
        env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_resp = await session.list_tools()
            tool_names = sorted(t.name for t in tools_resp.tools)

            call_resp = await session.call_tool("accounts_list", arguments={})
            # call_tool returns CallToolResult with .content (list of TextContent etc).
            # Our tool returns a list[dict] which MCP serialises as JSON text.
            text_blocks = [
                c.text for c in call_resp.content if getattr(c, "type", None) == "text"
            ]
            structured = (
                getattr(call_resp, "structuredContent", None)
                or getattr(call_resp, "structured_content", None)
            )

    return {
        "tool_names": tool_names,
        "accounts_text": text_blocks,
        "accounts_structured": structured,
    }


# ---------- tests ----------


def test_stdio_session_lists_tools_and_calls_accounts_list():
    raw, tid = _make_token()
    try:
        result = asyncio.run(_drive_server(raw))
    finally:
        tokens.revoke(tid)

    expected_subset = {
        "accounts_list",
        "mail_search",
        "mail_read",
        "mail_send",
        "mail_reply",
        "mail_delete",
        "mail_get_attachment",
        "calendar_list",
        "calendar_get",
        "calendar_create",
        "calendar_update",
        "calendar_cancel",
    }
    actual = set(result["tool_names"])
    missing = expected_subset - actual
    assert not missing, f"missing expected tools: {missing}"

    # accounts_list returns list[dict]; MCP transports either as text or as
    # structuredContent. At least one of them must be present and decode
    # to a list with the same number of accounts the config has.
    real_account_count = len([1 for _ in __import__("clerk.config", fromlist=["load_accounts"]).load_accounts().accounts])

    payload_list = None
    if result["accounts_structured"]:
        # structuredContent is the raw return value (dict/list as-is).
        payload_list = result["accounts_structured"]
        # FastMCP wraps list returns inside a structured payload — unwrap if needed.
        if isinstance(payload_list, dict) and "result" in payload_list:
            payload_list = payload_list["result"]
    elif result["accounts_text"]:
        # Some MCP transports JSON-encode the return value into a text block.
        for block in result["accounts_text"]:
            try:
                decoded = json.loads(block)
                if isinstance(decoded, list):
                    payload_list = decoded
                    break
            except json.JSONDecodeError:
                continue

    assert payload_list is not None, (
        f"could not parse accounts_list response: text={result['accounts_text']!r} "
        f"structured={result['accounts_structured']!r}"
    )
    assert len(payload_list) == real_account_count
    for entry in payload_list:
        assert "email" in entry
        assert "provider" in entry


def test_stdio_server_refuses_to_start_without_token_when_required():
    """If security.require_token=true and CLERK_TOKEN is not set, the server
    should exit non-zero with a clear stderr message — NOT silently accept
    the connection."""
    cfg = load_config()
    if not cfg.security.require_token:
        pytest.skip("config currently has require_token=false; test inapplicable")

    env = {**os.environ}
    env.pop("CLERK_TOKEN", None)
    result = subprocess.run(
        [str(CLERK_BIN), "mcp", "serve"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode != 0
    assert "CLERK_TOKEN" in result.stderr
