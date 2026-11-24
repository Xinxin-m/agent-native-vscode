import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import indexer
import server
import session_lineage


class ClaudeDesktopTitleTests(unittest.TestCase):
    local_id = "11111111-1111-4111-8111-111111111111"
    other_id = "22222222-2222-4222-8222-222222222222"
    remote_id = "33333333-3333-4333-8333-333333333333"
    child_id = "44444444-4444-4444-8444-444444444444"
    fork_id = "55555555-5555-4555-8555-555555555555"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        self.chats = self.data / "chats"
        self.index = self.data / "index.json"
        self.desktop = self.data / "desktop"
        self.record = self.desktop / "account" / "workspace" / "local_desktop.json"
        self.record.parent.mkdir(parents=True)
        self.key = "claude-" + self.local_id
        self.cwd = "/work/shared"
        roots = []
        for host, ids in (
            ("mac", [self.local_id, self.other_id, self.child_id, self.fork_id]),
            ("sc", [self.remote_id]),
        ):
            projects = self.data / host / "projects"
            project = projects / "shared"
            project.mkdir(parents=True)
            roots.append((host, str(projects), "", str(self.data / "missing-index")))
            for sid in ids:
                (project / (sid + ".jsonl")).write_text(json.dumps({
                    "type": "user", "cwd": self.cwd, "timestamp": "2026-11-21T10:00:00Z",
                    "message": {"role": "user", "content": "Original user question"},
                }) + "\n")
        files = {name: str(self.data / (name + ".json")) for name in server.FILES}
        Path(files["enrich"]).write_text(json.dumps({self.key: {"title": "Generated label"}}))
        for patcher in (
            patch.object(indexer, "CLAUDE_DESKTOP_SESSIONS", self.desktop, create=True),
            patch.object(indexer, "CHATS", str(self.chats)),
            patch.object(indexer, "INDEX", str(self.index)),
            patch.object(indexer, "load_config", return_value={"min_user_msgs": 1, "min_answer_msgs": 0}),
            patch.object(indexer, "source_roots", return_value=roots),
            patch.object(indexer.sys, "argv", ["indexer.py"]),
            patch.object(server, "CHATS", str(self.chats)),
            patch.dict(server.FILES, files),
            patch.object(session_lineage, "DATA", str(self.data)),
            patch.object(session_lineage, "_cached_resume", return_value={}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_record(self, path, sid, title, **extra):
        path.write_text(json.dumps({
            "cliSessionId": sid, "sessionId": path.stem, "title": title, "cwd": self.cwd, **extra,
        }))

    def refresh(self):
        with contextlib.redirect_stdout(io.StringIO()):
            indexer.main()

    def state_title(self, key):
        row = next(chat for chat in server.build_state()["chats"] if chat["key"] == key)
        return row["title"]

    def test_desktop_title_takes_precedence_and_metadata_only_rename_propagates(self):
        self.write_record(self.record, self.local_id, "Desktop title")
        self.write_record(self.record.with_name("local_remote.json"), self.remote_id, "Incorrect remote title")
        self.record.with_name("local_malformed.json").write_text("partial write")
        self.refresh()
        self.assertEqual(self.state_title(self.key), "Desktop title")
        self.assertEqual(self.state_title("claude-" + self.other_id), "Original user question")
        self.assertEqual(self.state_title("claude-" + self.remote_id), "Original user question")
        self.assertEqual(json.loads(server.chat_body(self.key)["body"])["title"], "Desktop title")
        tree = session_lineage.session_tree(None, {"key": self.key}, None)
        self.assertEqual(tree["nodes"][0]["title"], "Desktop title")

        self.write_record(self.record, self.local_id, "Renamed in Desktop")
        with patch.object(indexer, "parse_unit", side_effect=AssertionError("transcript unchanged")):
            self.refresh()
        self.assertEqual(self.state_title(self.key), "Renamed in Desktop")
        self.assertEqual(json.loads(server.chat_body(self.key)["body"])["title"], "Renamed in Desktop")

        flags = self.data / "flags.json"
        flags.write_text(json.dumps({"titles": {self.key: "Manual Library name"}}))
        self.assertEqual(self.state_title(self.key), "Manual Library name")
        self.assertEqual(json.loads(server.chat_body(self.key)["body"])["title"], "Manual Library name")

        flags.unlink()
        self.record.unlink()
        self.refresh()
        self.assertEqual(self.state_title(self.key), "Generated label")

    def test_handoff_inherits_source_title_in_sidebar_and_open_tab(self):
        self.write_record(self.record, self.local_id, "Source title")
        self.refresh()
        handoff = "claude-" + self.other_id
        handoff_path = self.chats / (handoff + ".json")
        records = json.loads(self.index.read_text())
        records[handoff].update(source_key=self.key, title_seed="Continue work handed off")
        self.index.write_text(json.dumps(records))
        chat = json.loads(handoff_path.read_text())
        chat.update(source_key=self.key, title_seed="Continue work handed off")
        handoff_path.write_text(json.dumps(chat))
        self.assertEqual(self.state_title(handoff), "Source title (continued)")
        self.assertEqual(json.loads(server.chat_body(handoff)["body"])["title"], "Source title (continued)")

        flags = self.data / "flags.json"
        flags.write_text(json.dumps({"titles": {self.key: "Renamed source"}}))
        self.assertEqual(json.loads(server.chat_body(handoff)["body"])["title"], "Renamed source (continued)")
        flags.write_text(json.dumps({"titles": {handoff: "My explicit handoff title"}}))
        self.assertEqual(self.state_title(handoff), "My explicit handoff title")

    def test_delegated_desktop_side_session_joins_tree_and_user_fork_stays_a_root(self):
        side_session = {"sideSession": True, "sessionId": self.record.stem, "taskId": "task"}
        self.write_record(self.record, self.local_id, "Parent")
        self.write_record(self.record.with_name("local_child.json"), self.child_id, "Delegated work",
                          spawnedFrom=side_session)
        self.write_record(self.record.with_name("local_fork.json"), self.fork_id, "User fork",
                          spawnedFrom=dict(side_session, userFork=True))
        self.refresh()
        rows = {row["key"]: row for row in server.build_state()["chats"]}
        child_key = "claude-" + self.child_id
        self.assertTrue(rows[child_key].get("is_child"))
        self.assertEqual(rows[child_key]["parent_key"], self.key)
        self.assertEqual(rows[child_key]["root_key"], self.key)
        self.assertFalse(rows["claude-" + self.fork_id].get("is_child"))
        tree = session_lineage.session_tree(None, {"key": self.key}, None)
        self.assertEqual(tree["edges"], [{"parent_key": self.key, "child_key": child_key}])
        self.assertIn("Delegated work", [node["title"] for node in tree["nodes"]])

    def test_desktop_side_task_stays_out_of_root_lists_and_can_recover_the_missing_parent_tree(self):
        self.write_record(self.record, self.local_id, "Parent")
        child = self.record.with_name("local_child.json")
        self.write_record(child, self.child_id, "Delegated work",
                          spawnedFrom={"sideSession": True, "sessionId": self.record.stem, "taskId": "task"})
        self.refresh()
        (self.data / "mac" / "projects" / "shared" / (self.local_id + ".jsonl")).unlink()
        self.refresh()
        rows = {row["key"]: row for row in server.build_state()["chats"]}
        child_key = "claude-" + self.child_id
        self.assertNotIn(self.key, rows)
        self.assertTrue(rows[child_key].get("is_child"))
        self.assertEqual(rows[child_key]["root_key"], self.key)
        for requested in (child_key, self.key):
            with self.subTest(requested=requested):
                tree = session_lineage.session_tree(None, {"key": requested}, None)
                self.assertEqual(tree["missing_parent_keys"], [self.key])
                self.assertEqual(tree["edges"], [{"parent_key": self.key, "child_key": child_key}])
                self.assertEqual({row["key"] for row in tree["nodes"]}, {self.key, child_key})

    def test_user_fork_correction_clears_only_desktop_owned_lineage_without_reparse(self):
        side_session = {"sideSession": True, "sessionId": self.record.stem, "taskId": "task"}
        self.write_record(self.record, self.local_id, "Parent")
        child = self.record.with_name("local_child.json")
        self.write_record(child, self.child_id, "Delegated work", spawnedFrom=side_session)
        project = self.data / "mac" / "projects" / "shared"
        native = project / self.local_id / "subagents" / "agent-native.jsonl"
        native.parent.mkdir(parents=True)
        native.write_text((project / (self.child_id + ".jsonl")).read_text())
        self.refresh()
        child_key = "claude-" + self.child_id
        stale = json.loads((self.chats / (child_key + ".json")).read_text())
        self.assertEqual(stale["parent_key"], self.key)
        self.write_record(child, self.child_id, "My fork", spawnedFrom=dict(side_session, userFork=True))
        with patch.object(indexer, "parse_unit", side_effect=AssertionError("transcript unchanged")):
            self.refresh()
        rows = {row["key"]: row for row in server.build_state()["chats"]}
        self.assertFalse(rows[child_key].get("is_child"))
        self.assertEqual(rows[child_key]["root_key"], child_key)
        clean = json.loads(server.chat_body(child_key)["body"])
        self.assertNotIn("parent_key", clean)
        self.assertNotIn("is_child", clean)
        self.assertNotIn("lineage_source", clean)
        self.assertEqual(clean["root_key"], child_key)
        self.assertTrue(rows[self.key + "-agent-native"]["is_child"])
        self.assertEqual(rows[self.key + "-agent-native"]["parent_key"], self.key)


if __name__ == "__main__":
    unittest.main()
