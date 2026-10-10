import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from assistant.config import Config
from assistant.desktop.automation import DesktopAdapter
from assistant.desktop.game_tab import selected_game
from assistant.desktop.models import OcrItem, WindowInfo
from assistant.desktop.recognition import read_image


FIXTURES = Path(__file__).parent / 'fixtures'


def fixture(name):
    path = FIXTURES / f'game-tabs-{name}.png'
    items = [OcrItem(i['text'], i['confidence'], tuple(map(tuple, i['polygon'])))
             for i in json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))]
    return path, items


def label(text, y):
    return OcrItem(text, .99, ((36, y), (118, y), (118, y + 24), (36, y + 24)))


class RedHomePlatformTests(unittest.TestCase):
    def test_failed_home_frame_recognizes_pc_despite_dark_red_mobile_background(self):
        path, items = fixture('red-pc')
        self.assertEqual('端游', selected_game(read_image(path), items))

    def test_scaled_failed_frame_keeps_platform_identity(self):
        path, items = fixture('red-pc')
        for scale in (.8, 1.25, 1.5):
            frame = cv2.resize(read_image(path), None, fx=scale, fy=scale)
            scaled = [OcrItem(i.text, i.confidence,
                              tuple((x * scale, y * scale) for x, y in i.polygon)) for i in items]
            with self.subTest(scale=scale):
                self.assertEqual('端游', selected_game(frame, scaled))

    def test_mobile_highlight_on_red_home_is_not_assumed_pc(self):
        path, items = fixture('red-pc')
        frame = np.zeros_like(read_image(path))
        frame[100:150, 33:278] = (22, 22, 66)
        frame[105:146, 159:274] = (78, 93, 130)
        self.assertEqual('手游', selected_game(frame, items))

    def test_neither_red_tab_is_a_selection(self):
        path, items = fixture('red-pc')
        frame = np.zeros_like(read_image(path))
        frame[100:150, 33:278] = (22, 22, 66)
        self.assertIsNone(selected_game(frame, items))

    def test_two_brown_tabs_are_ambiguous(self):
        path, items = fixture('red-pc')
        frame = np.zeros_like(read_image(path))
        frame[100:150, 33:278] = (78, 93, 130)
        self.assertIsNone(selected_game(frame, items))

    def test_missing_or_duplicate_platform_label_stays_unknown(self):
        path, items = fixture('red-pc')
        frame = read_image(path)
        self.assertIsNone(selected_game(frame, items[:1]))
        self.assertIsNone(selected_game(frame, items + items[:1]))

    def test_real_home_reaches_welfare_without_switching_to_mobile_or_repolling(self):
        path, items = fixture('red-pc')
        ready_path, ready_items = fixture('pc')
        window = WindowInfo(1, 2, '永劫无间', 'Chrome', True, False, (0, 0, 642, 1180))
        home = (window, path, items + [label('福利中心', 643)])
        ready = (window, ready_path, ready_items + [label('福利中心', 50), label('已签到', 340)])
        with tempfile.TemporaryDirectory() as folder, \
                patch('assistant.desktop.automation.match', return_value=None), \
                patch('assistant.desktop.automation.time.sleep'):
            adapter = DesktopAdapter(Path(folder))
            adapter.config = Config(game='端游')
            adapter.open_mini = Mock(return_value=window)
            adapter.recover_namecard_dialog = Mock()
            adapter.snapshot = Mock(side_effect=lambda _window, name: home if name == 'load-home' else ready)
            adapter.text_click = Mock()
            self.assertIs(ready, adapter.welfare())
            adapter.text_click.assert_called_once_with(home, '福利中心')
            self.assertEqual(3, adapter.snapshot.call_count)
