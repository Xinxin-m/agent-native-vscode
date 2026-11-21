"""Tests for the harness scripts. Synthetic data in temp directories only.

Run: python3 -m unittest discover -s scripts/harness -p 'test_*.py'
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import install_hooks  # noqa: E402
import router_hook  # noqa: E402
import scoreboard  # noqa: E402
import session_metrics  # noqa: E402
import sweep  # noqa: E402


def temp_dir(case):
    path = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, path, True)
    return path


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def claude_session(folder, sid, start, prompts, entrypoint="cli"):
    """prompts: list of (minute, text); each gets one assistant reply with a tool call."""
    records = [{"type": "user", "isMeta": True, "entrypoint": entrypoint, "timestamp": f"{start}T12:00:00Z",
                "message": {"content": "<local-command-caveat>x</local-command-caveat>"}}]
    for n, (minute, text) in enumerate(prompts):
        ts = f"{start}T12:{minute:02d}:00Z"
        records.append({"type": "user", "entrypoint": entrypoint, "timestamp": ts, "cwd": "/work",
                        "message": {"content": text}})
        for block in ({"type": "text", "text": "ok"}, {"type": "tool_use", "name": "Agent" if n == 0 else "Bash"}):
            records.append({"type": "assistant", "timestamp": ts, "message": {"id": f"m{n}", "content": [block]}})
        records.append({"type": "user", "timestamp": ts, "message": {"content": [{"type": "tool_result", "content": "x"}]}})
    write_jsonl(folder / "proj" / f"{sid}.jsonl", records)


def codex_session(folder, sid, start, prompts, source="cli"):
    records = [{"type": "session_meta", "timestamp": f"{start}T10:00:00Z", "payload": {"id": sid, "cwd": "/w", "source": source}},
               {"type": "response_item", "timestamp": f"{start}T10:00:00Z", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "<environment_context>x</environment_context>"}]}}]
    for minute, text in prompts:
        ts = f"{start}T10:{minute:02d}:00Z"
        records += [{"type": "response_item", "timestamp": ts, "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": text}]}},
                    {"type": "response_item", "timestamp": ts, "payload": {"type": "function_call", "name": "spawn_agent"}},
                    {"type": "response_item", "timestamp": ts, "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "done"}]}}]
    write_jsonl(folder / "2026" / "01" / "05" / f"rollout-{sid}.jsonl", records)


INSTRUCTION = "please keep every report file name in lower case kebab style with the date"


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir(self)
        self.claude, self.codex, self.out = self.tmp / "claude", self.tmp / "codex", self.tmp / "data" / "metrics.jsonl"
        claude_session(self.claude, "c1", "2026-01-05", [(0, "build the weekly summary table"), (3, "no, that is the wrong column")])
        claude_session(self.claude, "auto", "2026-01-05", [(0, "label this chat")], entrypoint="sdk-cli")
        codex_session(self.codex, "x1", "2026-01-06", [(0, INSTRUCTION), (2, "looks good, thanks")])
        claude_session(self.claude, "c2", "2026-01-12", [(0, INSTRUCTION.replace("please ", "")), (30, "still wrong after waiting")])

    def run_metrics(self, *extra):
        session_metrics.main(["--claude-dir", str(self.claude), "--codex-dir", str(self.codex), "--out", str(self.out),
                              "--since", "2026-01-01", "--until", "2026-02-01", *extra])
        return {r["session"]: r for r in map(json.loads, self.out.read_text().splitlines())}

    def test_counts_and_heuristics(self):
        rows = self.run_metrics()
        self.assertEqual(set(rows), {"c1", "c2", "x1"})  # automated SDK session excluded
        c1, x1, c2 = rows["c1"], rows["x1"], rows["c2"]
        self.assertEqual((c1["user_prompts"], c1["assistant_turns"], c1["tool_calls"], c1["subagents"]), (2, 2, 2, 1))
        self.assertEqual(c1["corrections"], 1)
        self.assertEqual(c1["wall_minutes"], 3.0)
        self.assertEqual((x1["user_prompts"], x1["assistant_turns"], x1["tool_calls"], x1["subagents"]), (2, 2, 2, 2))
        self.assertEqual((x1["corrections"], x1["repeats"]), (0, 0))
        self.assertEqual(c2["repeats"], 1)  # near-duplicate of the earlier Codex instruction
        self.assertEqual(c2["corrections"], 0)  # "still wrong" arrived 30 minutes after the reply
        self.assertNotIn("flagged_text", c1)
        self.assertNotIn("wrong column", self.out.read_text())

    def test_show_text_and_history_kept(self):
        self.run_metrics("--show-text")
        self.assertIn("wrong column", self.out.read_text())
        rows = self.run_metrics("--since", "2026-01-10")  # later flag wins; older rows stay in the file
        self.assertEqual(set(rows), {"c1", "c2", "x1"})
        self.assertTrue(self.run_metrics("--include-automated")["auto"]["automated"])

    def test_scoreboard(self):
        self.run_metrics()
        board = self.tmp / "scoreboard.md"
        scoreboard.main(["--metrics", str(self.out), "--out", str(board), "--min-prompts", "2"])
        text = board.read_text()
        self.assertIn("| 2026-01-05 | 2 | 2.00 (4/2) | 0.50 (1/2) | 0 | 100% (2/2) |", text)
        self.assertIn("| 2026-01-12 | 1 | 2.00 (2/1) → | 0.00 (0/1) ↓ better | 1 ↑ worse | 100% (1/1) → |", text)
        self.assertIn("proxies", text)
        self.assertEqual(text.count("is better"), 4)


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.root, self.reports = temp_dir(self), temp_dir(self)
        files = {"README.md": "[ok](docs/guide.md) [bad](docs/missing.md) [web](https://example.com)\n",
                 "notes.txt": "stray", "docs/guide.md": "x" * 2000, "docs/guide-v2.md": "x" * 2000,
                 "docs/plan copy.md": "y", "docs/report_final.pdf": "z", "data/table.csv": "a,b",
                 "data/.DS_Store": "", "a/.DS_Store": "", "tmp/old.log": "old",
                 "alpha/reports-2024/x.md": "1", "beta/reports-2025/y.md": "2"}
        for name, text in files.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)
        for empty in ("empty/inner", "data/keep-empty"):
            (self.root / empty).mkdir(parents=True)
        old = time.time() - 30 * 86400
        os.utime(self.root / "tmp/old.log", (old, old))

    def test_findings(self):
        report = self.reports / "sweep.json"
        code = sweep.main([str(self.root), "--json", str(report), "--md", str(report.with_suffix(".md")),
                           "--max", "stray_root=0"])
        found = json.loads(report.read_text())["findings"]
        paths = lambda key: {item["path"] for item in found.get(key, [])}
        self.assertEqual(code, 1)
        self.assertEqual(paths("stray_root"), {"notes.txt"})
        self.assertEqual(paths("version_copies"), {"docs/guide-v2.md", "docs/plan copy.md", "docs/report_final.pdf"})
        self.assertEqual(paths("stale_scratch"), {"tmp/old.log"})
        self.assertEqual({item["target"] for item in found["broken_links"]}, {"docs/missing.md"})
        self.assertEqual(found["duplicates"][0]["paths"], ["docs/guide-v2.md", "docs/guide.md"])
        self.assertIn("empty/inner", paths("empty_dirs"))
        self.assertIn("empty", paths("empty_dirs"))
        self.assertIn("a", paths("empty_dirs"))  # holds only OS junk
        self.assertEqual(found["scattered_kinds"][0]["kind"], "reports-*")
        self.assertTrue(next(i for i in found["empty_dirs"] if i["path"] == "data/keep-empty")["protected"])

    def test_fix_respects_protect_list(self):
        sweep.main([str(self.root), "--fix", "--md", str(self.reports / "sweep.md")])
        self.assertFalse((self.root / "empty").exists())
        self.assertFalse((self.root / "a").exists())
        self.assertTrue((self.root / "data/.DS_Store").exists())
        self.assertTrue((self.root / "data/keep-empty").is_dir())
        self.assertTrue((self.root / "notes.txt").exists() and (self.root / "tmp/old.log").exists())
        self.assertTrue((self.root / "docs/guide-v2.md").exists())


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir(self)
        self.rules = self.tmp / "rules.json"
        self.rules.write_text(json.dumps([
            {"name": "copies", "pattern": r"\bv2\b", "rule": "Edit in place.", "skill": "files"},
            {"name": "broad", "pattern": r"\bthe\b", "rule": "Too broad."},
            {"name": "bad", "pattern": "(", "rule": "Invalid."}]))

    def hook(self, payload):
        env = dict(os.environ, HARNESS_HOME=str(self.tmp))
        result = subprocess.run([sys.executable, str(HERE / "router_hook.py"), "--rules", str(self.rules)],
                                input=payload, capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 0)
        return result.stdout

    def test_hook_output(self):
        out = json.loads(self.hook(json.dumps({"session_id": "s", "prompt": "save a v2 of it"})))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("Edit in place. Use the `files` skill.", out["hookSpecificOutput"]["additionalContext"])
        self.assertIn("copies", (self.tmp / "data/router_log.jsonl").read_text())
        self.assertEqual(self.hook(json.dumps({"prompt": "nothing here"})), "")
        self.assertEqual(self.hook(json.dumps({"prompt": "/help v2"})), "")
        self.assertEqual(self.hook("not json"), "")

    def test_replay_flags_broad_and_invalid(self):
        prompts = ["fix the bug", "make a v2", "run tests", "ship it", "update docs", "rename file", "add a chart"]
        lines, ok = router_hook.replay(router_hook.load_rules(self.rules), prompts, 0.15)
        text = "\n".join(lines)
        self.assertFalse(ok)
        self.assertIn("| copies | 1 | 14.3% | ok |", text)
        self.assertIn("| broad | 1 | 14.3% | ok |", text)
        self.assertIn("invalid", text)
        lines, _ = router_hook.replay(router_hook.load_rules(self.rules), prompts + ["the end"], 0.15)
        self.assertIn("| broad | 2 | 25.0% | too broad |", "\n".join(lines))

    def test_example_rules_compile(self):
        rules = router_hook.load_rules(HERE / "router_rules.example.json")
        self.assertTrue(4 <= len(rules) <= 5 and all(r["regex"] for r in rules))


class HookInstallTests(unittest.TestCase):
    def test_session_end_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HARNESS_HOME=tmp)
            for payload in (json.dumps({"session_id": "s1", "transcript_path": "/t/s1.jsonl", "reason": "other"}), "garbage"):
                result = subprocess.run([sys.executable, str(HERE / "session_end_hook.py")], input=payload,
                                        capture_output=True, text=True, env=env, timeout=10)
                self.assertEqual((result.returncode, result.stdout), (0, ""))
            lines = (Path(tmp) / "data/learner_queue.jsonl").read_text().splitlines()
            self.assertEqual([json.loads(l)["session_id"] for l in lines], ["s1"])

    def test_merge_is_idempotent_and_keeps_existing(self):
        existing = {"model": "x", "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other"}]}],
                                            "Stop": [{"hooks": [{"type": "command", "command": "stop-hook"}]}]}}
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            settings.write_text(json.dumps(existing))
            self.addCleanup(setattr, install_hooks, "HOME", install_hooks.HOME)
            install_hooks.HOME = Path(tmp)
            install_hooks.main(["--settings", str(settings), "--python", "python3"])
            once = json.loads(settings.read_text())
            install_hooks.main(["--settings", str(settings), "--python", "python3"])
            self.assertEqual(json.loads(settings.read_text()), once)
            self.assertEqual(len(once["hooks"]["UserPromptSubmit"]), 2)
            self.assertIn("session_end_hook.py", json.dumps(once["hooks"]["SessionEnd"]))
            self.assertEqual(once["hooks"]["Stop"], existing["hooks"]["Stop"])
            self.assertTrue(any((Path(tmp) / "backups").iterdir()))
            install_hooks.main(["--settings", str(settings), "--uninstall"])
            self.assertEqual(json.loads(settings.read_text()), existing)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            install_hooks.main(["--settings", str(settings), "--dry-run"])
            self.assertFalse(settings.exists())


if __name__ == "__main__":
    unittest.main()
