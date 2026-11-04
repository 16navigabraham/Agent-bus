"""Two small commands built on bus.py. Neither needs MCP.

agent-bus-watch NAME   print one line for every new unread message to NAME (never marks them read).
                       Pipe it to notify-send, or run it as a Claude Code background monitor.
agent-bus-worker NAME  take 'task' messages addressed to NAME, run each one through a headless
                       agent command (default: opencode-router run), and reply with the result.

Why a separate worker name (e.g. opencode-worker) and not "opencode"? The worker only takes tasks.
If it shared the name with an interactive OpenCode window, that window's inbox call could grab them.
"""

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import time

from . import bus

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")  # opencode colours its output; strip it before storing
PREVIEW = 1500  # characters of output put in the reply; the rest goes in a note
# An agent exiting normally does NOT mean the job is finished (it may just have stopped after a plan),
# so every task asks the agent to say explicitly which it is, and the status is taken from that line.
MARKER = ("\n\n[Automated task from the bus. End your final message with exactly one line: "
          "TASK COMPLETE, or TASK INCOMPLETE: <what is left>.]")


def _status(returncode, answer):
    if returncode != 0:
        return f"failed (exit {returncode})"
    last = next((ln.strip() for ln in reversed(answer.splitlines()) if ln.strip()), "")
    if last.startswith("TASK COMPLETE"):
        return "completed"
    if last.startswith("TASK INCOMPLETE"):
        return "incomplete"
    return "exited without saying it was complete, so it may not be finished"


def find_session(title):
    """Id of the newest OpenCode session with exactly this title, or None (also None if opencode fails)."""
    try:
        done = subprocess.run(["opencode", "session", "list", "--format", "json"],
                              capture_output=True, text=True, timeout=30)
        found = [s for s in json.loads(done.stdout) if s.get("title") == title]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return max(found, key=lambda s: s.get("updated", 0))["id"] if found else None


def _run(cmd, cwd, timeout, env):
    """Run cmd and return (exit code or None, stdout, stderr, timed_out).

    The agent starts child processes of its own (shells, test scripts). Killing only the direct child
    would leave them running with nobody to report to, so it gets its own process group and the whole
    group is killed on timeout.
    """
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err, False
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
        return None, out or "", err or "", True


def run_task(conn, name, msg, cmd, cwd, timeout, keep_session=False):
    """Run one task message through `cmd`, then reply to its sender. Never raises on a task failure.

    keep_session: each sender gets ONE continuing OpenCode session (found again by its title), so it
    never has to repeat background. A task starting with "/new" begins a fresh session instead.
    """
    body, extra = msg["body"], []
    if keep_session:
        title = f"bus:{name}:{msg['sender']}"
        link_key = f"session:{name}:{msg['sender']}"  # note holding an OpenCode session id, set to adopt an existing session
        fresh = body.startswith("/new")
        if fresh:
            body = body[4:].lstrip()
            bus.note_delete(conn, link_key)  # a fresh start must not be pulled back into the linked session
        link = bus.note_get(conn, link_key)
        session = None if fresh else (link["body"].strip() if link else find_session(title))
        extra = ["-s", session] if session else ["--title", title]
    # The headless agent gets its own bus name. Otherwise it would register under the name of the folder
    # it runs in (e.g. opencode-asap) and could read, and so steal, the inbox of your open OpenCode tab.
    env = {**os.environ, "AGENT_BUS_NAME": f"{name}-agent"}
    try:
        code, out, err, timed_out = _run(cmd + extra + [body + MARKER], cwd, timeout, env)
    except OSError as problem:
        code, out, err, timed_out = 127, "", str(problem), False
    answer, trace = ANSI.sub("", out).strip(), ANSI.sub("", err).strip()
    status = f"timed out after {timeout}s and was stopped" if timed_out else _status(code, answer)
    answer = answer or "(no output)"
    key = f"result-{msg['id']}"
    # The reply carries the agent's answer only; its tool trace (stderr) is kept in the note.
    bus.note_set(conn, key, answer + (f"\n\n--- trace ---\n{trace}" if trace else ""), name)
    tail = "" if len(answer) <= PREVIEW else f"\n[...cut, full output in note '{key}']"
    bus.send(conn, name, msg["sender"], f"{status}:\n{answer[:PREVIEW]}{tail}", reply_to=msg["id"])


def worker(argv=None):
    p = argparse.ArgumentParser(prog="agent-bus-worker", description=__doc__.split("\n\n")[0])
    p.add_argument("name", help="the bus name this worker listens on, e.g. opencode-worker")
    p.add_argument("--allow-from", action="append", default=None,
                   help="sender allowed to give tasks (repeatable; default: claude-main)")
    p.add_argument("--cmd", default="opencode-router run", help="headless agent command; the task text is appended")
    p.add_argument("--cwd", default=os.getcwd(), help="folder the agent runs in (default: current folder)")
    p.add_argument("--cwd-for", action="append", default=[], metavar="SENDER=PATH",
                   help="folder to run in for tasks from SENDER, e.g. claude-asap=/home/me/code/ASAP (repeatable)")
    p.add_argument("--keep-session", action="store_true",
                   help="one continuing OpenCode session per sender (needs an opencode-style CLI)")
    p.add_argument("--timeout", type=int, default=3600,
                   help="seconds before a task is stopped (its whole process group is killed)")
    p.add_argument("--interval", type=float, default=3, help="seconds between checks")
    p.add_argument("--once", action="store_true", help="handle what is waiting, then exit")
    args = p.parse_args(argv)
    allowed = set(args.allow_from or ["claude-main"])
    cmd = shlex.split(args.cmd)
    cwd_for = dict(item.split("=", 1) for item in args.cwd_for)

    conn = bus.connect()
    while True:
        bus.touch(conn, args.name, "worker")
        for msg in bus.inbox(conn, args.name, kinds=("task",)):
            # Anything that can write to the bus could send a task, and a task runs an agent with shell access.
            if msg["sender"] not in allowed:
                bus.send(conn, args.name, msg["sender"], "refused: sender not allowed", reply_to=msg["id"])
                continue
            # shortcut: the task is marked read before it runs, so a crash mid-task loses it; fine for v1.
            run_task(conn, args.name, msg, cmd, cwd_for.get(msg["sender"], args.cwd), args.timeout,
                     args.keep_session)
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
