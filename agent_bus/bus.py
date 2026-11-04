"""The heart of agent-bus: a tiny message store on top of SQLite.

Nothing in this file knows about MCP. That keeps it easy to read and easy to
test: every function takes a database connection and returns plain Python data.
server.py is the thin layer that exposes these functions to AI tools.
"""

import os
import sqlite3
import time
from pathlib import Path

KINDS = ("message", "task", "context")
MAX_BODY = 20_000  # characters; stops one agent from flooding another's context

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    sender    TEXT NOT NULL,
    recipient TEXT NOT NULL,
    kind      TEXT NOT NULL,
    body      TEXT NOT NULL,
    reply_to  INTEGER,
    read      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_inbox ON messages (recipient, read);

CREATE TABLE IF NOT EXISTS peers (
    name      TEXT PRIMARY KEY,
    harness   TEXT NOT NULL DEFAULT '',
    last_seen REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    key     TEXT PRIMARY KEY,
    body    TEXT NOT NULL,
    author  TEXT NOT NULL,
    updated REAL NOT NULL
);
"""


def default_db_path():
    return Path(os.environ.get("AGENT_BUS_DB", Path.home() / ".agent-bus" / "bus.db"))


def connect(path=None):
    """Open (and create if needed) the shared database."""
    path = Path(path or default_db_path())
    # Messages can contain code and context, so keep the folder and file private.
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # isolation_level=None = autocommit; inbox() opens its own transaction.
    # timeout: several agents share this file, so wait for each other politely.
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # lets one agent read while another writes
    conn.executescript(SCHEMA)
    os.chmod(path, 0o600)
    return conn


def _clean(value, what):
    value = (value or "").strip()
    if not value:
        raise ValueError(f"{what} must not be empty")
    return value


def touch(conn, name, harness=""):
    """Record that `name` is alive right now. Called on every tool call."""
    name = _clean(name, "name")
    conn.execute(
        """INSERT INTO peers (name, harness, last_seen) VALUES (?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET last_seen = excluded.last_seen,
               harness = CASE WHEN excluded.harness = '' THEN harness ELSE excluded.harness END""",
        (name, harness, time.time()),
    )


def peers(conn, within=3600):
    """Agents seen in the last `within` seconds, with how many unread messages each has."""
    now = time.time()
    rows = conn.execute(
        """SELECT p.name, p.harness, p.last_seen,
                  (SELECT COUNT(*) FROM messages m WHERE m.recipient = p.name AND m.read = 0) AS unread
           FROM peers p WHERE p.last_seen >= ? ORDER BY p.last_seen DESC""",
        (now - within,),
    ).fetchall()
    return [
        {"name": r["name"], "harness": r["harness"], "seconds_ago": int(now - r["last_seen"]), "unread": r["unread"]}
        for r in rows
    ]


def send(conn, sender, to, body, kind="message", reply_to=None):
    """Store a message for `to`. The recipient need not be online: it waits in their inbox."""
    to = _clean(to, "recipient")
    body = _clean(body, "body")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if len(body) > MAX_BODY:
        raise ValueError(f"body is {len(body)} characters; the limit is {MAX_BODY}. Put long text in a note.")
    cur = conn.execute(
        "INSERT INTO messages (ts, sender, recipient, kind, body, reply_to) VALUES (?, ?, ?, ?, ?, ?)",
        (time.time(), sender, to, kind, body, reply_to),
    )
    return cur.lastrowid


def inbox(conn, name, kinds=None):
    """Return unread messages for `name`, oldest first, and mark them read.

    `kinds` (e.g. ("task",)) limits it to those kinds; other unread messages stay unread.
    The select and the update happen in one transaction, so a message that
    arrives in between can never be marked read without being returned.
    """
    where, params = "recipient = ? AND read = 0", [name]
    if kinds:
        where += " AND kind IN (%s)" % ",".join("?" * len(kinds))
        params += list(kinds)
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            f"SELECT id, ts, sender, kind, body, reply_to FROM messages WHERE {where} ORDER BY id", params
        ).fetchall()
        conn.executemany("UPDATE messages SET read = 1 WHERE id = ?", [(r["id"],) for r in rows])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return [dict(r) for r in rows]


def unread(conn, name):
    """Unread messages for `name`, oldest first, WITHOUT marking them read (used by the watcher)."""
    rows = conn.execute(
        "SELECT id, ts, sender, kind, body, reply_to FROM messages WHERE recipient = ? AND read = 0 ORDER BY id",
        (name,),
    ).fetchall()
    return [dict(r) for r in rows]


def history(conn, name, limit=20):
    """The last `limit` messages sent or received by `name`, oldest first. Does not mark anything read."""
    rows = conn.execute(
        """SELECT id, ts, sender, recipient, kind, body, reply_to FROM messages
           WHERE sender = ? OR recipient = ? ORDER BY id DESC LIMIT ?""",
        (name, name, max(1, min(limit, 200))),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def note_set(conn, key, body, author):
    """Create or overwrite a shared note. Notes are for context that outlives any one chat."""
    key = _clean(key, "key")
    conn.execute(
        """INSERT INTO notes (key, body, author, updated) VALUES (?, ?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET body = excluded.body, author = excluded.author, updated = excluded.updated""",
        (key, _clean(body, "body"), author, time.time()),
    )


def note_get(conn, key):
    row = conn.execute("SELECT key, body, author, updated FROM notes WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def note_delete(conn, key):
    """Remove a note. Used by the worker to drop a session link when a sender starts a fresh session."""
    conn.execute("DELETE FROM notes WHERE key = ?", (key,))


def note_list(conn):
    rows = conn.execute("SELECT key, author, updated FROM notes ORDER BY updated DESC").fetchall()
    return [dict(r) for r in rows]
