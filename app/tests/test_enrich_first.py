import json
import unittest

import enrich
from enrich_fixture import Env


def label(i, project="Alpha", title="A specific four word title"):
    return {"i": i, "title": title, "description": "One sentence.", "tags": ["a", "B", "c"], "project": project}


class FirstLabelTests(unittest.TestCase):
    def test_one_turn_chat_gets_model_title(self):
        with Env() as env:
            env.chat("one", 1)
            env.reply = lambda kind, n: [label(0)]
            done = env.run("--first")
        self.assertEqual(len(env.prompts), 1)
        self.assertFalse(done["one"].get("provisional"))
        self.assertEqual(done["one"]["title"], "A specific four word title")

    def test_two_turns_label_in_one_batched_call_from_first_turns_only(self):
        with Env() as env:
            env.chat("a", 2); env.chat("b", 5); env.chat("c", 1)
            env.reply = lambda kind, n: [label(i, "Alpha" if i == 0 else "none") for i in range(n)]
            done = env.run("--first")
        self.assertEqual(len(env.prompts), 1)
        prompt = env.prompts[0]
        self.assertNotIn("b user message 4", prompt)   # chat b has 5 turns; only the first 2 are sent
        self.assertIn("b user message 1", prompt)
        self.assertIn("c user message", prompt)      # one turn: not yet labelled
        self.assertLessEqual(max(len(s) for s in prompt.split("\n\n[")[1:]), 2600)
        self.assertEqual(sorted(k for k, v in done.items() if not v.get("provisional")), ["a", "b", "c"])
        rec = done["a"]
        self.assertEqual(rec["keywords"], ["a", "b", "c"])
        self.assertEqual(rec["labelled_through_turn"], 4)
        self.assertEqual(rec["labelled_n_user"], 2)
        self.assertEqual(env.proj["assignments"]["a"], "alpha")
        self.assertNotIn("b", env.proj["assignments"])    # "none" places nothing

    def test_project_choices_exclude_workspace_projects_and_rules_ride_along(self):
        with Env() as env:
            env.cfg["project_rules"] = ["Alpha is everything for the fund."]
            env.chat("a", 2)
            env.reply = lambda kind, n: [label(0)]
            env.run("--first")
        self.assertIn("- Alpha", env.prompts[0])
        self.assertNotIn("wsdir", env.prompts[0])
        self.assertIn("Alpha is everything for the fund.", env.prompts[0])

    def test_manual_and_existing_placements_are_never_overridden(self):
        with Env() as env:
            for k in ("m", "s", "e"):
                env.chat(k, 2)
            env.proj["assignments"] = {"m": "beta", "s": "workspace-1", "e": "beta"}
            env.proj["manual_assignments"] = ["m"]
            env.proj["workspace_seeds"] = {"s": "workspace-1"}
            env.reply = lambda kind, n: [label(i) for i in range(n)]
            env.run("--first")
            sugg = env.load("sugg.json")
        a = env.proj["assignments"]
        self.assertEqual((a["m"], a["s"], a["e"]), ("beta", "alpha", "beta"))
        self.assertNotIn("s", env.proj["workspace_seeds"])
        self.assertEqual(list(sugg), ["e"])   # assigned but not manual: a suggestion, not a move

    def test_limit_caps_hook_runs_newest_first(self):
        with Env() as env:
            for i in range(5):
                env.chat(f"c{i}", 2, last=f"2026-11-15T1{i}:00:00Z")
            env.reply = lambda kind, n: [label(i) for i in range(n)]
            done = env.run("--first", "--limit", "2")
        self.assertEqual(sorted(k for k, v in done.items() if not v.get("provisional")), ["c3", "c4"])

    def test_failed_batch_keeps_existing_label_then_settles_locally_after_three_tries(self):
        with Env() as env:
            env.chat("a", 2)
            env.done = {"a": {"title": "Old good title", "error": "boom"}}
            env.reply = lambda kind, n: []           # model answers with an empty array
            for _ in range(2):
                env.run("--first")
            self.assertNotIn("fallback", env.done["a"])
            env.run("--first")
        self.assertTrue(env.done["a"]["fallback"])
        self.assertEqual(len(env.prompts), 3)

    def test_refines_once_at_four_prompts_without_assistant_text(self):
        with Env() as env:
            env.chat("a", 1)
            env.reply = lambda kind, n: [label(0)]
            env.run("--first")
            env.chat("a", 4)
            env.run("--first")
            env.chat("a", 5)
            env.run("--first")
        self.assertEqual(len(env.prompts), 2)
        self.assertIn("a user message 3", env.prompts[1])
        self.assertNotIn("a answer", "".join(env.prompts))

    def test_force_keys_labels_only_those_keys(self):
        with Env() as env:
            env.chat("a", 2); env.chat("b", 2)
            env.reply = lambda kind, n: [label(i) for i in range(n)]
            done = env.run("--force-keys", "b")
        self.assertFalse(done["b"].get("provisional"))
        self.assertTrue(done["a"]["provisional"])
        self.assertEqual(env.prompts[0].count("user message 0"), 1)

    def test_manual_title_flag_is_untouched_by_labelling(self):
        with Env() as env:
            env.chat("a", 2)
            env.flags = {"titles": {"a": "My own name"}}
            env.reply = lambda kind, n: [label(0)]
            env.run("--first")
            self.assertEqual(env.load("flags.json")["titles"]["a"], "My own name")


if __name__ == "__main__":
    unittest.main()
