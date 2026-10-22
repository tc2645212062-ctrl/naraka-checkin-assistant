import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from assistant.config import Config
from assistant.desktop.automation import DesktopAdapter, StopAutomation
from assistant.desktop.models import OcrItem, WindowInfo


class MiniNavigationTests(unittest.TestCase):
    def run_flow(self, mode):
        main = WindowInfo(10, 20, '微信', 'Qt', True, False, (0, 0, 1590, 1138))
        def page(*labels):
            return (main, Path('offline'), [OcrItem(label, .99, ((150, 60+i*90), (300, 60+i*90),
                        (300, 85+i*90), (150, 85+i*90))) for i, label in enumerate(labels)])
        discovery = page('发现', '朋友圈', '小程序')
        ready = page('最近使用', '永劫无间', '我的常用')
        observed = [discovery] + [discovery] * 15
        observed += [ready] if mode == 'recovered' else ([discovery] * 15 if mode == 'stuck' else [page('加载中')] * 15)
        if mode == 'unknown':
            observed = [discovery] + [page('加载中')] * 15
        if mode == 'challenge':
            observed[-1] = StopAutomation('manual_required', '安全验证')
        with tempfile.TemporaryDirectory() as folder, patch('assistant.desktop.automation.time.sleep'):
            adapter = DesktopAdapter(Path(folder)); adapter.config = Config()
            adapter.existing_mini = lambda:None; adapter.ensure_main = lambda:main
            adapter.mini_list_snapshot = Mock(side_effect=observed)
            adapter.text_click = Mock(); adapter.click = Mock(); adapter.wait_mini = lambda:main
            adapter.recover_wechat = Mock(return_value=main)
            try:
                result = adapter.open_mini(recover=False)
            except StopAutomation as error:
                result = error
        return result, adapter

    def test_unchanged_discovery_retries_navigation_once_then_opens_target(self):
        result, adapter = self.run_flow('recovered')
        self.assertIsInstance(result, WindowInfo)
        self.assertEqual(2, adapter.text_click.call_count)
        self.assertTrue(all(call.args[1] == '小程序' for call in adapter.text_click.call_args_list))
        adapter.click.assert_called_once()
        adapter.recover_wechat.assert_not_called()

    def test_persistent_discovery_is_bounded_without_restarting_when_disabled(self):
        result, adapter = self.run_flow('stuck')
        self.assertEqual('network_error', result.status)
        self.assertEqual(2, adapter.text_click.call_count)
        adapter.click.assert_not_called()
        adapter.recover_wechat.assert_not_called()

    def test_unknown_loading_page_is_not_clicked_again(self):
        result, adapter = self.run_flow('unknown')
        self.assertEqual('network_error', result.status)
        adapter.text_click.assert_called_once()
        adapter.click.assert_not_called()

    def test_challenge_during_second_wait_stops_immediately(self):
        result, adapter = self.run_flow('challenge')
        self.assertEqual('manual_required', result.status)
        adapter.click.assert_not_called()

