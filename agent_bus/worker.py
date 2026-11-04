"""Two small commands built on bus.py. Neither needs MCP.

agent-bus-watch NAME   print one line for every new unread message to NAME (never marks them read).
                       Pipe it to notify-send, or run it as a Claude Code background monitor.
agent-bus-worker NAME  take 'task' messages addressed to NAME, run each one through a headless
                       agent command (default: opencode-router run), and reply with the result.

Why a separate worker name (e.g. opencode-worker) and not "opencode"? The worker only takes tasks.
If it shared the name with an interactive OpenCode window, that window's inbox call could grab them.
"""

import argparse
import os
import re
import shlex
import subprocess
import time

from . import bus

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")  # opencode colours its output; strip it before storing
PREVIEW = 1500  # characters of output put in the reply; the rest goes in a note


def run_task(conn, name, msg, cmd, cwd, timeout):
    """Run one task message through `cmd`, then reply to its sender. Never raises on a task failure."""
    try:
        done = subprocess.run(cmd + [msg["body"]], capture_output=True, text=True, timeout=timeout, cwd=cwd)
        output = ANSI.sub("", done.stdout + done.stderr).strip() or "(no output)"
        status = "done" if done.returncode == 0 else f"failed (exit {done.returncode})"
    except (subprocess.TimeoutExpired, OSError) as err:
        output, status = f"({err})", "failed"
    key = f"result-{msg['id']}"
    bus.note_set(conn, key, output, name)
    tail = "" if len(output) <= PREVIEW else f"\n[...cut, full output in note '{key}']"
    bus.send(conn, name, msg["sender"], f"{status}:\n{output[:PREVIEW]}{tail}", reply_to=msg["id"])


def worker(argv=None):
    p = argparse.ArgumentParser(prog="agent-bus-worker", description=__doc__.split("\n\n")[0])
    p.add_argument("name", help="the bus name this worker listens on, e.g. opencode-worker")
    p.add_argument("--allow-from", action="append", default=None,
                   help="sender allowed to give tasks (repeatable; default: claude-main)")
    p.add_argument("--cmd", default="opencode-router run", help="headless agent command; the task text is appended")
    p.add_argument("--cwd", default=os.getcwd(), help="folder the agent runs in (default: current folder)")
    p.add_argument("--timeout", type=int, default=900, help="seconds before a task is abandoned")
    p.add_argument("--interval", type=float, default=3, help="seconds between checks")
    p.add_argument("--once", action="store_true", help="handle what is waiting, then exit")
    args = p.parse_args(argv)
    allowed = set(args.allow_from or ["claude-main"])
    cmd = shlex.split(args.cmd)

    conn = bus.connect()
    while True:
        bus.touch(conn, args.name, "worker")
        for msg in bus.inbox(conn, args.name, kinds=("task",)):
            # Anything that can write to the bus could send a task, and a task runs an agent with shell access.
            if msg["sender"] not in allowed:
                bus.send(conn, args.name, msg["sender"], "refused: sender not allowed", reply_to=msg["id"])
                continue
            # shortcut: the task is marked read before it runs, so a crash mid-task loses it; fine for v1.
            run_task(conn, args.name, msg, cmd, args.cwd, args.timeout)
        if args.once:
            return
        time.sleep(args.interval)


def watch(argv=None):
    p = argparse.ArgumentParser(prog="agent-bus-watch", description=__doc__.split("\n\n")[0])
    p.add_argument("name")
    p.add_argument("--interval", type=float, default=3)
    p.add_argument("--once", action="store_true", help="print what is unread now, then exit")
    args = p.parse_args(argv)
    conn = bus.connect()
    seen = set()
    while True:
        for m in bus.unread(conn, args.name):
            if m["id"] not in seen:
                seen.add(m["id"])
                print(f"[{m['kind']}] #{m['id']} from {m['sender']}: {m['body'][:100]!r}", flush=True)
        if args.once:
            return
        time.sleep(args.interval)
