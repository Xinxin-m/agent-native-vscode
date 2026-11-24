import json
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch

import label_model
from enrich_fixture import Env

CFG = {"models_file": "/nonexistent"}


def done(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.env = Env().__enter__()
        self.env.patches[-3].stop()                      # use the real label_model.ask
        self.addCleanup(self.env.__exit__)

    def test_claude_limit_falls_back_to_codex_and_remembers(self):
        calls = []
        def claude(*a):
            calls.append("claude"); return done("You've hit your weekly limit", code=1)
        def codex(*a):
            calls.append("codex"); return done('[{"i": 0}]')
        with patch.dict(label_model.CALLERS, {"claude": claude, "codex": codex}):
            self.assertEqual(label_model.ask("p", CFG), ('[{"i": 0}]', "codex"))
            label_model.ask("p", CFG)
        self.assertEqual(calls, ["claude", "codex", "codex"])   # claude is not poked again during backoff
        self.assertFalse(label_model.paused())

    def test_both_unavailable_trips_global_pause_and_stops_spawning(self):
        calls = []
        def limited(*a):
            calls.append(1); return done("Not logged in", code=1)
        with patch.dict(label_model.CALLERS, {"claude": limited, "codex": limited}):
            with self.assertRaises(label_model.Unavailable):
                label_model.ask("p", CFG)
            self.assertTrue(label_model.paused())
            with self.assertRaises(label_model.Unavailable):
                label_model.ask("p", CFG)
        self.assertEqual(len(calls), 2)

    def test_a_valid_answer_mentioning_quota_is_not_a_limit_error(self):
        ok = '[{"i": 0, "title": "API quota planning notes"}]'
        with patch.dict(label_model.CALLERS, {"claude": lambda *a: done(ok), "codex": lambda *a: done("")}):
            self.assertEqual(label_model.ask("p", CFG)[1], "claude")

    def test_models_come_from_the_labeling_tier_file(self):
        path = self.env.p("models.json")
        Path(path).write_text(json.dumps({"tiers": {"fast": {"claude": "claude-haiku-9", "codex": "gpt-luna-9"},
                                                    "small": {"claude": "claude-haiku-8"}}}))
        self.assertEqual(label_model.models({"models_file": path}), {"claude": "claude-haiku-9", "codex": "gpt-luna-9"})
        self.assertEqual(label_model.models({"models_file": path, "labeling_tier": "small"})["claude"], "claude-haiku-8")

    def test_codex_command_uses_verified_flags(self):
        seen = {}
        def run(cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            Path(cmd[cmd.index("-o") + 1]).write_text("[]")
            return done()
        with patch.object(label_model.subprocess, "run", run):
            r = label_model._codex("prompt", "gpt-6-luna", 5, {})
        cmd = seen["cmd"]
        for flag in ("exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "-o", "-"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("-m") + 1], "gpt-6-luna")
        self.assertEqual(seen["kw"]["input"], "prompt")
        self.assertEqual(seen["kw"]["env"]["DASHBOARD_REFRESH_ACTIVE"], "1")
        self.assertEqual(r.stdout, "[]")

    def test_claude_command_keeps_strict_mcp_config(self):
        seen = {}
        with patch.object(label_model.subprocess, "run", lambda cmd, **kw: seen.update(cmd=cmd) or done("[]")):
            label_model._claude("p", "m", 5, {})
        self.assertIn("--strict-mcp-config", seen["cmd"])
        self.assertIn("--no-session-persistence", seen["cmd"])


if __name__ == "__main__":
    unittest.main()
