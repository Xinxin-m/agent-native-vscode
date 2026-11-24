import unittest

from enrich_fixture import Env


def keep(i, **extra):
    return dict({"i": i, "title": "keep", "add": [], "drop": [], "project": "keep"}, **extra)


def labelled(env, key, n_user_then, **extra):
    """An existing label that covered the first n_user_then user turns."""
    rec = {"title": "Old title", "description": "d", "keywords": ["x", "y"], "topic_hint": "", "is_ops": False,
           "model": "claude-haiku", "content_sig": f"{n_user_then}:{n_user_then}:old",
           "labelled_through_turn": 2 * n_user_then, "labelled_n_user": n_user_then, "labelled_n_answer": n_user_then}
    env.done[key] = dict(rec, **extra)


class DailyTests(unittest.TestCase):
    def test_only_chats_with_new_user_turns_are_sent_and_only_the_new_turns(self):
        with Env() as env:
            env.chat("grew", 4); labelled(env, "grew", 2)
            env.chat("same", 3); labelled(env, "same", 3)
            env.reply = lambda kind, n: [keep(0, title="New focus title", add=["Z"], drop=["x"])]
            done = env.run("--daily")
        self.assertEqual(len(env.prompts), 1)
        p = env.prompts[0]
        self.assertIn("title: Old title", p)
        self.assertIn("grew user message 3", p)
        self.assertIn("grew user message 2", p)
        self.assertNotIn("grew user message 1", p)        # already covered by the label
        self.assertNotIn("same user message", p)
        self.assertLess(p.index("grew user message 3"), p.index("grew user message 2"))   # newest first
        self.assertEqual(done["grew"]["title"], "New focus title")
        self.assertEqual(done["grew"]["keywords"], ["y", "z"])
        self.assertEqual((done["grew"]["labelled_through_turn"], done["grew"]["labelled_n_user"]), (8, 4))
        self.assertEqual(done["same"]["title"], "Old title")

    def test_per_chat_cap_and_twenty_chats_per_call(self):
        with Env() as env:
            for i in range(45):
                env.chat(f"c{i:02d}", 6, last=f"2026-11-15T12:{i:02d}:00Z"); labelled(env, f"c{i:02d}", 1)
            env.reply = lambda kind, n: [keep(i) for i in range(n)]
            env.run("--daily")
        self.assertEqual(len(env.prompts), 3)   # 20 + 20 + 5
        for chunk in env.prompts[0].split("\n\n[")[1:]:
            self.assertLessEqual(len(chunk.split("NEW:\n")[1]), 1500)

    def test_runs_once_per_day_unless_forced(self):
        with Env() as env:
            env.chat("a", 3); labelled(env, "a", 1)
            env.reply = lambda kind, n: [keep(0)]
            env.run("--daily"); env.run("--daily")
            self.assertEqual(len(env.prompts), 1)
            self.assertIn("already ran today", env.out)
            env.index["a"].update(n_user=5, n_answer=5)
            env.chat("a", 5)
            env.run("--daily", "--force")
        self.assertEqual(len(env.prompts), 2)

    def test_manual_title_wins_and_project_rules(self):
        with Env() as env:
            for k in ("t", "p", "u", "m"):
                env.chat(k, 3); labelled(env, k, 1)
            env.flags = {"titles": {"t": "Mine"}}
            env.proj["assignments"] = {"p": "alpha", "m": "alpha", "t": "alpha"}
            env.proj["manual_assignments"] = ["m"]
            env.reply = lambda kind, n: [keep(i, title="Model title", project="Beta") for i in range(n)]
            env.run("--daily")
            sugg = env.load("sugg.json")
        self.assertEqual(env.proj["assignments"]["p"], "alpha")      # moves are suggestions only
        self.assertEqual(env.proj["assignments"]["m"], "alpha")      # manual placement untouched
        self.assertEqual(env.proj["assignments"]["u"], "beta")       # no project yet: placed
        self.assertEqual(sorted(sugg), ["p", "t"])                   # not "m": manual
        self.assertEqual(sugg["p"]["to"], "beta")

    def test_manual_title_is_not_written_to_the_label(self):
        with Env() as env:
            env.chat("t", 3); labelled(env, "t", 1)
            env.flags = {"titles": {"t": "Mine"}}
            env.reply = lambda kind, n: [keep(0, title="Model title")]
            done = env.run("--daily")
        self.assertEqual(done["t"]["title"], "Old title")

    def test_legacy_label_without_through_turn_uses_its_signature(self):
        with Env() as env:
            env.chat("old", 3)
            env.done["old"] = {"title": "Legacy", "keywords": [], "model": "claude-haiku", "content_sig": "1:1:old"}
            env.reply = lambda kind, n: [keep(0)]
            env.run("--daily")
        self.assertIn("old user message 2", env.prompts[0])
        self.assertIn("old user message 1", env.prompts[0])
        self.assertNotIn("old user message 0", env.prompts[0])

    def test_unsigned_legacy_label_is_adopted_without_a_call(self):
        with Env() as env:
            env.chat("old", 3)
            env.done["old"] = {"title": "Legacy", "keywords": [], "model": "claude-haiku"}
            env.run("--daily")
        self.assertEqual(env.prompts, [])
        self.assertEqual(env.done["old"]["labelled_n_user"], 3)

    def test_single_turn_chat_labelled_after_idle_day(self):
        with Env() as env:
            env.cfg["first_label_after_user_turns"] = 2
            env.chat("idle", 1, last="2026-01-01T00:00:00Z")
            env.chat("fresh", 1, last="2999-01-01T00:00:00Z")
            env.reply = lambda kind, n: [{"i": 0, "title": "Idle one turn chat", "description": "d", "tags": [], "project": "none"}]
            done = env.run("--daily")
        self.assertFalse(done["idle"].get("provisional"))
        self.assertTrue(done["fresh"]["provisional"])

    def test_hook_mode_never_runs_the_daily_update(self):
        with Env() as env:
            env.chat("grew", 4); labelled(env, "grew", 2)
            env.run("--first", "--limit", "6")
        self.assertEqual(env.prompts, [])


if __name__ == "__main__":
    unittest.main()
