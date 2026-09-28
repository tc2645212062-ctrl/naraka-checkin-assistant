import tempfile
import unittest
from pathlib import Path
from assistant.config import Config
from assistant.storage import Store
from assistant.presentation import dashboard


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name)/"test.db")
        self.config = Config()

    def add(self, day, source, task="calendar", status="success", scope=None):
        rid = self.store.create_run(day, scope or self.config.scope, day+"T10:00:00+08:00", "test")
        self.store.record_task(rid, task, task, status, "test evidence")
        self.store.finish(rid, day+"T10:01:00+08:00", "partial", "test", source)
        return rid

    def test_stale_and_other_account_results_do_not_complete_today(self):
        self.add("2026-09-22", "desktop")
        self.add("2026-09-23", "desktop", scope=Config(account="另一个账号").scope)
        view = dashboard(self.store, self.config, "2026-09-23")
        self.assertEqual(0, view["done"])
        self.assertIsNone(view["latest"])

    def test_simulation_is_excluded_from_real_dashboard(self):
        self.add("2026-09-23", "simulation", "key")
        real = dashboard(self.store, self.config, "2026-09-23")
        preview = dashboard(self.store, self.config, "2026-09-23", True)
        self.assertEqual(0, real["done"])
        self.assertEqual(1, preview["done"])
        self.assertFalse(preview["tasks"][-1]["verified"])

    def test_new_failure_replaces_prior_task_success(self):
        self.add("2026-09-23", "desktop", "key")
        self.add("2026-09-23", "desktop", "key", "unrecognized")
        view = dashboard(self.store, self.config, "2026-09-23")
        self.assertEqual(0, view["done"])
        self.assertEqual("unrecognized", view["tasks"][-1]["status"])

    def test_local_skip_preserves_task_evidence_and_manual_is_not_verified(self):
        self.add("2026-09-23", "desktop")
        self.add("2026-09-23", "manual", "key")
        rid = self.store.create_run("2026-09-23", self.config.scope, "2026-09-23T12:00:00+08:00", "manual")
        self.store.finish(rid,"2026-09-23T12:00:00+08:00","already_signed","local skip","local")
        view = dashboard(self.store,self.config,"2026-09-23")
        self.assertEqual(1,view["done"])
        self.assertFalse(view["tasks"][-1]["verified"])
