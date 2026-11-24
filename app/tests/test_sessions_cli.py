"""sessions CLI: failure wording, stuck-run detection, key resolution and read-only behavior, all on fixtures."""
import contextlib
import io
import json
import os
import re
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sessions_cli as sc

CLAUDE_ID = "f743a19d-11a0-41c3-adb7-aee6194838c3"
CLAUDE_KEY = "claude-" + CLAUDE_ID
CODEX_ID = "01a1e338-cb16-7010-8b30-ca50a551df66"
CODEX_KEY = "codex-" + CODEX_ID
AUTH_TEXT = ("Failed to authenticate: this login reached its time limit and cannot be renewed. "
             "Run `claude auth login` to sign in again")
LIMIT_TEXT = "You've hit your weekly limit · resets Nov 17 at 12am (America/Los_Angeles)"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def claude_line(role, text, ts, error=None):
    o = {"type": role, "timestamp": iso(ts), "message": {"role": role, "content": [{"type": "text", "text": text}]}}
    if error:
        o.update({"isApiErrorMessage": True, "error": error})
    return json.dumps(o, separators=(",", ":"))


def write_lines(path, lines, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    if mtime:
        os.utime(path, (mtime, mtime))


def codex_event(kind, ts, **payload):
    return json.dumps({"timestamp": iso(ts), "type": "event_msg", "payload": dict(type=kind, **payload)}, separators=(",", ":"))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.data, self.claude, self.codex = base / "data", base / "claude", base / "codex"
        (self.data / "execution" / "runs").mkdir(parents=True)
        self.claude.mkdir()
        self.codex.mkdir()
        self.saved = {k: getattr(sc, k) for k in ("DATA", "CLAUDE_ROOT", "CODEX_ROOT", "PORT")}
        sc.DATA, sc.CLAUDE_ROOT, sc.CODEX_ROOT, sc.PORT = self.data, self.claude, self.codex, 9   # port 9: nothing listens
        os.environ["SESSIONS_CACHE_DIR"] = str(base / "cache")
        sc._state["server"] = None           # the first Context probes port 9 and finds it closed; no network use
        sc._ps.clear()
        sc.NOTES.clear()
        self.t = time.time()

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(sc, k, v)
        os.environ.pop("SESSIONS_CACHE_DIR", None)
        sc._state["server"] = None
        sc._ps.clear()
        self.tmp.cleanup()

    def run_json(self, run_id, **fields):
        run = {"id": run_id, "operation": "message", "host_id": "mac", "process_transport": "local", "cwd": "/work"}
        run.update(fields)
        d = self.data / "execution" / "runs" / run_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "run.json").write_text(json.dumps(run))

    def claude_transcript(self, lines, age_s=0):
        path = self.claude / "-work" / (CLAUDE_ID + ".jsonl")
        write_lines(path, lines, self.t - age_s)
        return path

    def codex_rollout(self, lines, age_s=0):
        day = datetime.fromtimestamp(self.t)
        path = self.codex / day.strftime("%Y/%m/%d") / (day.strftime("rollout-%Y-%m-%dT00-00-00-") + CODEX_ID + ".jsonl")
        write_lines(path, lines, self.t - age_s)
        return path

    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            sc.main(list(argv))
        return out.getvalue()


class ClassifyTests(unittest.TestCase):
    def test_claude_weekly_limit_with_reset_time(self):
        ref = datetime(2026, 11, 15, 17, 7, tzinfo=timezone.utc).timestamp()
        c = sc.classify(LIMIT_TEXT, "rate_limit", ref)
        self.assertEqual(c["kind"], "limit_reached")
        # Nov 17 00:00 in Los Angeles is 08:00 UTC
        self.assertEqual(c["resets_at"], datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc).timestamp())

    def test_claude_time_only_reset_is_the_next_occurrence(self):
        ref = datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc).timestamp()    # 12:00 in Los Angeles
        c = sc.classify("You've hit your weekly limit · resets 1am (America/Los_Angeles)", "rate_limit", ref)
        self.assertEqual(c["resets_at"], datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc).timestamp())

    def test_codex_usage_limit_wording_from_rollouts(self):
        text = ("You've hit your usage limit. To continue using Codex and get access to GPT-5.3-Codex, "
                "start a free trial of Plus today (https://chatgpt.com/explore/plus), or try again at Aug 26th, 2026 8:11 AM.")
        c = sc.classify(text, "usage_limit_exceeded")
        self.assertEqual(c["kind"], "limit_reached")
        self.assertEqual(datetime.fromtimestamp(c["resets_at"]).strftime("%Y-%m-%d %H:%M"), "2026-08-26 08:11")

    def test_sign_in_failures(self):
        self.assertEqual(sc.classify(AUTH_TEXT, "authentication_failed")["kind"], "auth_expired")
        self.assertEqual(sc.classify("Not logged in · Please run /login", "authentication_failed")["kind"], "not_logged_in")
        self.assertEqual(sc.classify("Your access token could not be refreshed. Please log out and sign in again.")["kind"], "auth_expired")

    def test_other_kinds(self):
        self.assertEqual(sc.classify("Selected model is at capacity. Please try a different model.", "server_overloaded")["kind"], "rate_limited")
        self.assertEqual(sc.classify("API Error: Can't reach the API server (ENOTFOUND)", "server_error")["kind"], "other_error")

    def test_server_failure_field_wins(self):
        c = sc.classify("anything", failure={"kind": "auth_expired", "raw": "x"})
        self.assertEqual((c["kind"], c["from"]), ("auth_expired", "server"))
        self.assertEqual(sc.classify("anything", failure={"kind": "other"})["kind"], "other_error")


class ProblemTests(Fixture):
    def problems(self):
        ctx = sc.Context()
        return {p["key"]: p for p in sc.find_problems(ctx)}

    def test_running_run_with_dead_process_and_limit_error_is_stuck(self):
        self.claude_transcript([claude_line("user", "go", self.t - 7 * 3600),
                                claude_line("assistant", LIMIT_TEXT, self.t - 7 * 3600 + 5, error="rate_limit")], age_s=7 * 3600)
        self.run_json("r1", provider="claude", key=CLAUDE_KEY, session_id=CLAUDE_ID, status="running",
                      created_at=iso(self.t - 7.1 * 3600), updated_at=iso(self.t - 7 * 3600), process_pid=999999,
                      process_identity={"pid": 999999, "start_sec": int(self.t - 7.1 * 3600)}, account_id="claude-default")
        p = self.problems()[CLAUDE_KEY]
        self.assertEqual(p["kind"], "limit_reached")
        self.assertIn("dead_process", p["flags"])
        self.assertIn("stale_running", p["flags"])
        self.assertIn("process 999999 is gone", p["reason"])
        self.assertIn("weekly limit", p["reason"])
        self.assertIsNotNone(p["resets_at"])

    def test_zombie_process(self):
        sc._ps.update({4242: ("Z", self.t - 100)})
        self.run_json("r1", provider="claude", key=CLAUDE_KEY, session_id=CLAUDE_ID, status="running",
                      created_at=iso(self.t - 200), updated_at=iso(self.t - 10), process_pid=4242)
        self.claude_transcript([claude_line("user", "go", self.t - 100)])
        p = self.problems()[CLAUDE_KEY]
        self.assertEqual(p["kind"], "dead_process")
        self.assertIn("zombie_process", p["flags"])
        self.assertIn("zombie", p["reason"])

    def test_healthy_running_run_is_not_a_problem(self):
        sc._ps.update({4242: ("S", self.t - 100)})
        self.run_json("r1", provider="claude", key=CLAUDE_KEY, session_id=CLAUDE_ID, status="running",
                      created_at=iso(self.t - 200), updated_at=iso(self.t - 5), process_pid=4242)
        self.claude_transcript([claude_line("user", "go", self.t - 100), claude_line("assistant", "working", self.t - 5)])
        self.assertEqual(self.problems(), {})

    def test_stale_running_without_a_process_check(self):
        self.run_json("r1", provider="claude", key=CLAUDE_KEY, session_id=CLAUDE_ID, status="running", host_id="sc",
                      process_transport="ssh", created_at=iso(self.t - 5 * 3600), updated_at=iso(self.t - 4 * 3600))
        self.claude_transcript([claude_line("user", "go", self.t - 4 * 3600)], age_s=4 * 3600)
        p = self.problems()[CLAUDE_KEY]
        self.assertEqual(p["kind"], "stale_running")
        self.assertNotIn("dead_process", p["flags"])

    def test_codex_run_still_running_after_task_complete(self):
        self.codex_rollout([codex_event("task_started", self.t - 600), codex_event("task_complete", self.t - 300, error=None)])
        sc._ps.update({4243: ("S", self.t - 700)})
        self.run_json("r2", provider="codex", key=CODEX_KEY, session_id=CODEX_ID, status="running",
                      created_at=iso(self.t - 650), updated_at=iso(self.t - 2), process_pid=4243)
        p = self.problems()[CODEX_KEY]
        self.assertEqual(p["kind"], "completed_but_running")

    def test_codex_turn_that_ended_before_the_run_does_not_count(self):
        self.codex_rollout([codex_event("task_complete", self.t - 3000, error=None)])
        sc._ps.update({4243: ("S", self.t - 700)})
        self.run_json("r2", provider="codex", key=CODEX_KEY, session_id=CODEX_ID, status="running",
                      created_at=iso(self.t - 650), updated_at=iso(self.t - 2), process_pid=4243)
        self.assertEqual(self.problems(), {})

    def test_codex_task_complete_with_usage_limit_error(self):
        err = {"message": "You've hit your usage limit. Visit settings, or try again at Dec 1st, 2026 9:00 AM.",
               "codex_error_info": "usage_limit_exceeded"}
        self.codex_rollout([codex_event("task_started", self.t - 60), codex_event("task_complete", self.t - 30, error=err)])
        p = self.problems()[CODEX_KEY]
        self.assertEqual(p["kind"], "limit_reached")
        self.assertEqual(datetime.fromtimestamp(p["resets_at"]).strftime("%Y-%m-%d %H:%M"), "2026-12-01 09:00")

    def test_failed_run_auth_error_and_later_success_clears_it(self):
        self.run_json("bad", provider="claude", key=CLAUDE_KEY, session_id=CLAUDE_ID, status="failed", error=AUTH_TEXT,
                      created_at=iso(self.t - 3600), updated_at=iso(self.t - 3500), account_id="claude-default")
        self.claude_transcript([claude_line("user", "go", self.t - 3600)], age_s=3500)
        p = self.problems()[CLAUDE_KEY]
        self.assertEqual(p["kind"], "auth_expired")
        self.assertIn("claude auth login", p["fix"])
        self.run_json("good", provider="claude", key=CLAUDE_KEY, session_id=CLAUDE_ID, status="completed",
                      created_at=iso(self.t - 600), updated_at=iso(self.t - 500))
        self.assertEqual(self.problems(), {})

    def test_interrupted_without_error_is_not_a_problem(self):
        self.run_json("r1", provider="claude", key=CLAUDE_KEY, session_id=CLAUDE_ID, status="interrupted",
                      created_at=iso(self.t - 600), updated_at=iso(self.t - 500))
        self.claude_transcript([claude_line("user", "go", self.t - 600), claude_line("assistant", "ok", self.t - 520)])
        self.assertEqual(self.problems(), {})

    def test_error_turn_in_a_chat_the_broker_never_launched(self):
        self.claude_transcript([claude_line("user", "go", self.t - 100), claude_line("assistant", "Not logged in · Please run /login", self.t - 90, error="authentication_failed")])
        p = self.problems()[CLAUDE_KEY]
        self.assertEqual(p["kind"], "not_logged_in")
        self.assertTrue(p["headless"])

    def test_old_errors_are_outside_the_window(self):
        self.claude_transcript([claude_line("assistant", AUTH_TEXT, self.t - 3 * 86400, error="authentication_failed")], age_s=3 * 86400)
        self.assertEqual(self.problems(), {})

    def test_server_failure_record_is_used(self):
        self.run_json("f", provider="claude", key=CLAUDE_KEY, session_id=CLAUDE_ID, status="failed", error="whatever",
                      failure={"kind": "limit_reached", "provider": "claude", "resets_at": iso(self.t + 3600), "raw": "limit"},
                      created_at=iso(self.t - 300), updated_at=iso(self.t - 200))
        p = self.problems()[CLAUDE_KEY]
        self.assertEqual(p["kind"], "limit_reached")
        self.assertAlmostEqual(p["resets_at"], self.t + 3600, delta=2)


class CommandTests(Fixture):
    def setUp(self):
        super().setUp()
        self.claude_transcript([
            claude_line("user", "<system-reminder>noise</system-reminder>first question", self.t - 300),
            claude_line("assistant", "x" * 600, self.t - 250),
            claude_line("user", "second question", self.t - 200),
            claude_line("assistant", "answer", self.t - 100)])
        sub = self.claude / "-work" / CLAUDE_ID / "subagents"
        write_lines(sub / "agent-a1.jsonl", [claude_line("assistant", "sub", self.t - 50)])
        write_lines(self.claude / "-work" / "f743ffff-0000-0000-0000-000000000000.jsonl", [claude_line("user", "other", self.t - 40)])
        (self.data / "index.json").write_text(json.dumps({
            CLAUDE_KEY: {"key": CLAUDE_KEY, "source": "claude", "id": CLAUDE_ID, "file": str(self.claude / "-work" / (CLAUDE_ID + ".jsonl")),
                         "cwd": "/work", "title_seed": "Seed title", "ts_first": iso(self.t - 300), "ts_last": iso(self.t - 100),
                         "node": "mac", "model_main": "claude-test-1", "account_id": "claude-default"},
            "claude-f743ffff-0000-0000-0000-000000000000": {"key": "claude-f743ffff-0000-0000-0000-000000000000", "source": "claude", "id": "f743ffff-0000-0000-0000-000000000000"},
            CLAUDE_KEY + "-agent-a1": {"key": CLAUDE_KEY + "-agent-a1", "source": "claude", "id": CLAUDE_ID + "-agent-a1"}}))
        (self.data / "enrich.json").write_text(json.dumps({CLAUDE_KEY: {"title": "Enriched title"}}))

    def test_resolve_accepts_key_uuid_and_prefixes(self):
        ctx = sc.Context()
        for q in (CLAUDE_KEY, CLAUDE_ID, "claude-f743a19d", "f743a19d", "F743A19D-11A0"):
            self.assertEqual(sc.resolve(ctx, q), CLAUDE_KEY)

    def test_resolve_reports_ambiguity_and_misses(self):
        ctx = sc.Context()
        with self.assertRaises(SystemExit) as e:
            sc.resolve(ctx, "f743")
        self.assertIn("ambiguous", str(e.exception))
        with self.assertRaises(SystemExit) as e:
            sc.resolve(ctx, "nothing-here")
        self.assertIn("no session matches", str(e.exception))

    def test_show_json(self):
        d = json.loads(self.cli("show", "f743a19d-11", "--json"))
        self.assertEqual((d["provider"], d["session_id"], d["title"], d["model"], d["account"]),
                         ("claude", CLAUDE_ID, "Enriched title", "claude-test-1", "claude-default"))
        self.assertEqual(d["subagent_transcripts"], 1)
        self.assertEqual([t["role"] for t in d["turns"]], ["assistant", "user", "assistant"])
        self.assertTrue(d["transcripts"][0].endswith(CLAUDE_ID + ".jsonl"))

    def test_show_trims_turns_to_400_characters(self):
        d = json.loads(self.cli("show", CLAUDE_KEY, "--json"))
        self.assertLessEqual(max(len(t["text"]) for t in d["turns"]), 400)

    def test_tail_strips_injected_blocks(self):
        d = json.loads(self.cli("tail", CLAUDE_KEY, "-n", "4", "--json"))
        self.assertEqual(d["turns"][0]["text"], "first question")
        self.assertEqual(len(d["turns"]), 4)

    def test_ref_line_format(self):
        line = self.cli("ref", CLAUDE_KEY).strip()
        parts = line.split(" | ")
        self.assertEqual(parts[0], "claude session " + CLAUDE_ID)
        self.assertEqual(parts[1], "Enriched title")
        self.assertEqual(parts[2], "cwd /work")
        self.assertTrue(parts[3].startswith("transcript ") and parts[3].endswith(CLAUDE_ID + ".jsonl"))
        self.assertEqual(parts[4], "account claude-default")
        self.assertEqual(parts[5], "status: sessions show claude-f743a19d")

    def test_recent_lists_chat_once_with_subagent_activity(self):
        d = json.loads(self.cli("recent", "--hours", "1", "--json"))
        keys = [r["key"] for r in d["recent"]]
        self.assertEqual(keys.count(CLAUDE_KEY), 1)

    def test_problems_table_and_json_when_server_is_down(self):
        self.run_json("bad", provider="claude", key=CLAUDE_KEY, session_id=CLAUDE_ID, status="failed", error=AUTH_TEXT,
                      created_at=iso(self.t - 400), updated_at=iso(self.t - 90))
        text = self.cli("problems")
        self.assertIn("auth_expired", text)
        self.assertIn("claude auth login", text)
        self.assertEqual(json.loads(self.cli("problems", "--json"))["problems"][0]["kind"], "auth_expired")

    def test_commands_never_write_under_data(self):
        def snapshot():
            return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in self.data.rglob("*") if p.is_file()}
        before = snapshot()
        for argv in (["live"], ["recent"], ["problems"], ["show", CLAUDE_KEY], ["tail", CLAUDE_KEY], ["ref", CLAUDE_KEY]):
            self.cli(*argv)
        self.assertEqual(snapshot(), before)

    def test_live_without_a_snapshot_still_lists_running_runs(self):
        sc._ps.update({4242: ("S", self.t - 100)})
        self.run_json("r1", provider="claude", key=CLAUDE_KEY, session_id=CLAUDE_ID, status="running",
                      created_at=iso(self.t - 200), updated_at=iso(self.t - 5), process_pid=4242, model="claude-test-1")
        rows = json.loads(self.cli("live", "--json"))["live"]
        self.assertEqual([(r["key"], r["status"]) for r in rows], [(CLAUDE_KEY, "running")])
        self.assertTrue(any("not reachable" in n for n in sc.NOTES))


class SourceHygieneTests(unittest.TestCase):
    def test_source_has_no_personal_paths_or_addresses(self):
        text = Path(sc.__file__).read_text()
        self.assertNotIn("/Users/", text)
        self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.(com|edu|org|net)", text))


if __name__ == "__main__":
    unittest.main()
