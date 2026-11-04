"""End-to-end check over real MCP: two server processes, one shared database.

Run: .venv/bin/python tests/smoke_mcp.py
Starts the server twice as "alice" and "bob", has alice message bob, and checks bob receives it.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def params(name, db):
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_bus.server"],
        env={"AGENT_BUS_NAME": name, "AGENT_BUS_DB": db},
    )


async def call(name, db, tool, args=None):
    async with stdio_client(params(name, db)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args or {})
            assert not result.is_error, result.content
            return result.structured_content["result"]


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "bus.db")
        assert await call("alice", db, "whoami") == "alice"
        await call("alice", db, "send", {"to": "bob", "body": "hello bob", "kind": "task"})
        got = await call("bob", db, "inbox")
        bodies = [m["body"] for m in got]
        assert bodies == ["hello bob"], got
        assert await call("bob", db, "inbox") == []
        names = {p["name"] for p in await call("bob", db, "peers")}
        assert {"alice", "bob"} <= names, names
    print("smoke test passed")


asyncio.run(main())
