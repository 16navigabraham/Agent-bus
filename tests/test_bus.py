"""Tests for the core store. Run: .venv/bin/python -m unittest discover -s tests -v"""

import tempfile
import time
import unittest
from pathlib import Path

from agent_bus import bus


class BusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = bus.connect(Path(self.tmp.name) / "bus.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_send_then_inbox_marks_read(self):
        bus.send(self.conn, "a", "b", "hello")
        first = bus.inbox(self.conn, "b")
        self.assertEqual([m["body"] for m in first], ["hello"])
        self.assertEqual(first[0]["sender"], "a")
        self.assertEqual(bus.inbox(self.conn, "b"), [])  # already read

    def test_inbox_is_private_and_ordered(self):
        bus.send(self.conn, "a", "b", "one")
        bus.send(self.conn, "a", "c", "not for b")
        bus.send(self.conn, "a", "b", "two")
        self.assertEqual([m["body"] for m in bus.inbox(self.conn, "b")], ["one", "two"])

    def test_message_waits_for_offline_recipient(self):
        bus.send(self.conn, "a", "never-seen-before", "wait for me", kind="task")
        self.assertEqual(bus.inbox(self.conn, "never-seen-before")[0]["kind"], "task")

    def test_reply_links_to_original(self):
        original = bus.send(self.conn, "a", "b", "question?")
        bus.send(self.conn, "b", "a", "answer", reply_to=original)
        self.assertEqual(bus.inbox(self.conn, "a")[0]["reply_to"], original)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            bus.send(self.conn, "a", "b", "x", kind="shout")
        with self.assertRaises(ValueError):
            bus.send(self.conn, "a", "", "x")
        with self.assertRaises(ValueError):
            bus.send(self.conn, "a", "b", "   ")
        with self.assertRaises(ValueError):
            bus.send(self.conn, "a", "b", "x" * (bus.MAX_BODY + 1))

    def test_history_includes_read_messages_both_directions(self):
        bus.send(self.conn, "a", "b", "ping")
        bus.inbox(self.conn, "b")
        bus.send(self.conn, "b", "a", "pong")
        self.assertEqual([m["body"] for m in bus.history(self.conn, "b")], ["ping", "pong"])

    def test_peers_show_unread_and_hide_stale(self):
        bus.touch(self.conn, "a", "claude-code")
        bus.touch(self.conn, "old")
        self.conn.execute("UPDATE peers SET last_seen = ? WHERE name = 'old'", (time.time() - 99999,))
        bus.send(self.conn, "b", "a", "hi")
        listed = bus.peers(self.conn, within=3600)
        self.assertEqual([p["name"] for p in listed], ["a"])
        self.assertEqual(listed[0]["unread"], 1)
        self.assertEqual(listed[0]["harness"], "claude-code")

    def test_touch_keeps_harness_when_not_given(self):
        bus.touch(self.conn, "a", "opencode")
        bus.touch(self.conn, "a")
        self.assertEqual(bus.peers(self.conn)[0]["harness"], "opencode")

    def test_notes_overwrite_and_list(self):
        bus.note_set(self.conn, "plan", "v1", "a")
        bus.note_set(self.conn, "plan", "v2", "b")
        note = bus.note_get(self.conn, "plan")
        self.assertEqual((note["body"], note["author"]), ("v2", "b"))
        self.assertEqual([n["key"] for n in bus.note_list(self.conn)], ["plan"])
        self.assertIsNone(bus.note_get(self.conn, "missing"))

    def test_two_connections_share_one_file(self):
        other = bus.connect(Path(self.tmp.name) / "bus.db")
        bus.send(self.conn, "a", "b", "across connections")
        self.assertEqual(bus.inbox(other, "b")[0]["body"], "across connections")
        other.close()


if __name__ == "__main__":
    unittest.main()
