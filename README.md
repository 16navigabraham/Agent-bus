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

## Author

Built by Adebanjo Abraham ([@AbrahamNAVIG1](https://x.com/AbrahamNAVIG1)).

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

**Reply status.** A task is sent with a line asking the agent to end with `TASK COMPLETE` or
`TASK INCOMPLETE: <what is left>`. The reply starts with `completed`, `incomplete`, `failed (exit N)`,
`exited without saying it was complete...` (the agent just stopped; check the output), or
`timed out after Ns and was stopped`. On timeout the agent's whole process group is killed, so no
child process keeps running with nobody to report to. The default `--timeout` is 3600 seconds.
The reply holds the agent's answer; its tool trace is in note `result-<id>`.

**Continuing sessions.** With `--keep-session`, each sender gets one OpenCode session that continues
across tasks (found again by the title `bus:<worker>:<sender>`), so a sender doesn't repeat background
it already gave. Start a task with `/new` to begin a fresh session. Without the flag every task is a
fresh session. `--cwd-for SENDER=PATH` runs a sender's tasks in its own project folder. The headless
agent runs under the bus name `<worker>-agent`, so it can't read an open tab's inbox.
