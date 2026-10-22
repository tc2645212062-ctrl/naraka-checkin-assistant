from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from assistant.config import Config
from assistant.desktop.automation import DesktopAdapter, StopAutomation
from assistant.desktop.models import OcrItem, WindowInfo
from assistant.desktop.task_workflows import PosterPageNotReady


class TaskRouteRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.mini = WindowInfo(1, 2, '永劫无间', 'Chrome', True, False, (0, 0, 642, 1180))

    def page(self, *labels):
        return (self.mini, Path('unused'), [OcrItem(label, .99, ((100, 50+i*35), (400, 50+i*35),
                  (400, 75+i*35), (100, 75+i*35))) for i, label in enumerate(labels)])

    def run_route(self, labels, missing_back=False, challenge=False):
        pages = [self.page(*row) for row in labels]
        if challenge:
            pages[-1] = StopAutomation('manual_required', '安全验证')
        with tempfile.TemporaryDirectory() as folder, \
             patch('assistant.desktop.automation.time.sleep'), \
             patch('assistant.desktop.automation.read_image', return_value=np.zeros((1180, 642, 3), np.uint8)), \
             patch('assistant.desktop.automation.match', return_value=None if missing_back else (.99, (32, 57))), \
             patch('assistant.desktop.automation.win.click_window_point') as click:
            adapter = DesktopAdapter(Path(folder)); adapter.config = Config(); adapter.guard = Mock()
            adapter.snapshot = Mock(side_effect=pages)
            try:
                outcome = adapter.restore_task_route(self.mini)
            except StopAutomation as exc:
                outcome = exc
            calls = click.call_args_list
        return outcome, adapter, calls

    def test_preview_returns_through_gallery_to_known_welfare(self):
        outcome, _, calls = self.run_route([['图片预览', '转发给朋友'], ['藏品馆', '分享藏品馆'], ['福利中心']])
        self.assertIsInstance(outcome, tuple)
        self.assertEqual(2, len(calls))

    def test_slow_preview_back_is_not_repeated(self):
        outcome, _, calls = self.run_route([['图片预览', '转发给朋友']] * 4 + [['藏品馆', '分享藏品馆'], ['福利中心']])
        self.assertIsInstance(outcome, tuple)
        self.assertEqual(2, len(calls))

    def test_official_article_returns_once(self):
        outcome, _, calls = self.run_route([['永劫无间', '劫宝永劫无间2026年10月21日23:09浙江'], ['福利中心']])
        self.assertIsInstance(outcome, tuple)
        self.assertEqual(1, len(calls))

    def test_unknown_page_is_never_navigated(self):
        outcome, _, calls = self.run_route([['未知页面', '返回']])
        self.assertEqual('unrecognized', outcome.status)
        self.assertEqual([], calls)

    def test_known_route_without_verified_back_arrow_is_not_clicked(self):
        outcome, _, calls = self.run_route([['图片预览', '转发给朋友']], missing_back=True)
        self.assertEqual('unrecognized', outcome.status)
        self.assertEqual([], calls)

    def test_persistent_preview_stops_after_one_back_and_bounded_observations(self):
        outcome, adapter, calls = self.run_route([['图片预览', '转发给朋友']] * 15)
        self.assertEqual('entry_unavailable', outcome.status)
        self.assertEqual(15, adapter.snapshot.call_count)
        self.assertEqual(1, len(calls))

    def test_real_challenge_during_return_is_preserved(self):
        outcome, _, calls = self.run_route([['图片预览', '转发给朋友'], ['安全验证']], challenge=True)
        self.assertEqual('manual_required', outcome.status)
        self.assertEqual(1, len(calls))

    def test_late_poster_credit_is_rechecked_without_generating_again(self):
        config = Config(enabled_tasks=['poster', 'read'])
        preview = self.page('图片预览', '转发给朋友')
        with tempfile.TemporaryDirectory() as folder:
            adapter = DesktopAdapter(Path(folder)); adapter.config = config; adapter.window = self.mini
            adapter.guard = Mock(); adapter.snapshot = Mock(return_value=preview)
            adapter.restore_task_route = Mock(return_value=self.page('福利中心'))
            adapter.welfare = Mock(return_value=self.page('福利中心'))
            adapter.results = {task: ('not_signed', 'pending') for task in config.enabled_tasks}
            driver = Mock()
            driver.task_button.side_effect = [SimpleNamespace(text=status) for status in ('去完成', '已完成', '去完成', '已完成')]
            driver.complete_poster.side_effect = PosterPageNotReady('海报生成等待超时')
            with patch('assistant.desktop.workflows.WorkflowDriver', return_value=driver):
                outcome = adapter.sign(config)
            self.assertEqual('success', outcome.status)
            self.assertEqual('success', adapter.results['poster'][0])
            driver.complete_poster.assert_called_once()
            driver.complete_reading.assert_called_once()
            adapter.restore_task_route.assert_called_once()

