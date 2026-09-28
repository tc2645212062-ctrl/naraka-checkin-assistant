import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QAbstractAnimation
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from assistant.config import Config, load_config
from assistant.ui import App, Window
from assistant.preview import PreviewAdapter


class QtUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = Window()
        self.app = App(self.window, Path(self.temp.name), Config(), preview=True)
        self.app.timer.stop()
        self.qt.processEvents()

    def tearDown(self):
        if self.app.worker:
            self.app.cancel.set()
            self.app.worker.join(3)
        self.app.force_close = True
        self.window.close()
        self.window.deleteLater()
        self.qt.processEvents()
        self.temp.cleanup()

    def wait_run(self):
        deadline = time.monotonic()+5
        while self.app.busy and time.monotonic() < deadline:
            QTest.qWait(30)
            self.app.tick()
        self.assertFalse(self.app.busy)

    def test_save_validation_preserves_existing_config(self):
        self.app.fields["daily_time"].setText("09:45")
        self.assertTrue(self.app.save())
        self.app.fields["daily_time"].setText("25:99")
        self.assertFalse(self.app.save())
        self.assertEqual("09:45",load_config(Path(self.temp.name)/"config.json").daily_time)

    def test_preview_timer_and_button_cannot_call_desktop(self):
        self.app.config.enabled = True
        self.app.config.daily_time = "00:00"
        with patch.object(self.app.engine,"due",side_effect=AssertionError("preview must not schedule")):
            self.app.tick()
            self.app.run()
        self.assertFalse(self.app.busy)
        self.assertEqual([],self.app.store.history())

    def test_all_four_worker_results_are_labeled_simulation(self):
        for scenario in ("success","already_signed","manual_required","network_error"):
            self.app.config.task_id = scenario
            self.app.adapter_factory = lambda cancel,s=scenario: PreviewAdapter(s,cancel)
            self.app.run()
            self.wait_run()
            record = self.app.store.history(1)[0]
            self.assertEqual(scenario,record["status"])
            self.assertEqual("simulation",record["source"])
            self.assertIn("模拟",self.app.status.text())
            self.assertIn("模拟",self.app.notice_text.text())

    def test_worker_stop_and_duplicate_start(self):
        self.app.adapter_factory = lambda cancel: PreviewAdapter("success",cancel)
        self.app.run()
        first = self.app.worker
        self.app.run()
        self.assertIs(first,self.app.worker)
        QTest.qWait(80)
        self.app.stop()
        self.wait_run()
        self.assertEqual("interrupted",self.app.store.history(1)[0]["status"])
        self.assertIsNone(self.app.store.completion(self.app._day.isoformat(),self.app.config.scope))

    def test_partial_gold_key_is_not_presented_as_verified(self):
        self.app.engine.run(self.app.config,PreviewAdapter("partial"),"simulation")
        self.app.refresh()
        self.assertEqual(5,self.app.view["done"])
        status,_,source = self.app.task_rows["key"]
        self.assertIn("等待有效金钥匙",status.text())
        self.assertEqual("模拟数据",source.text())

    def test_theme_persistence_and_reduced_motion_interrupts_animation(self):
        self.app.system_motion_off = False
        self.app.navigate(1)
        self.assertEqual(QAbstractAnimation.Running,self.app.fade.state())
        self.app.navigate(2)
        self.assertEqual(2,self.app.stack.currentIndex())
        self.app.theme.setCurrentIndex(self.app.theme.findData("dark"))
        self.app.motion.setChecked(True)
        self.assertEqual(QAbstractAnimation.Stopped,self.app.fade.state())
        self.assertEqual(1.0,self.app.effect.opacity())
        prefs = json.loads((Path(self.temp.name)/"ui-settings.json").read_text())
        self.assertEqual({"theme":"dark","reduce_motion":True},prefs)

    def test_log_selection_filter_and_export(self):
        for scenario in ("network_error","success"):
            self.app.config.task_id = scenario
            self.app.engine.run(self.app.config,PreviewAdapter(scenario),"simulation")
        self.app.refresh()
        self.app.log_filter.setCurrentIndex(1)
        self.assertEqual(1,self.app.history.rowCount())
        self.assertIn("网络",self.app.log_text.toPlainText())
        out = Path(self.temp.name)/"导出日志.json"
        with patch("assistant.ui.QFileDialog.getSaveFileName",return_value=(str(out),"JSON")):
            self.app.export()
        self.assertEqual(2,len(json.loads(out.read_text(encoding="utf-8"))["runs"]))

    def test_keyboard_navigation_save_and_minimum_window(self):
        self.window.activateWindow()
        self.window.setFocus()
        QTest.qWait(30)
        QTest.keyClick(self.window,Qt.Key_3,Qt.ControlModifier)
        self.assertEqual(2,self.app.stack.currentIndex())
        self.window.resize(840,600)
        self.qt.processEvents()
        self.assertLessEqual(self.app.stack.width(),self.window.width())
        self.assertGreater(self.app.stack.height(),250)

    def test_real_scheduler_dispatch_with_injected_adapter_is_once_per_day(self):
        self.app.preview = False
        self.app.config.enabled = True
        self.app.config.daily_time = "00:00"
        self.app.adapter_factory = lambda cancel: PreviewAdapter("success",cancel)
        with patch.object(self.app,"notify"):
            self.app.tick()
            self.assertTrue(self.app.busy)
            self.wait_run()
            self.app.tick()
        rows = self.app.store.history()
        self.assertEqual(1,len(rows))
        self.assertEqual("scheduled",rows[0]["trigger"])
        self.assertTrue(self.app.store.schedule_claimed(self.app._day.isoformat(),self.app.config.scope))

    def test_completed_record_without_task_evidence_is_not_claimed_verified(self):
        rid = self.app.store.create_run(self.app._day.isoformat(),self.app.config.scope,"2026-09-23T10:00:00+08:00","test")
        self.app.store.finish(rid,"2026-09-23T10:00:00+08:00","success","old completion","local")
        self.app.refresh()
        self.assertEqual(0,self.app.view["done"])
        self.assertIn("待逐项核对",self.app.status.text())

    def test_both_palettes_have_readable_text_contrast(self):
        from assistant.appearance import LIGHT,DARK
        def luminance(h):
            rgb = [int(h[i:i+2],16)/255 for i in (1,3,5)]
            rgb = [c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4 for c in rgb]
            return sum(c*w for c,w in zip(rgb,(.2126,.7152,.0722)))
        for palette in (LIGHT,DARK):
            for key in ("ink","muted","gold","green","red","amber"):
                low,high = sorted([luminance(palette[key]),luminance(palette["panel"])])
                self.assertGreaterEqual((high+.05)/(low+.05),4.5)
