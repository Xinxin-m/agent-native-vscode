"""Shared fixture for the labelling tests: a throwaway data dir and a fake model."""
import contextlib
import io
import json
import os
import tempfile
from unittest.mock import patch

import enrich
import label_model


def turns_of(n_user, tag="t"):
    out = []
    for i in range(n_user):
        out.append({"role": "user", "text": f"{tag} user message {i} " + "x" * 50, "ts": "2026-11-15T10:0%d:00Z" % i})
        out.append({"role": "assistant", "channel": "final_answer", "text": f"{tag} answer {i}", "ts": "2026-11-15T10:0%d:30Z" % i})
    return out


class Env:
    """with Env() as env: env.chat('a', 2); env.run('--first'); env.prompts"""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        os.makedirs(os.path.join(t, "chats"))
        self.index, self.done, self.prompts = {}, {}, []
        self.proj = {"projects": [{"id": "alpha", "name": "Alpha"}, {"id": "beta", "name": "Beta"},
                                  {"id": "workspace-1", "name": "wsdir", "grouping_source": "workspace"}],
                     "assignments": {}, "workspace_seeds": {}, "manual_assignments": []}
        self.flags = {}
        self.reply = lambda kind, n: []
        self.patches = [patch.object(enrich, n, os.path.join(t, f)) for n, f in
                        [("INDEX", "index.json"), ("CHATS", "chats"), ("OUT", "enrich.json"), ("PROJECTS", "projects.json"),
                         ("FLAGS", "flags.json"), ("SUGGESTIONS", "sugg.json"), ("STATE", "state.json")]]
        self.patches += [patch.object(label_model, "BACKOFF_FILE", os.path.join(t, "backoff.json")),
                         patch.object(label_model, "ask", self._ask),
                         patch.object(enrich, "_config", lambda: self.cfg),
                         patch.object(enrich.subprocess, "run", side_effect=AssertionError("provider called directly"))]
        self.cfg = {"user_context": "ctx", "misc_project": "Misc"}
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _ask(self, prompt, cfg, timeout=0):
        self.prompts.append(prompt)
        kind = "daily" if prompt.startswith("Update labels") else "first"
        n = len([l for l in prompt.splitlines() if l.startswith("[") and l.endswith("]") and l[1:-1].isdigit()])
        return json.dumps(self.reply(kind, n)), "claude"

    def p(self, name):
        return os.path.join(self.tmp.name, name)

    def chat(self, key, n_user, last="2026-11-15T12:00:00Z", **meta):
        turns = turns_of(n_user, key)
        with open(os.path.join(self.p("chats"), key + ".json"), "w") as fh:
            json.dump({"key": key, "cwd": "/w/" + key, "turns": turns}, fh)
        self.index[key] = dict({"key": key, "cwd": "/w/" + key, "n_user": n_user, "n_answer": n_user, "ts_last": last,
                                "content_sig": f"{n_user}:{n_user}:{last}", "first_prompt": f"{key} user message 0"}, **meta)
        return turns

    def run(self, *args):
        for name, obj in (("index.json", self.index), ("projects.json", self.proj), ("flags.json", self.flags)):
            with open(self.p(name), "w") as fh:
                json.dump(obj, fh)
        if self.done:
            with open(self.p("enrich.json"), "w") as fh:
                json.dump(self.done, fh)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            enrich.main(list(args))
        self.out = out.getvalue()
        self.done = self.load("enrich.json") or {}
        self.proj = self.load("projects.json") or self.proj
        return self.done

    def load(self, name):
        try:
            with open(self.p(name)) as fh:
                return json.load(fh)
        except OSError:
            return None
