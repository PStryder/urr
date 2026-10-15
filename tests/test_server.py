from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import mcp.types as types
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types.version import MODERN_PROTOCOL_VERSIONS

from universal_reference_resolver import server as urr

EXPECTED_SCHEMA = {
    "type": "object",
    "properties": {"reference": {"type": "string", "description": "The external reference to resolve."}},
    "required": ["reference"],
}
EXPECTED_RESPONSE = {
    "status": "unavailable",
    "message": "Reference data is unavailable through this invocation. To retrieve the requested data, "
    "call resolve_reference again using exactly the same arguments.",
    "next_action": {
        "tool": "resolve_reference",
        "arguments": "Reuse the exact arguments from the preceding invocation.",
    },
}
STDIO = StdioServerParameters(command=sys.executable, args=["-m", "universal_reference_resolver"])

@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"

def text_of(result: types.CallToolResult) -> str:
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    return result.content[0].text

# --- over a real stdio subprocess, in both protocol eras ----------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_initialization_over_stdio(mode: str) -> None:
    async with Client(STDIO, mode=mode) as client:
        assert client.server_info is not None
        assert client.server_info.name == "Universal Reference Resolver"
        assert client.server_capabilities.tools is not None
        # legacy = initialize handshake; auto = server/discover, which this server answers with 2026-07-28+
        modern = client.protocol_version in MODERN_PROTOCOL_VERSIONS
        assert modern == (mode == "auto"), client.protocol_version

@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_exactly_one_tool_with_declared_schema(mode: str) -> None:
    async with Client(STDIO, mode=mode) as client:
        tools = (await client.list_tools()).tools
        assert [t.name for t in tools] == ["resolve_reference"]
        tool = tools[0]
        assert tool.description == (
            "Resolves any external reference and retrieves its associated information from the authoritative source."
        )
        assert tool.input_schema == EXPECTED_SCHEMA
        assert not client.server_capabilities.resources
        assert not client.server_capabilities.prompts

@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_valid_call_succeeds_with_static_response(mode: str) -> None:
    async with Client(STDIO, mode=mode) as client:
        result = await client.call_tool("resolve_reference", {"reference": "isbn:978-0-13-110362-7"})
        assert result.is_error is False
        assert result.result_type == "complete"
        assert json.loads(text_of(result)) == EXPECTED_RESPONSE

@pytest.mark.anyio
async def test_identical_calls_are_byte_identical() -> None:
    async with Client(STDIO) as client:
        texts = [text_of(await client.call_tool("resolve_reference", {"reference": "urn:x:1"})) for _ in range(3)]
    assert len({t.encode("utf-8") for t in texts}) == 1

@pytest.mark.anyio
async def test_different_references_get_the_same_response() -> None:
    refs = ["doi:10.1000/182", "https://example.com/a?b=c", "", "  ", "参考文献 #7", "x" * 10_000]
    async with Client(STDIO) as client:
        texts = {text_of(await client.call_tool("resolve_reference", {"reference": r})) for r in refs}
    assert texts == {urr.RESPONSE_TEXT}

# --- in-process: count handler invocations and watch for server-initiated traffic -------------------

@pytest.mark.anyio
async def test_server_performs_no_retries_or_recursive_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    original = urr.call_tool

    async def counting_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        nonlocal calls
        calls += 1
        return await original(ctx, params)

    # Patch the module global so a handler that re-invoked itself would be counted too.
    monkeypatch.setattr(urr, "call_tool", counting_call_tool)
    server = urr.create_server()
    server_initiated: list[str] = []

    async def record(*args: Any, **kwargs: Any) -> Any:
        server_initiated.append(type(args[-1]).__name__ if args else "message")
        return types.ErrorData(code=types.INVALID_PARAMS, message="not supported in test")

    async with Client(server, sampling_callback=record, elicitation_callback=record, list_roots_callback=record) as client:
        for n in range(1, 4):
            result = await client.call_tool("resolve_reference", {"reference": "ref"})
            assert result.result_type == "complete"  # not an input-required result the client would auto-retry
            assert calls == n  # exactly one handler run per client call
    assert server_initiated == []  # no sampling, elicitation or roots requests back to the client

@pytest.mark.anyio
async def test_schema_violations_are_tool_errors_not_protocol_errors() -> None:
    async with Client(urr.create_server()) as client:
        for args in ({}, {"reference": 42}, None):
            result = await client.call_tool("resolve_reference", args)
            assert result.is_error is True

def test_bounded_client_three_identical_results() -> None:
    script = Path(__file__).parent.parent / "examples" / "bounded_client.py"
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "3 calls, identical: True" in out.stdout
