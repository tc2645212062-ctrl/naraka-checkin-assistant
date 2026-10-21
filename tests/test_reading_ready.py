import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from assistant.desktop.automation import StopAutomation
from assistant.desktop.models import OcrItem, WindowInfo
from assistant.desktop.task_workflows import Snapshot, TaskWorkflows
from assistant.desktop.windows import AutomationSafetyError


class ReadingReadyTests(unittest.TestCase):
    def flow(self, delay=0, never=False, external=False, challenge=False):
        mini = WindowInfo(1, 2, '永劫无间', 'Chrome', True, False, (0, 0, 642, 1180))
        reader = WindowInfo(3, 2, '微信', 'Chrome_WidgetWin_0', True, False, (0, 0, 1200, 1000))
        def page(window, *labels):
            return Snapshot('test', window, Path('unused'), [
                OcrItem(label, .99, ((100, 200+i*20), (400, 200+i*20), (400, 220+i*20), (100, 220+i*20)))
                for i, label in enumerate(labels)])
        welfare = page(mini, '福利中心', '每日阅读文章', '去完成', '每日登录永劫无间游戏')
        result = page(mini, '福利中心', '每日阅读文章', '已完成')
        host = reader if external else mini
        article = page(host, '劫宝 永劫无间 2026年10月21日23:06', '官方文章正文')
        driver = TaskWorkflows(); driver.click_item = Mock(); driver.back = Mock(); driver.log = Mock()
        driver.auxiliary_handles = set()
        driver._return_to_welfare_after_reading = Mock(return_value=result)
        pages = [welfare] * delay
        pages += [StopAutomation('manual_required', '安全验证')] if challenge else [article]
        pages += [article]
        if never:
            pages = [welfare] * 12
        driver.snapshot = Mock(side_effect=pages)
        windows = [mini]
        calls = []
        def enumerate_windows():
            calls.append(1)
            return [mini, reader] if external and len(calls) > 1 else windows
        with patch('assistant.desktop.task_workflows.time.sleep'), \
             patch('assistant.desktop.task_workflows.focus_window', side_effect=lambda w:w), \
             patch('assistant.desktop.task_workflows.enumerate_wechat_windows', side_effect=enumerate_windows), \
             patch('assistant.desktop.task_workflows.win32gui.GetForegroundWindow', return_value=3 if external else 1), \
             patch('assistant.desktop.task_workflows.scroll_window') as scroll, \
             patch('assistant.desktop.task_workflows.close_window') as close:
            try:
                outcome = driver.complete_reading(welfare)
            except (AutomationSafetyError, StopAutomation) as exc:
                outcome = exc
        return outcome, driver, scroll.call_args_list, close.call_args_list, host

    def test_welfare_brand_text_is_not_mistaken_for_an_article(self):
        outcome, driver, scroll, close, _ = self.flow(never=True)
        self.assertIsInstance(outcome, AutomationSafetyError)
        self.assertIn('文章', str(outcome))
        self.assertEqual(12, driver.snapshot.call_count)
        self.assertEqual([], scroll)
        self.assertEqual([], close)
        driver.back.assert_not_called()
        driver.click_item.assert_called_once()

    def test_delayed_article_loads_before_any_scrolling_or_back(self):
        outcome, driver, scroll, _, host = self.flow(delay=3)
        self.assertIsInstance(outcome, Snapshot)
        self.assertEqual(5, len(scroll))
        self.assertTrue(all(call.args[0] == host for call in scroll))
        self.assertEqual(5, driver.snapshot.call_count)
        driver.click_item.assert_called_once()
        driver.back.assert_called_once()

    def test_new_official_reader_window_is_read_and_closed_without_touching_mini_back(self):
        outcome, driver, scroll, close, host = self.flow(external=True)
        self.assertIsInstance(outcome, Snapshot)
        self.assertTrue(all(call.args[0] == host for call in scroll))
        self.assertEqual([host], [call.args[0] for call in close])
        driver.back.assert_not_called()

    def test_verification_stops_before_scrolling(self):
        outcome, driver, scroll, _, _ = self.flow(challenge=True)
        self.assertIsInstance(outcome, StopAutomation)
        self.assertEqual('manual_required', outcome.status)
        self.assertEqual([], scroll)
        driver.back.assert_not_called()
