from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from assistant.adapters import AssistAdapter, MockAdapter, Result
from assistant.config import BEIJING, Config, load_config, save_config
from assistant.engine import Engine
from assistant.storage import Store


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = Store(self.folder/"test.db")
        self.time = datetime(2026, 9, 22, 20, 1, tzinfo=BEIJING)
        self.engine = Engine(self.store, lambda: self.time)
        self.config = Config(enabled=True, daily_time="20:00")

    def test_four_required_scenarios(self):
        for state in ("success", "already_signed", "manual_required", "network_error"):
            with self.subTest(state=state):
                cfg = replace(self.config, task_id=state)
                adapter = MockAdapter(state)
                record = self.engine.run(cfg, adapter)
                self.assertEqual(state, record["status"])
                self.assertEqual(adapter.sign_calls, int(state == "success"))
                self.assertEqual(bool(self.store.completion("2026-09-22", cfg.scope)), state in ("success", "already_signed"))
                self.assertTrue(self.store.events(record["id"]))

    def test_duplicate_is_persistent_and_never_clicks(self):
        self.engine.run(self.config, MockAdapter("success"))
        restarted = Engine(Store(self.store.path), lambda: self.time)
        adapter = MockAdapter("success")
        self.assertEqual("already_signed", restarted.run(self.config, adapter)["status"])
        self.assertEqual(0, adapter.check_calls)
        self.assertEqual(0, adapter.sign_calls)

    def test_account_game_task_and_day_are_isolated(self):
        self.engine.run(self.config, MockAdapter("success"))
        for cfg in (replace(self.config, account="另一个账号"), replace(self.config, game="手游"), replace(self.config, task_id="new-event")):
            self.assertEqual("success", self.engine.run(cfg, MockAdapter("success"))["status"])
        self.time += timedelta(days=1)
        self.assertEqual("success", self.engine.run(self.config, MockAdapter("success"))["status"])

    def test_unverified_success_is_never_stored(self):
        class Untrusted:
            def check(self, config):
                return Result("success", "I clicked a button")
        result = self.engine.run(self.config, Untrusted())
        self.assertEqual("unrecognized", result["status"])
        self.assertIsNone(self.store.completion("2026-09-22", self.config.scope))

    def test_unverified_not_signed_does_not_act(self):
        class Untrusted:
            def check(self, config):
                return Result("not_signed", "unknown")
            def sign(self, config):
                raise AssertionError("Must not click")
        self.assertEqual("unrecognized", self.engine.run(self.config, Untrusted())["status"])

    def test_opening_wechat_does_not_equal_success(self):
        adapter = AssistAdapter(lambda _: (True, "opened"))
        self.assertEqual("awaiting_confirmation", self.engine.run(self.config, adapter)["status"])

    def test_expired_activity_no_desktop_action(self):
        adapter = MockAdapter("success")
        result = self.engine.run(replace(self.config, end_date="2026-09-21"), adapter)
        self.assertEqual("activity_missing", result["status"])
        self.assertEqual(0, adapter.check_calls)

    def test_schedule_pause_before_time_catchup_and_restart(self):
        self.assertFalse(self.engine.due(replace(self.config, enabled=False)))
        self.assertFalse(self.engine.due(replace(self.config, daily_time="21:00")))
        self.assertTrue(self.engine.due(self.config))
        self.assertFalse(Engine(Store(self.store.path), lambda: self.time).due(self.config))
        self.time += timedelta(days=1)
        self.assertTrue(self.engine.due(self.config))

    def test_missing_network_is_not_success(self):
        class Offline:
            def check(self, config):
                raise TimeoutError("offline")
        self.assertEqual("network_error", self.engine.run(self.config, Offline())["status"])

    def test_unexpected_exception_persists_reason(self):
        class Broken:
            def check(self, config):
                raise ValueError("broken")
        record = self.engine.run(self.config, Broken())
        self.assertEqual("error", record["status"])
        self.assertIn("broken", record["reason"])

    def test_crash_recovery_does_not_complete(self):
        rid = self.store.create_run("2026-09-22", self.config.scope, self.time.isoformat(), "scheduled")
        self.store.recover(self.time.isoformat())
        self.assertEqual("interrupted", self.store.get_run(rid)["status"])
        self.assertFalse(self.store.completion("2026-09-22", self.config.scope))

    def test_crossover_midnight_requires_new_evidence(self):
        outer = self
        class Slow:
            def check(self, config):
                outer.time += timedelta(days=1)
                return Result("success", "after midnight", "desktop", True)
        record = self.engine.run(self.config, Slow())
        self.assertNotEqual("success", record["status"])

    def test_config_validation_and_atomic_roundtrip(self):
        path = self.folder/"config.json"
        save_config(path, self.config)
        self.assertEqual(load_config(path), self.config)
        for changes in ({"daily_time": "25:00"}, {"start_date": "2026-02-30"}, {"game": "other"}, {"enabled": "true"}, {"idle_seconds": -1}):
            with self.assertRaises(ValueError):
                replace(self.config, **changes).validate()

    def test_corrupt_config_preserved(self):
        path = self.folder/"config.json"
        path.write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_config(path)
        self.assertEqual("broken", path.read_text())

    def test_concurrent_run_rejected(self):
        self.engine.lock.acquire()
        try:
            with self.assertRaises(RuntimeError):
                self.engine.run(self.config, MockAdapter("success"))
        finally:
            self.engine.lock.release()

    def test_export_contains_result_and_evidence_events(self):
        self.engine.run(self.config, MockAdapter("success"))
        path = self.folder/"export.json"
        self.store.export(path)
        import json
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("success", payload["runs"][0]["status"])
        self.assertTrue(payload["events"])


if __name__ == "__main__":
    unittest.main()
