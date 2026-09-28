from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from assistant.config import Config
from assistant.desktop.automation import DesktopAdapter, StopAutomation
from assistant.desktop.models import OcrItem, WindowInfo
from assistant.desktop.recognition import Calendar, calendar, read_image
from assistant.desktop.vision import read_text

FIXTURES = Path(__file__).parent/"fixtures"


class RecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signed = read_image(FIXTURES/"signed-calendar.png")
        cls.unsigned = read_image(FIXTURES/"unsigned-calendar.png")
        cls.signed_items = read_text(FIXTURES/"signed-calendar.png")
        cls.unsigned_items = read_text(FIXTURES/"unsigned-calendar.png")

    def test_real_signed_calendar(self):
        self.assertEqual("already_signed", calendar(self.signed, self.signed_items, date(2026, 9, 22)).status)

    def test_real_unsigned_calendar(self):
        self.assertEqual("not_signed", calendar(self.unsigned, self.unsigned_items, date(2026, 9, 16)).status)

    def test_darkening_unsigned_does_not_claim_success(self):
        dark = np.clip(self.unsigned.astype(float)*.35, 0, 255).astype("uint8")
        self.assertEqual("not_signed", calendar(dark, self.unsigned_items, date(2026, 9, 16)).status)

    def test_missing_or_ambiguous_date_stops(self):
        with self.assertRaises(ValueError):
            calendar(self.signed, self.signed_items, date(2026, 10, 22))
        duplicate = next(i for i in self.signed_items if i.text == "9.22")
        with self.assertRaises(ValueError):
            calendar(self.signed, self.signed_items+[duplicate], date(2026, 9, 22))

    def test_blank_icon_cannot_become_already_signed(self):
        with self.assertRaises(ValueError):
            calendar(np.zeros_like(self.signed), self.signed_items, date(2026, 9, 22))

    def test_scaled_calendar(self):
        factor = 1.25
        scaled = cv2.resize(self.signed, None, fx=factor, fy=factor)
        items = [OcrItem(i.text, i.confidence, tuple((x*factor,y*factor) for x,y in i.polygon)) for i in self.signed_items]
        self.assertEqual("already_signed", calendar(scaled, items, date(2026, 9, 22)).status)


class ActionTests(unittest.TestCase):
    def setUp(self):
        # These are simulated actions; their result must not depend on a real
        # UAC prompt or lock screen appearing on the machine running the tests.
        desktop = patch("assistant.desktop.automation.win.desktop_available", return_value=True)
        desktop.start()
        self.addCleanup(desktop.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.adapter = DesktopAdapter(Path(self.temp.name))
        self.adapter.config = Config()
        self.adapter.assert_game = lambda snap: None  # Platform guards have separate real-frame tests.
        self.adapter.window = WindowInfo(1, 1, "永劫无间", "Chrome", True, False, (0,0,642,1180))
        self.snap = (self.adapter.window, FIXTURES/"signed-calendar.png", [])
        self.adapter.snapshot = lambda *args: self.snap

    def test_sign_verifies_transition_with_one_click(self):
        before = Calendar("not_signed", (100,100), 22, "test")
        after = Calendar("already_signed", (100,100), 23, "test")
        with patch("assistant.desktop.automation.calendar", side_effect=[before,after]), patch("assistant.desktop.automation.win.click_window_point") as click, patch("assistant.desktop.automation.time.sleep"):
            result = self.adapter.sign_calendar(Config())
        self.assertEqual("success", result.status)
        self.assertTrue(result.verified)
        click.assert_called_once()

    def test_recheck_avoids_double_click(self):
        with patch("assistant.desktop.automation.calendar", return_value=Calendar("already_signed", (100,100), 22, "test")), patch("assistant.desktop.automation.win.click_window_point") as click:
            result = self.adapter.sign_calendar(Config())
        self.assertEqual("already_signed", result.status)
        click.assert_not_called()

    def test_no_evidence_no_blind_retry(self):
        with patch("assistant.desktop.automation.calendar", return_value=Calendar("not_signed", (100,100), 22, "test")), patch("assistant.desktop.automation.win.click_window_point") as click, patch("assistant.desktop.automation.time.sleep"):
            result = self.adapter.sign_calendar(Config())
        self.assertEqual("unrecognized", result.status)
        click.assert_called_once()

    def test_cancel_prevents_input(self):
        self.adapter.cancel = threading.Event()
        self.adapter.cancel.set()
        with self.assertRaises(StopAutomation):
            self.adapter.guard()

    def test_unavailable_desktop_prevents_input(self):
        before = Calendar("not_signed", (100,100), 22, "test")
        with patch("assistant.desktop.automation.win.desktop_available", return_value=False), patch("assistant.desktop.automation.calendar", return_value=before), patch("assistant.desktop.automation.win.click_window_point") as click:
            result = self.adapter.sign_calendar(Config())
        self.assertEqual("desktop_busy", result.status)
        click.assert_not_called()

    def test_success_popup_with_punctuation_is_acknowledged(self):
        before = Calendar("not_signed", (100,100), 22, "test")
        after = Calendar("already_signed", (100,100), 23, "test")
        items = [OcrItem(t, .99, ((0,0),(20,0),(20,20),(0,20))) for t in ("签到成功！", "太棒了")]
        snapshots = iter([self.snap, (self.adapter.window,self.snap[1],items), self.snap])
        self.adapter.snapshot = lambda *args: next(snapshots)
        with patch("assistant.desktop.automation.calendar", side_effect=[before,after]), patch("assistant.desktop.automation.win.click_window_point"), patch("assistant.desktop.automation.time.sleep"), patch.object(self.adapter,"text_click") as dismiss:
            result = self.adapter.sign_calendar(Config())
        self.assertEqual("success", result.status)
        dismiss.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class MiniEntryRecoveryTests(unittest.TestCase):
    def test_already_on_discover_uses_visible_miniprogram_entry(self):
        from unittest.mock import Mock
        def item(text,y):
            return OcrItem(text,.99,((100,y),(220,y),(220,y+20),(100,y+20)))
        main=WindowInfo(10,20,'微信','Qt',True,False,(0,0,1482,1127))
        discover=(main,Path('unused'),[item('发现',50),item('朋友圈',100),item('小程序',300)])
        ready=(main,Path('unused'),[item('最近使用',100),item('永劫无间',200),item('我的常用',400)])
        with tempfile.TemporaryDirectory() as folder, patch('assistant.desktop.automation.time.sleep'):
            a=DesktopAdapter(Path(folder));a.config=Config();a.existing_mini=lambda:None
            a.ensure_main=lambda:main;a.mini_list_snapshot=Mock(side_effect=[discover,ready]);a.text_click=Mock();a.click=Mock();a.wait_mini=lambda:main
            self.assertEqual(main,a.open_mini())
            a.text_click.assert_called_once_with(discover,'小程序')
            a.click.assert_called_once_with(ready,ready[2][1])

    def test_list_can_be_in_separate_chrome_window(self):
        from unittest.mock import Mock
        main=WindowInfo(10,20,'微信','Qt',True,False,(0,0,1482,1127))
        child=WindowInfo(11,21,'微信','Chrome_WidgetWin_0',True,True,(-32000,-32000,-31763,-31961))
        items=[OcrItem(t,.99,((0,0),(50,0),(50,20),(0,20))) for t in ('最近使用','我的常用')]
        with tempfile.TemporaryDirectory() as folder, patch('assistant.desktop.automation.win.enumerate_wechat_windows',return_value=[main,child]):
            a=DesktopAdapter(Path(folder));page=(child,Path('unused'),items);a.snapshot=Mock(return_value=page)
            self.assertEqual(page,a.mini_list_snapshot(main))
            a.snapshot.assert_called_once_with(child,'mini-list-window',inspect_only=True)

    def test_unrelated_article_is_not_treated_as_miniprogram_verification(self):
        from unittest.mock import Mock
        main=WindowInfo(10,20,'微信','Qt',True,False,(0,0,1482,1127))
        article=WindowInfo(11,21,'微信','Chrome_WidgetWin_0',True,False,(0,0,960,960))
        items=[OcrItem('扫码登录',.99,((0,0),(50,0),(50,20),(0,20)))]
        with tempfile.TemporaryDirectory() as folder, patch('assistant.desktop.automation.win.enumerate_wechat_windows',return_value=[main,article]):
            a=DesktopAdapter(Path(folder));page=(article,Path('unused'),items);home=(main,Path('unused'),[])
            a.snapshot=Mock(side_effect=[page,home]);a.check_page=Mock()
            self.assertEqual(home,a.mini_list_snapshot(main))
            a.check_page.assert_not_called()

    def test_unresponsive_chat_recovers_once_before_opening_mini(self):
        from unittest.mock import Mock
        main=WindowInfo(10,20,'微信','Qt',True,False,(0,0,1482,1127))
        search=OcrItem('Q 搜索',.99,((100,50),(150,50),(150,80),(100,80)))
        page=(main,Path('unused'),[search])
        with tempfile.TemporaryDirectory() as folder, patch('assistant.desktop.automation.time.sleep'), patch('assistant.desktop.automation.read_image',return_value=np.zeros((500,500,3))), patch('assistant.desktop.automation.match',return_value=(.99,(55,390))), patch('assistant.desktop.automation.win.click_window_point'):
            a=DesktopAdapter(Path(folder));a.config=Config();a.existing_mini=Mock(side_effect=[None,main]);a.ensure_main=lambda:main
            a.mini_list_snapshot=lambda _:page;a.snapshot=Mock(return_value=page);a.recover_wechat=Mock()
            self.assertEqual(main,a.open_mini());a.recover_wechat.assert_called_once_with(main)
