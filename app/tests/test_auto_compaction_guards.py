"""Synthetic auto-compaction regression tests."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import broker

class AutoCompactionGuards(unittest.TestCase):
    def setUp(self):
        timer = patch.object(broker.threading, "Timer")
        timer.start()
        self.addCleanup(timer.stop)
        self.b = object.__new__(broker.Broker)
        self.b.store = Mock()
        self.b.store.get.return_value = {"cwd": "/tmp"}
        self.b.policy = lambda cwd="": {"auto_compact": True, "auto_compact_threshold": .5}
        self.b._send = Mock()
        self.b._deliver_next = Mock()
        self.run = {"provider": "claude", "operation": "message", "session_id": "s", "cwd": "/tmp"}
    def ctrl(self, **kw):
        return {"protocol": "claude-stream", "context": {"tokens": 600, "window": 1000, "model": "m"}, **kw}
    def test_pending_child_and_requests_defer_compaction(self):
        for field in ("background", "pending_requests"):
            with self.subTest(field=field):
                ctrl = self.ctrl(**{field: {"x": {"kind": "approval"}}})
                self.b._after_turn("r", self.run, ctrl)
                self.b._send.assert_not_called()
    def test_new_model_invalidates_old_window(self):
        ctrl = self.ctrl()
        self.b._track_context("r", ctrl, model="new", tokens=600)
        self.assertNotIn("window", ctrl["context"])
        self.b._after_turn("r", self.run, ctrl)
        self.b._send.assert_not_called()
    def test_unconfirmed_claude_compaction_blocks_queued_message(self):
        ctrl = self.ctrl(auto_compacting=True, compact_spent=True)
        self.b._after_turn("r", self.run, ctrl)
        self.b._deliver_next.assert_not_called()
        self.assertTrue(any(call.args[1] == "control_error" for call in self.b.store.emit.call_args_list))


    def test_failed_or_interrupted_codex_turn_does_not_compact(self):
        for status in ("failed", "interrupted"):
            with self.subTest(status=status):
                self.b._after_turn = Mock()
                run = {"provider": "codex", "session_id": "root"}
                ctrl = {"pending_rpc": {}, "background": {}, "protocol": "app-server"}
                state = broker.stream._State("root")
                self.b._codex_rpc("r", run, state, ctrl, {"method": "turn/completed", "params": {"threadId": "root", "turn": {"status": status}}})
                self.b._after_turn.assert_not_called()
    def test_confirmed_claude_compaction_releases_queue(self):
        ctrl = self.ctrl(auto_compacting=True, compact_spent=True)
        self.b._auto_compacted("r", self.run, ctrl, post_tokens=100)
        self.b._after_turn("r", self.run, ctrl)
        self.b._deliver_next.assert_called_once()
        self.assertTrue(any(c.args[1] == "compacted" for c in self.b.store.emit.call_args_list))


    def test_deferred_compaction_resumes_after_child_or_request_clears(self):
        for field in ("background", "pending_requests"):
            with self.subTest(field=field):
                self.b._send.reset_mock()
                ctrl = self.ctrl(**{field: {"x": {"kind": "approval"}}})
                self.b._after_turn("r", self.run, ctrl)
                self.b._send.assert_not_called()
                ctrl[field] = {}
                self.b._settle("r", ctrl)
                self.b._send.assert_called_once()


    def test_new_usage_without_current_window_cannot_reuse_old_window(self):
        ctrl = self.ctrl()
        self.b._track_context("r", ctrl, model="m", tokens=700)
        self.assertNotIn("window", ctrl["context"])
        self.assertNotIn("ratio", ctrl["context"])
        self.b._after_turn("r", self.run, ctrl)
        self.b._send.assert_not_called()
        saved = self.b.store.update.call_args.kwargs["context"]
        self.assertNotIn("window", saved)


    def test_queue_cannot_bypass_compaction_holds(self):
        for field in ("background", "pending_requests", "compact_failed"):
            with self.subTest(field=field):
                ctrl = self.ctrl(initialized=True, **{field: True})
                self.assertFalse(self.b._between_turns(ctrl))
    def test_codex_confirmation_does_not_bypass_pending_work(self):
        ctrl = {"protocol": "app-server", "auto_compacting": True, "background": {"child": "task"}, "context": {"tokens": 600, "window": 1000}}
        self.b._auto_compacted("r", {"provider": "codex", "session_id": "s"}, ctrl)
        self.b._deliver_next.assert_not_called()


    def test_late_pending_request_prevents_idle_close(self):
        import threading
        ctrl = {"control_lock": threading.RLock(), "idle_timer": threading.current_thread(), "pending_requests": {"x": {"kind": "approval"}}, "protocol": "app-server"}
        self.b._signal = Mock()
        self.b._close_idle("r", ctrl)
        self.b._signal.assert_not_called()
        self.assertFalse(ctrl.get("closing", False))
    def test_new_model_rearms_threshold(self):
        ctrl = self.ctrl(compact_spent=True)
        self.b._track_context("r", ctrl, model="new", tokens=600, window=1000)
        self.b._after_turn("r", self.run, ctrl)
        self.b._send.assert_called_once()


    def test_codex_compaction_receipt_and_confirmation_both_orders(self):
        for first in ("receipt", "confirmation"):
            with self.subTest(first=first):
                self.b._deliver_next.reset_mock()
                run = {"provider": "codex", "session_id": "root", "cwd": "/tmp"}
                self.b.store.get.return_value = run
                ctrl = {"protocol": "app-server", "auto_compacting": True, "compact_spent": True, "pending_rpc": {"100": {"kind": "auto_compact"}}, "context": {"tokens": 600, "window": 1000}}
                state = broker.stream._State("root")
                receipt = {"id": 100, "result": {}}
                confirmation = {"method": "thread/compacted", "params": {"threadId": "root"}}
                for event in ((receipt, confirmation) if first == "receipt" else (confirmation, receipt)):
                    self.b._codex_rpc("r", run, state, ctrl, event)
                    self.b._settle("r", ctrl)
                self.b._deliver_next.assert_called_once()


    def test_compaction_waits_for_native_goal_startup(self):
        for field in ("goal_get_pending", "goal_set_pending", "turn_open"):
            with self.subTest(field=field):
                self.b._send.reset_mock()
                ctrl = self.ctrl(**{field: True})
                self.b._after_turn("r", self.run, ctrl)
                self.b._send.assert_not_called()
    def test_pending_compaction_receipt_prevents_idle_close(self):
        import threading
        ctrl = {"control_lock": threading.RLock(), "idle_timer": threading.current_thread(), "pending_rpc": {"100": {"kind": "auto_compact"}}, "protocol": "app-server"}
        self.b._signal = Mock()
        self.b._close_idle("r", ctrl)
        self.b._signal.assert_not_called()


    def test_explicit_replayed_recovery_requires_successful_root_result(self):
        ctrl = self.ctrl(compact_failed=True, compact_spent=True, claude_inputs=[{"wire": "retry", "message": "retry", "attachments": [], "results": 0, "recover_compact": True}])
        self.b._claude_lifecycle("r", ctrl, {"type": "user", "isReplay": True, "message": {"content": "retry"}})
        self.assertTrue(ctrl["compact_failed"])
        self.b._claude_compact_recovery(ctrl, {"type": "result", "origin": {"kind": "task-notification"}}, None)
        self.assertTrue(ctrl["compact_failed"])
        self.b._claude_compact_recovery(ctrl, {"type": "result"}, "provider error")
        self.assertTrue(ctrl["compact_failed"])
        ctrl["compact_recovery_replayed"] = True
        self.b._claude_compact_recovery(ctrl, {"type": "result"}, None)
        self.assertFalse(ctrl.get("compact_failed", False))
        self.assertTrue(ctrl["compact_spent"])
    def test_unreplayed_recovery_does_not_clear_failure(self):
        ctrl = self.ctrl(compact_failed=True, compact_spent=True)
        self.b._claude_compact_recovery(ctrl, {"type": "result"}, None)
        self.assertTrue(ctrl["compact_failed"])


    def test_explicit_steer_is_marked_for_recovery_before_native_replay(self):
        import threading
        run = {**self.run, "status": "running"}
        self.b.store.get.return_value = run
        self.b.lock = threading.RLock()
        ctrl = self.ctrl(compact_failed=True, compact_spent=True, initialized=True, turn_open=False)
        self.b.active = {"r": ctrl}
        self.b.control("r", {"action": "steer", "message": "continue"})
        self.assertTrue(ctrl["claude_inputs"][0]["recover_compact"])
        self.b._claude_lifecycle("r", ctrl, {"type": "user", "isReplay": True, "message": {"content": "continue"}})
        self.b._claude_compact_recovery(ctrl, {"type": "result"}, None)
        self.assertFalse(ctrl.get("compact_failed", False))
        self.assertTrue(ctrl["compact_spent"])

if __name__ == "__main__": unittest.main()
