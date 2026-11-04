"""Tests for inbox(kinds=...), unread() and the worker/watch commands.
Run: .venv/bin/python -m unittest discover -s tests -v
The worker is tested with `echo` standing in for the real agent, so no model is called.
"""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from agent_bus import bus, worker


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENT_BUS_DB"] = str(Path(self.tmp.name) / "bus.db")  # worker uses the default path
        self.conn = bus.connect()

    def tearDown(self):
        self.conn.close()
        del os.environ["AGENT_BUS_DB"]
        self.tmp.cleanup()

    def test_inbox_kinds_leaves_other_messages_unread(self):
        bus.send(self.conn, "a", "w", "just chatting")
        bus.send(self.conn, "a", "w", "do this", kind="task")
        self.assertEqual([m["body"] for m in bus.inbox(self.conn, "w", kinds=("task",))], ["do this"])
        self.assertEqual([m["body"] for m in bus.inbox(self.conn, "w")], ["just chatting"])

    def test_unread_does_not_mark_read(self):
        bus.send(self.conn, "a", "w", "hi")
        self.assertEqual(len(bus.unread(self.conn, "w")), 1)
        self.assertEqual(len(bus.inbox(self.conn, "w")), 1)

    def test_worker_runs_task_and_replies(self):
        task_id = bus.send(self.conn, "claude-main", "w", "hello from the bus", kind="task")
        worker.worker(["w", "--once", "--cmd", "echo", "--cwd", self.tmp.name])
        reply = bus.inbox(self.conn, "claude-main")[0]
        self.assertEqual(reply["reply_to"], task_id)
        self.assertEqual(reply["sender"], "w")
        self.assertIn("hello from the bus", reply["body"])
        self.assertTrue(reply["body"].startswith("done"))
        self.assertIn("hello from the bus", bus.note_get(self.conn, f"result-{task_id}")["body"])

    def test_worker_refuses_unknown_sender(self):
        bus.send(self.conn, "stranger", "w", "rm -rf things", kind="task")
        worker.worker(["w", "--once", "--cmd", "echo"])
        self.assertIn("refused", bus.inbox(self.conn, "stranger")[0]["body"])
        self.assertIsNone(bus.note_get(self.conn, "result-1"))

    def test_worker_reports_failure_and_cuts_long_output(self):
        bus.send(self.conn, "claude-main", "w", "x" * 5000, kind="task")
        worker.worker(["w", "--once", "--cmd", "echo"])
        body = bus.inbox(self.conn, "claude-main")[0]["body"]
        self.assertIn("full output in note", body)
        self.assertLess(len(body), 2000)
        bus.send(self.conn, "claude-main", "w", "anything", kind="task")
        worker.worker(["w", "--once", "--cmd", "definitely-not-a-command-xyz"])
        self.assertIn("failed", bus.inbox(self.conn, "claude-main")[0]["body"])

    def test_worker_ignores_plain_messages(self):
        bus.send(self.conn, "claude-main", "w", "not a task")
        worker.worker(["w", "--once", "--cmd", "echo"])
        self.assertEqual(bus.inbox(self.conn, "claude-main"), [])  # no reply, message still waiting
        self.assertEqual(len(bus.inbox(self.conn, "w")), 1)

    def test_watch_prints_new_mail_once(self):
        bus.send(self.conn, "a", "me", "ping")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            worker.watch(["me", "--once"])
        self.assertIn("from a", out.getvalue())
        self.assertEqual(len(bus.unread(self.conn, "me")), 1)  # watching must not consume it


if __name__ == "__main__":
    unittest.main()
