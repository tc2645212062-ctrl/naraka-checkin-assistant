import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from assistant.config import Config
from assistant.desktop.automation import DesktopAdapter, StopAutomation
from assistant.desktop.models import OcrItem, WindowInfo
from assistant.desktop.task_workflows import Snapshot, TaskWorkflows
from assistant.desktop.windows import AutomationSafetyError


class PosterLoadingTests(unittest.TestCase):
    def setUp(self):
        self.mini = WindowInfo(1, 2, '永劫无间', 'Chrome', True, False, (0, 0, 642, 1180))
        self.welfare = self.page('每日分享无间镖客海报', '去完成')
        self.result = self.page('每日分享无间镖客海报', '已完成')
        self.gallery = self.page('藏品馆', '分享藏品馆')
        fixture = json.loads((Path(__file__).parent / 'fixtures/poster-loading.json').read_text(encoding='utf-8'))
        self.generating, self.preview = [Snapshot(state, self.mini, Path('unused'), [
            OcrItem(i['text'], i['confidence'], tuple(map(tuple, i['polygon']))) for i in fixture[state]])
            for state in ('generating', 'preview')]
        self.driver = TaskWorkflows()
        self.driver.click_item = Mock()
        self.driver.click_text = Mock()
        self.driver.back = Mock()
        self.driver.log = Mock()
        self.driver.wait_for_welfare = Mock(return_value=self.result)
        for name, options in (('time.sleep', {}), ('focus_window', {'return_value': self.mini})):
            item = patch('assistant.desktop.task_workflows.' + name, **options)
            item.start()
            self.addCleanup(item.stop)

    def page(self, *labels):
        return Snapshot('test', self.mini, Path('unused'), [
            OcrItem(text, .99, ((100, 55 + i * 25), (300, 55 + i * 25),
                              (300, 75 + i * 25), (100, 75 + i * 25)))
            for i, text in enumerate(labels)])

    def run_success(self, pages):
        self.driver.snapshot = Mock(side_effect=pages)
        self.assertIs(self.result, self.driver.complete_poster(self.welfare))
        self.driver.click_item.assert_called_once()
        self.driver.click_text.assert_called_once()
        self.assertEqual('分享藏品馆', self.driver.click_text.call_args.args[1])
        self.assertEqual(2, self.driver.back.call_count)
        self.driver.wait_for_welfare.assert_called_once()

    def test_real_generating_frame_is_waited_out_without_generating_twice(self):
        self.run_success([self.gallery] + [self.generating] * 4 + [self.preview, self.gallery])

    def test_ready_preview_keeps_existing_no_send_flow(self):
        self.run_success([self.gallery, self.preview, self.gallery])

    def test_initial_gallery_loading_is_observed_before_generation(self):
        self.run_success([self.page('加载中'), self.gallery, self.preview, self.gallery])

    def test_slow_back_to_gallery_does_not_send_another_back_until_ready(self):
        self.run_success([self.gallery, self.preview, self.preview, self.preview, self.gallery])

    def test_never_generated_preview_has_bounded_observations_and_no_back_or_send(self):
        self.driver.snapshot = Mock(side_effect=[self.gallery] + [self.generating] * 20)
        with self.assertRaisesRegex(AutomationSafetyError, '海报生成'):
            self.driver.complete_poster(self.welfare)
        self.assertEqual(21, self.driver.snapshot.call_count)
        self.driver.click_text.assert_called_once()
        self.driver.back.assert_not_called()
        self.driver.wait_for_welfare.assert_not_called()

    def test_never_returned_gallery_does_not_blindly_navigate_again(self):
        self.driver.snapshot = Mock(side_effect=[self.gallery, self.preview] + [self.preview] * 12)
        with self.assertRaisesRegex(AutomationSafetyError, '返回藏品馆'):
            self.driver.complete_poster(self.welfare)
        self.driver.back.assert_called_once()
        self.driver.wait_for_welfare.assert_not_called()

    def test_manual_verification_during_generation_propagates_immediately(self):
        self.driver.snapshot = Mock(side_effect=[self.gallery, StopAutomation('manual_required', '安全验证')])
        with self.assertRaises(StopAutomation) as raised:
            self.driver.complete_poster(self.welfare)
        self.assertEqual('manual_required', raised.exception.status)
        self.driver.click_text.assert_called_once()
        self.driver.back.assert_not_called()

    def test_generation_without_server_credit_is_not_success(self):
        self.driver.snapshot = Mock(side_effect=[self.gallery, self.preview, self.gallery])
        self.driver.wait_for_welfare.return_value = self.welfare
        with self.assertRaisesRegex(AutomationSafetyError, 'credit'):
            self.driver.complete_poster(self.welfare)

    def test_completed_poster_never_generates_or_sends(self):
        self.assertIs(self.result, self.driver.complete_poster(self.result))
        self.driver.click_item.assert_not_called()
        self.driver.click_text.assert_not_called()
        self.driver.back.assert_not_called()

    def test_unknown_poster_route_stops_other_tasks_instead_of_looping_on_preview(self):
        from assistant.desktop.task_workflows import PosterPageNotReady
        config = Config(enabled_tasks=['poster', 'read', 'key', 'login'], auto_game_login=True)
        with tempfile.TemporaryDirectory() as folder:
            adapter = DesktopAdapter(Path(folder), manual=True)
            adapter.config = config
            adapter.guard = Mock()
            adapter.welfare = Mock(return_value=(self.mini, Path('unused'), []))
            adapter.results = {task: ('not_signed', 'page') for task in config.enabled_tasks}
            driver = Mock()
            driver.task_button.return_value = SimpleNamespace(text='去完成')
            driver.complete_poster.side_effect = PosterPageNotReady('海报生成等待超时')
            with patch('assistant.desktop.workflows.WorkflowDriver', return_value=driver), \
                 patch('assistant.game_login.ensure_game_login') as game:
                result = adapter.sign(config)
            self.assertEqual('partial', result.status)
            self.assertEqual('entry_unavailable', adapter.results['poster'][0])
            self.assertIn('后续任务未执行', result.reason)
            adapter.welfare.assert_called_once()
            driver.complete_reading.assert_not_called()
            driver.find_today_key.assert_not_called()
            game.assert_not_called()
