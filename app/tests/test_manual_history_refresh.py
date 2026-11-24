import threading
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import Mock, patch

import remote_sync
import server


class ManualHistoryRefreshTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(server, "_HISTORY_REFRESH_JOBS", OrderedDict(), create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, path, body=None):
        handler = SimpleNamespace(path=path, _json=lambda value, status=200: (status, value))
        status, result = server.Handler._post(handler, body or {})
        self.assertEqual(status, 200)
        return result

    def test_local_index_runs_first_then_imports_remote_changes_only_once(self):
        worker = Mock()
        with patch.object(server.threading, "Thread", worker):
            first = self.post("/api/sync", {"include_remote": True})
            second = self.post("/api/sync", {"include_remote": True})
        self.assertTrue(first["started"])
        self.assertFalse(second["started"])
        self.assertEqual(first["refresh"]["id"], second["refresh"]["id"])
        self.assertEqual(worker.call_count, 1)
        job_id = first["refresh"]["id"]
        order = Mock()
        def remote(**_):
            self.assertTrue(server.history_refresh_status(job_id)["local_ready"])
            return {"changed": 1, "pipeline": None, "sources": [{"name": "sc", "last_error": ""}]}
        order.sync.side_effect = remote
        with patch.object(remote_sync, "sync", order.sync), patch.object(server.subprocess, "run", order.run):
            args = worker.call_args.kwargs
            args["target"](*args["args"])
        self.assertEqual([call[0] for call in order.mock_calls], ["run", "sync", "run"])
        for command in order.run.call_args_list:
            self.assertIn("--parse-only", command.args[0])
            self.assertNotIn("--uncapped", command.args[0])
        completed = server.history_refresh_status(job_id)
        self.assertTrue(completed["remote_indexed"])
        self.assertFalse(completed["running"])
        self.assertFalse(completed["errors"])

    def test_remote_failure_is_reported_and_does_not_undo_a_local_refresh(self):
        worker = Mock()
        with patch.object(server.threading, "Thread", worker):
            response = self.post("/api/sync", {"include_remote": True})
        with patch.object(remote_sync, "sync", return_value={
            "changed": 0, "sources": [{"name": "sc", "last_error": "offline"}]
        }), patch.object(server.subprocess, "run") as run:
            args = worker.call_args.kwargs
            args["target"](*args["args"])
        run.assert_called_once()
        completed = server.history_refresh_status(response["refresh"]["id"])
        self.assertTrue(completed["local_ready"])
        self.assertFalse(completed["running"])
        self.assertIn("offline", completed["errors"][0])

    def test_full_rescan_coalesces_with_running_job_and_keeps_full_indexing(self):
        worker = Mock()
        with patch.object(server.threading, "Thread", worker):
            first = self.post("/api/sync", {"include_remote": True})
            second = self.post("/api/rescan")
        self.assertEqual(first["refresh"]["id"], second["refresh"]["id"])
        self.assertFalse(second["started"])
        with patch.object(remote_sync, "sync", return_value={"changed": 0}), \
                patch.object(server.subprocess, "run") as run:
            args = worker.call_args.kwargs
            args["target"](*args["args"])
        self.assertEqual(run.call_count, 2)
        self.assertIn("--parse-only", run.call_args_list[0].args[0])
        self.assertIn("--uncapped", run.call_args_list[1].args[0])

    def test_client_open_stays_lightweight(self):
        with patch.object(server, "light_sync", return_value=False) as local:
            self.assertEqual(self.post("/api/sync"), {"ok": True, "started": False})
        local.assert_called_once_with("client-open")

    def test_manual_remote_refresh_joins_existing_transfer(self):
        ready, release = threading.Event(), threading.Event()
        config = {"remote_sources": [{"name": "sc", "days": 30}]}
        def pull(_):
            ready.set()
            self.assertTrue(release.wait(2))
            return {"changed": 2}
        results = []
        with patch.object(remote_sync, "_config", return_value=config), \
                patch.object(remote_sync, "sync_source", side_effect=pull) as source, \
                patch.object(remote_sync, "_run_pipeline", return_value=True):
            background = threading.Thread(target=lambda: results.append(remote_sync.sync()))
            background.start()
            self.assertTrue(ready.wait(2))
            job = getattr(remote_sync, "_active_sync", None)
            self.assertIsNotNone(job)
            waiting = threading.Event()
            original_wait = job["done"].wait
            def confirm_join():
                waiting.set()
                return original_wait(2)
            try:
                with patch.object(job["done"], "wait", side_effect=confirm_join):
                    manual = threading.Thread(target=lambda: results.append(remote_sync.sync(run_pipeline=False, wait=True)))
                    manual.start()
                    self.assertTrue(waiting.wait(2))
                    release.set()
                    manual.join(2)
                self.assertFalse(manual.is_alive())
            finally:
                release.set()
                background.join(2)
        source.assert_called_once_with({"name": "sc", "days": 30})
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertTrue(results[0]["pipeline"])


if __name__ == "__main__":
    unittest.main()
