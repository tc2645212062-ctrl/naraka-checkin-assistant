import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from assistant.config import Config
from assistant.desktop.automation import DesktopAdapter, StopAutomation
from assistant.desktop.models import OcrItem, WindowInfo
from assistant.desktop import update_prompt


class MiniRestartTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parent / 'fixtures/mini-restart.json'
        self.items = [OcrItem(i['text'], i['confidence'], tuple(map(tuple, i['polygon'])))
                      for i in json.loads(path.read_text(encoding='utf-8'))]

    def test_real_split_restart_prompt_selects_cancel_not_confirmation(self):
        button = update_prompt.defer_mini_restart_button(self.items, 642, 1180)
        self.assertEqual('取消', button.text)
        self.assertEqual((220, 646), button.center)

    def test_partial_header_and_unrelated_cancel_do_not_trigger(self):
        for items in (self.items[1:], self.items[:1] + self.items[3:]):
            self.assertIsNone(update_prompt.defer_mini_restart_button(items, 642, 1180))

    def test_ambiguous_or_misplaced_buttons_stop_without_guessing(self):
        duplicate = next(i for i in self.items if i.text == '取消')
        with self.assertRaises(ValueError):
            update_prompt.defer_mini_restart_button(self.items + [duplicate], 642, 1180)
        with self.assertRaises(ValueError):
            update_prompt.defer_mini_restart_button(self.items, 2000, 1180)

    def flow(self, persist=False):
        window = WindowInfo(1, 2, '永劫无间', 'Chrome', True, False, (0, 0, 642, 1180))
        prompt = (window, Path('unused'), self.items)
        def item(text, y):
            return OcrItem(text, .99, ((200, y), (350, y), (350, y + 20), (200, y + 20)))
        ready = (window, Path('unused'), [item('福利中心', 55), item('已签到', 340)])
        with tempfile.TemporaryDirectory() as folder, patch('assistant.desktop.automation.time.sleep'), \
             patch('assistant.desktop.automation.read_image', return_value=np.zeros((1180, 642, 3))), \
             patch('assistant.desktop.automation.match', return_value=None), \
             patch('assistant.desktop.game_tab.selected_game', return_value='端游'):
            adapter = DesktopAdapter(Path(folder)); adapter.config = Config()
            adapter.open_mini = Mock(return_value=window)
            adapter.recover_namecard_dialog = Mock()
            adapter.snapshot = Mock(side_effect=[prompt, prompt if persist else ready])
            adapter.assert_game = Mock()
            adapter.text_click = Mock()
            try:
                result = adapter.welfare()
            except StopAutomation as exc:
                result = exc
            return result, adapter.text_click.call_args_list, adapter.snapshot.call_count

    def test_restart_prompt_is_cancelled_once_and_welfare_remains_accessible(self):
        result, clicks, captures = self.flow()
        self.assertIsInstance(result, tuple)
        self.assertEqual(['取消'], [call.args[1] for call in clicks])
        self.assertEqual(2, captures)

    def test_persistent_restart_prompt_stops_instead_of_repeating_cancel(self):
        result, clicks, captures = self.flow(True)
        self.assertEqual('precondition', result.status)
        self.assertEqual(['取消'], [call.args[1] for call in clicks])
        self.assertEqual(2, captures)
