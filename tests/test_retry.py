import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from assistant.config import Config, BEIJING
from assistant.storage import Store
from assistant.ui import App


class RetryTests(unittest.TestCase):
    def test_pending_key_rechecks_and_unrecognized_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            app = object.__new__(App)
            app.store = Store(Path(folder)/"state.db")
            app.config = Config(enabled=True)
            instant = datetime(2026,9,23,18,0,tzinfo=BEIJING)
            rid = app.store.create_run("2026-09-23",app.config.scope,instant.isoformat(),"manual")
            app.store.record_task(rid,"key","金钥匙","waiting_release","expired")
            app.store.finish(rid,instant.isoformat(),"partial","expired","desktop")
            with patch("assistant.ui.now",return_value=instant):
                self.assertEqual(instant+timedelta(minutes=30),app.retry_at())
                self.assertFalse(app.retry_due())
                app.store.record_task(rid,"key","金钥匙","unrecognized","unknown after submit")
                self.assertIsNone(app.retry_at())

    def test_retry_does_not_cross_midnight(self):
        with tempfile.TemporaryDirectory() as folder:
            app = object.__new__(App)
            app.store = Store(Path(folder)/"state.db")
            app.config = Config(enabled=True)
            instant = datetime(2026,9,23,23,45,tzinfo=BEIJING)
            rid = app.store.create_run("2026-09-23",app.config.scope,instant.isoformat(),"manual")
            app.store.record_task(rid,"key","金钥匙","waiting_release","expired")
            app.store.finish(rid,instant.isoformat(),"partial","expired","desktop")
            with patch("assistant.ui.now",return_value=instant):
                self.assertIsNone(app.retry_at())
