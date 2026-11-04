# agent-bus

A small local message bus that lets AI coding agents on one machine talk to each
other, whichever tool they run in: Claude Code, OpenCode, Gemini CLI, anything that
supports [MCP](https://modelcontextprotocol.io).

One agent can hand a task to another, ask for context, or leave a note for whoever
picks the work up next, for example when one tool runs out of quota and you continue
in another.

## How it works

- Each tool starts its own copy of `agent-bus` (an MCP server over stdio).
- All copies read and write the same SQLite file, `~/.agent-bus/bus.db`
  (folder `0700`, file `0600`). That shared file is how the agents see each other.
- Each session picks its name with the `AGENT_BUS_NAME` environment variable.

**Delivery is pull, not push.** MCP is request/response, so a tool can't interrupt an
idle agent. A message arrives when the receiving agent calls `inbox`. Tell your agents
to check it, e.g. add to `CLAUDE.md` / `AGENTS.md`:

> At the start of each task, call the agent-bus `inbox` tool.

## Tools

| Tool | What it does |
|------|--------------|
| `whoami` | Your name on the bus |
| `peers` | Agents seen recently, with unread counts |
| `send(to, body, kind, reply_to)` | Message another agent. `kind` is `message`, `task` or `context` |
| `inbox` | Read and clear your unread messages |
| `history` | Recent messages sent and received |
| `note_set(key, body)` / `note_get(key)` | Shared notes for longer context; `note_get` with no key lists them |

Messages to a name that isn't online wait in its inbox.

## Install

```bash
git clone <this repo> && cd agent-bus
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Connect a tool

Use the full path to `.venv/bin/agent-bus` and a different `AGENT_BUS_NAME` per tool.

**Claude Code**

```bash
claude mcp add agent-bus -s user -e AGENT_BUS_NAME=claude-main -e AGENT_BUS_HARNESS=claude-code -- /path/to/agent-bus/.venv/bin/agent-bus
```

**OpenCode** (`~/.config/opencode/opencode.json`)

```json
{
  "mcp": {
    "agent-bus": {
      "type": "local",
      "command": ["/path/to/agent-bus/.venv/bin/agent-bus"],
      "environment": { "AGENT_BUS_NAME": "opencode", "AGENT_BUS_HARNESS": "opencode" }
    }
  }
}
```

**Gemini CLI** (`~/.gemini/settings.json`), untested

```json
{
  "mcpServers": {
    "agent-bus": {
      "command": "/path/to/agent-bus/.venv/bin/agent-bus",
      "env": { "AGENT_BUS_NAME": "gemini", "AGENT_BUS_HARNESS": "gemini-cli" }
    }
  }
}
```

## Limitations

- Two sessions of the same tool launched with the same `AGENT_BUS_NAME` share one
  inbox. Give each its own name (`AGENT_BUS_NAME=claude-2 claude`).
- No broadcast, no authentication, no encryption. It trusts every process running as
  your user. Don't put secrets in messages or notes.
- Messages are never deleted automatically.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v   # the store
.venv/bin/python tests/smoke_mcp.py                 # two real MCP server processes
```

## License

MIT, see `LICENSE`.

## Delegating work: worker and watcher

`agent-bus-worker NAME` takes `task` messages addressed to NAME, runs each through a headless
agent (default `opencode-router run`) and replies with the result (long output goes in a note
called `result-<id>`). Use a name no interactive window uses, e.g. `opencode-worker`.

```bash
agent-bus-worker opencode-worker --cwd ~/code/my-project      # leave running in a spare terminal
# in Claude Code:  send(to="opencode-worker", kind="task", body="...")  then later: inbox
agent-bus-watch claude-main                                   # prints a line when mail arrives
```

Only senders in `--allow-from` (default `claude-main`) may give tasks, because a task runs an
agent with shell access. A task is marked read before it runs, so a crash mid-task loses it.
