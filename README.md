# agent-bus

A tiny local message bus that lets your AI coding agents talk to each other.

## The problem

You use more than one AI coding tool: Claude Code, OpenCode, Gemini CLI, and so on. Each one
lives in its own terminal and knows nothing about the others. So you copy and paste context
between them, you can't ask a second agent to check or do some of the work, and when one hits
its usage limit you start again in another.

## What it does

agent-bus gives every agent a name and an inbox. Agents can:

- **send** a message or a task to another agent by name,
- **read** their inbox when they are ready,
- **share notes**: longer context, summaries, results.

Typical uses: hand over a job when one tool runs out of quota; let a cheaper agent do the
grunt work (searching code, summarising logs, boilerplate, running tests) so the expensive one
keeps its quota for the hard parts; keep two agents on one project in step through a shared note.

It works with any tool that supports [MCP](https://modelcontextprotocol.io). Tested with Claude
Code and OpenCode.

## How it works

- Each tool starts its own copy of `agent-bus` (an MCP server over stdio).
- All copies read and write the same SQLite file, `~/.agent-bus/bus.db`
  (folder `0700`, file `0600`). That shared file is how the agents see each other.
- Each session picks its name with the `AGENT_BUS_NAME` environment variable.

**Delivery is pull, not push.** MCP is request/response, so a tool can't interrupt an idle
agent. A message arrives when the receiving agent calls `inbox`. Tell your agents to check it,
e.g. in `CLAUDE.md` or `AGENTS.md`: "At the start of each task, call the agent-bus `inbox` tool."
(See [Getting notified](#getting-notified) for a way to be told when mail arrives.)

## Tools

| Tool | What it does |
|------|--------------|
| `whoami` | Your name on the bus |
| `peers` | Agents seen recently, with unread counts |
| `send(to, body, kind, reply_to)` | Message another agent. `kind` is `message`, `task` or `context` |
| `inbox` | Read and clear your unread messages |
| `history` | Recent messages sent and received |
| `note_set(key, body)` / `note_get(key)` | Shared notes for longer text; `note_get` with no key lists them |

Messages to a name that isn't online wait in its inbox.

## Install

```bash
git clone https://github.com/16navigabraham/Agent-bus.git && cd Agent-bus
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Connect a tool

Use the full path to `.venv/bin/agent-bus` and a different `AGENT_BUS_NAME` for each tool.

**Claude Code**

```bash
claude mcp add agent-bus -s user -e AGENT_BUS_NAME=claude-main -e AGENT_BUS_HARNESS=claude-code -- /path/to/Agent-bus/.venv/bin/agent-bus
```

**OpenCode** (`~/.config/opencode/opencode.json`)

```json
{
  "mcp": {
    "agent-bus": {
      "type": "local",
      "command": ["/path/to/Agent-bus/.venv/bin/agent-bus"],
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
      "command": "/path/to/Agent-bus/.venv/bin/agent-bus",
      "env": { "AGENT_BUS_NAME": "gemini", "AGENT_BUS_HARNESS": "gemini-cli" }
    }
  }
}
```

One name per project works well, e.g. `claude-web` and `opencode-web` for a web project, so
handoffs reach the right session.

## Delegating work to another agent

`agent-bus-worker NAME` listens for `task` messages addressed to NAME, runs each one through a
headless agent command, and replies with the result. Any CLI that takes the task text as its
last argument works; the default is `opencode run`.

```bash
# Terminal 1: a worker that runs tasks with OpenCode, inside your project folder
agent-bus-worker my-worker --cwd /path/to/project

# Any connected agent (here, through its MCP tool):
#   send(to="my-worker", kind="task", body="List the TODO comments in src/ and summarise them")
# ...and later read the reply with: inbox
```

Use a name that no interactive window uses. A worker only takes tasks, but if it shared a name
with an open agent, that agent's `inbox` call could grab them first.

| Option | Meaning |
|--------|---------|
| `--allow-from NAME` | Who may give tasks (repeatable, default `claude-main`) |
| `--cmd "..."` | The headless agent command; the task text is appended |
| `--cwd PATH` | Folder the agent runs in |
| `--cwd-for SENDER=PATH` | Run tasks from SENDER in its own folder (repeatable) |
| `--keep-session` | One continuing OpenCode session per sender (see below) |
| `--timeout N` | Seconds before a task is stopped (default 3600) |
| `--once` | Handle what is waiting, then exit |

**Reply status.** Each task asks the agent to end with `TASK COMPLETE` or
`TASK INCOMPLETE: <what is left>`. A clean exit alone doesn't prove the job is finished (an agent
can stop after writing a plan), so the status comes from that line. The reply starts with
`completed`, `incomplete`, `failed (exit N)`, `exited without saying it was complete...` (check the
output), or `timed out after Ns and was stopped`. On timeout the agent's whole process group is
killed, so no child process keeps running with nobody to report to. The reply holds the agent's
answer; its tool trace is in note `result-<task id>`.

**Continuing sessions.** With `--keep-session`, each sender gets one OpenCode session that
continues across tasks (found again by the title `bus:<worker>:<sender>`), so a sender doesn't
repeat background it already gave. Start a task with `/new` to begin a fresh session. To adopt an
existing session, set the note `session:<worker>:<sender>` to its id. This flag relies on
OpenCode's `-s` and `--title` options. Without it, every task is a fresh session.

The headless agent runs under the bus name `<worker>-agent`, so it can't read the inbox of an
open tab.

## Getting notified

`agent-bus-watch NAME` prints one line whenever new mail arrives for NAME, without marking it read:

```bash
agent-bus-watch claude-main | while read -r line; do notify-send "agent-bus" "$line"; done
```

Some tools can run it as a background monitor and wake the agent when a line appears.

## Security and limitations

- **No authentication or encryption.** It trusts every process running as your user. Don't put
  secrets in messages or notes.
- **A task runs an agent with shell access**, so only senders in `--allow-from` may give tasks.
  Anything that can write to the database could still claim to be one of them.
- **Task content goes to your agent's model provider.** Don't delegate work that includes secrets.
- A task is marked read before it runs, so a worker crash mid-task loses it.
- Two sessions launched with the same `AGENT_BUS_NAME` share one inbox.
- Delivery is pull only, there is no broadcast, and messages are never deleted automatically.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v   # the store and the worker
.venv/bin/python tests/smoke_mcp.py                 # two real MCP server processes
```

## License

MIT, see `LICENSE`.

## Author

Built by Adebanjo Abraham ([@AbrahamNAVIG1](https://x.com/AbrahamNAVIG1)).
