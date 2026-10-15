"""Bounded test client: call resolve_reference exactly three times with identical arguments.

Launches the server over stdio, makes three calls, and checks that all three results are identical.
The call count is fixed; this client never follows the tool's instruction beyond that.

Usage: python examples/bounded_client.py [reference]
"""

from __future__ import annotations

import sys

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters

CALLS = 3


async def run(reference: str) -> list[str]:
    params = StdioServerParameters(command=sys.executable, args=["-m", "universal_reference_resolver"])
    texts = []
    async with Client(params) as client:
        for _ in range(CALLS):
            result = await client.call_tool("resolve_reference", {"reference": reference})
            assert not result.is_error
            texts.append(result.content[0].text)
    return texts


def main() -> int:
    reference = sys.argv[1] if len(sys.argv) > 1 else "doi:10.1000/182"
    texts = anyio.run(run, reference)
    identical = all(t == texts[0] for t in texts)
    print(f"{len(texts)} calls, identical: {identical}")
    print(texts[0])
    return 0 if identical else 1


if __name__ == "__main__":
    sys.exit(main())
