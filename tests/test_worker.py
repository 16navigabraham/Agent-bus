"""Tests for inbox(kinds=...), unread() and the worker/watch commands.
Run: .venv/bin/python -m unittest discover -s tests -v
The worker is tested with `echo` standing in for the real agent, so no model is called.
"""

import contextlib
import io
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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
        # echo never says TASK COMPLETE, so exiting normally must NOT be reported as finished.
        self.assertTrue(reply["body"].startswith("exited without saying it was complete"))
        self.assertIn("hello from the bus", bus.note_get(self.conn, f"result-{task_id}")["body"])

    def _reply_to_fake_agent(self, shell_script, *extra, body="the task text"):
        """Send one task to a fake agent (a `sh -c` script) and return the worker's reply body."""
        bus.send(self.conn, "claude-main", "w", body, kind="task")
        worker.worker(["w", "--once", "--cmd", f"sh -c '{shell_script}' x", *extra])
        return bus.inbox(self.conn, "claude-main")[0]["body"]

    def test_status_comes_from_the_completion_marker(self):
        self.assertTrue(self._reply_to_fake_agent("echo all good; echo TASK COMPLETE").startswith("completed"))
        body = self._reply_to_fake_agent("echo half way; echo TASK INCOMPLETE: step 3 left")
        self.assertTrue(body.startswith("incomplete"))
        self.assertIn("step 3 left", body)

    def test_trace_goes_to_the_note_not_the_reply(self):
        body = self._reply_to_fake_agent("echo answer; echo noisy-tool-trace >&2; echo TASK COMPLETE")
        self.assertNotIn("noisy-tool-trace", body)
        self.assertIn("noisy-tool-trace", bus.note_get(self.conn, "result-1")["body"])

    SHOW_ARGS = 'echo "args: $@"; echo TASK COMPLETE'

    def test_keep_session_continues_the_senders_existing_session(self):
        with mock.patch.object(worker, "find_session", return_value="ses_abc") as lookup:
            body = self._reply_to_fake_agent(self.SHOW_ARGS, "--keep-session")
        lookup.assert_called_once_with("bus:w:claude-main")  # one session per worker and sender
        self.assertIn("args: -s ses_abc the task text", body)

    def test_keep_session_names_a_new_session_when_none_exists(self):
        with mock.patch.object(worker, "find_session", return_value=None):
            body = self._reply_to_fake_agent(self.SHOW_ARGS, "--keep-session")
        self.assertIn("args: --title bus:w:claude-main the task text", body)

    def test_slash_new_starts_a_fresh_session(self):
        with mock.patch.object(worker, "find_session", return_value="ses_abc"):
            body = self._reply_to_fake_agent(self.SHOW_ARGS, "--keep-session", body="/new start over")
        self.assertNotIn("ses_abc", body)
        self.assertIn("--title bus:w:claude-main start over", body)  # "/new" itself is not passed on

    def test_a_linked_session_note_is_adopted_before_any_title_lookup(self):
        bus.note_set(self.conn, "session:w:claude-main", "ses_linked", "me")
        with mock.patch.object(worker, "find_session", return_value="ses_by_title") as lookup:
            body = self._reply_to_fake_agent(self.SHOW_ARGS, "--keep-session")
        lookup.assert_not_called()
        self.assertIn("args: -s ses_linked the task text", body)

    def test_slash_new_drops_the_link(self):
        bus.note_set(self.conn, "session:w:claude-main", "ses_linked", "me")
        with mock.patch.object(worker, "find_session", return_value=None):
            body = self._reply_to_fake_agent(self.SHOW_ARGS, "--keep-session", body="/new restart")
        self.assertNotIn("ses_linked", body)
        self.assertIsNone(bus.note_get(self.conn, "session:w:claude-main"))

    def test_sessions_are_off_unless_asked_for(self):
        with mock.patch.object(worker, "find_session") as lookup:
            body = self._reply_to_fake_agent(self.SHOW_ARGS)
        lookup.assert_not_called()
        self.assertIn("args: the task text", body)

    def test_headless_agent_gets_its_own_bus_name(self):
        body = self._reply_to_fake_agent('echo "me=$AGENT_BUS_NAME"; echo TASK COMPLETE')
        self.assertIn("me=w-agent", body)

    def test_cwd_for_runs_a_sender_in_its_own_folder(self):
        project = Path(self.tmp.name) / "project"
        project.mkdir()
        body = self._reply_to_fake_agent('pwd; echo TASK COMPLETE', "--cwd-for", f"claude-main={project}")
        self.assertIn(str(project.resolve()), body)

    def test_timeout_kills_the_whole_process_group(self):
        started = time.time()
        # The agent starts a grandchild (sleep) the way opencode starts shells and test scripts.
        body = self._reply_to_fake_agent("sleep 31337 & wait", "--timeout", "1")
        self.assertLess(time.time() - started, 20)
        self.assertTrue(body.startswith("timed out after 1s"))
        # Anchored: only a process whose whole command line is exactly this, never a shell that merely mentions it.
        leftover = subprocess.run(["pgrep", "-f", "^sleep 31337$"], capture_output=True, text=True).stdout
        self.assertEqual(leftover.strip(), "", "the grandchild process was left running")

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
