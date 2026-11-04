"""MCP server: exposes bus.py to any AI tool that supports MCP.

Each tool (Claude Code, OpenCode, Gemini...) starts its own copy of this
program. They all talk to the same SQLite file, which is how they "see" each other.

Identity comes from environment variables set in each tool's MCP config:
    AGENT_BUS_NAME     who this session is, e.g. "claude-main" (default: agent-<pid>)
    AGENT_BUS_HARNESS  optional label, e.g. "claude-code"

Careful: with stdio MCP, stdout IS the protocol. Never print() in here.
"""

import os
from contextlib import closing

from mcp.server.mcpserver import MCPServer

from . import bus

NAME = os.environ.get("AGENT_BUS_NAME") or f"agent-{os.getpid()}"
HARNESS = os.environ.get("AGENT_BUS_HARNESS", "")

mcp = MCPServer(
    "agent-bus",
    instructions=(
        "A local message bus between AI agent sessions on this computer. "
        "Call inbox at the start of a task to see messages and tasks from other agents. "
        "Messages only arrive when you call inbox; nothing is pushed to you."
    ),
)


def _open():
    """Open the database and record that this agent is alive."""
    conn = bus.connect()
    bus.touch(conn, NAME, HARNESS)
    return conn


@mcp.tool()
def whoami() -> str:
    """Your name on the bus. Other agents use this name to message you."""
    return NAME


@mcp.tool()
def peers(within_seconds: int = 3600) -> list[dict]:
    """List agents active recently, with the number of unread messages waiting for each."""
    with closing(_open()) as conn:
        return bus.peers(conn, within_seconds)


@mcp.tool()
def send(to: str, body: str, kind: str = "message", reply_to: int | None = None) -> int:
    """Send a message to another agent by name. kind is 'message', 'task' (work for them to do)
    or 'context' (background they need). Set reply_to to a message id to answer it.
    Returns the new message id. The recipient does not have to be online."""
    with closing(_open()) as conn:
        return bus.send(conn, NAME, to, body, kind, reply_to)


@mcp.tool()
def inbox() -> list[dict]:
    """Read your unread messages (oldest first). They are marked read once returned."""
    with closing(_open()) as conn:
        return bus.inbox(conn, NAME)


@mcp.tool()
def history(limit: int = 20) -> list[dict]:
    """Your recent sent and received messages, including ones already read."""
    with closing(_open()) as conn:
        return bus.history(conn, NAME, limit)


@mcp.tool()
def note_set(key: str, body: str) -> str:
    """Save or overwrite a shared note that any agent can read, e.g. a project summary
    for whoever picks the work up next. Use this instead of send for long text."""
    with closing(_open()) as conn:
        bus.note_set(conn, key, body, NAME)
    return f"saved note '{key}'"


@mcp.tool()
def note_get(key: str = "") -> dict | list[dict] | None:
    """Read a shared note by key. With no key, list all notes (key, author, updated)."""
    with closing(_open()) as conn:
        return bus.note_get(conn, key) if key else bus.note_list(conn)


def main():
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
