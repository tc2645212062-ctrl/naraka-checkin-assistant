import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from assistant.config import Config
from assistant.desktop.automation import DesktopAdapter, StopAutomation
from assistant.desktop.models import OcrItem, WindowInfo
from assistant.desktop.recognition import Calendar, calendar, read_image


class CalendarLoadingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.adapter = DesktopAdapter(Path(temporary.name))
        self.adapter.config = Config(enabled_tasks=['calendar'])
        self.adapter.window = WindowInfo(1, 2, '永劫无间', 'Chrome', True, False, (0, 0, 642, 1180))
        self.adapter.guard = Mock()
        self.adapter.assert_game = Mock()
        self.snap = (self.adapter.window, Path('unused'), [])
        self.adapter.snapshot = Mock(return_value=self.snap)
        self.adapter.welfare = Mock(return_value=self.snap)
        for target, options in (
            ('assistant.desktop.automation.time.sleep', {}),
            ('assistant.desktop.automation.read_image', {'return_value': np.zeros((10, 10, 3), dtype='uint8')}),
            ('assistant.desktop.automation.win.click_window_point', {}),
            ('assistant.desktop.automation.win.idle_seconds', {'return_value': 900}),
            ('assistant.platform.InstanceLock', {}),
            ('assistant.desktop.session.WindowSession', {}),
        ):
            item = patch(target, **options)
            handle = item.start()
            self.addCleanup(item.stop)
            if target.endswith('click_window_point'):
                self.click = handle

    def test_real_loading_calendar_does_not_prove_signed_or_unsigned(self):
        path = Path(__file__).parent / 'fixtures/calendar-loading.png'
        items = [OcrItem(i['text'], i['confidence'], tuple(map(tuple, i['polygon'])))
                 for i in json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))]
        with self.assertRaisesRegex(ValueError, '图标不确定'):
            calendar(read_image(path), items, date(2026, 10, 21))
        self.click.assert_not_called()

    def test_check_waits_for_graphics_after_text_has_loaded(self):
        ready = Calendar('not_signed', (100, 100), 50, 'verified icon')
        with patch('assistant.desktop.automation.calendar', side_effect=[ValueError('图标未就绪'), ready]) as recognize:
            result = self.adapter.check(self.adapter.config)
        self.assertEqual('not_signed', result.status)
        self.assertEqual('not_signed', self.adapter.results['calendar'][0])
        self.assertEqual(2, recognize.call_count)
        self.adapter.snapshot.assert_called_once()
        self.click.assert_not_called()

    def test_ready_frame_does_not_wait_or_recapture(self):
        ready = Calendar('already_signed', (100, 100), 50, 'verified icon')
        with patch('assistant.desktop.automation.calendar', return_value=ready):
            frame, state = self.adapter.wait_calendar(self.snap)
        self.assertIs(frame, self.snap)
        self.assertIs(state, ready)
        self.adapter.snapshot.assert_not_called()
        self.click.assert_not_called()

    def test_missing_graphics_stop_after_bounded_read_only_wait(self):
        with patch('assistant.desktop.automation.calendar', side_effect=ValueError('图标不确定')) as recognize:
            with self.assertRaises(StopAutomation) as raised:
                self.adapter.wait_calendar(self.snap)
        self.assertEqual('unrecognized', raised.exception.status)
        self.assertIn('图标不确定', str(raised.exception))
        self.assertEqual(12, recognize.call_count)
        self.assertEqual(11, self.adapter.snapshot.call_count)
        self.click.assert_not_called()

    def test_platform_change_stops_wait_before_recognizing_other_game(self):
        self.adapter.assert_game.side_effect = [None, StopAutomation('unrecognized', '页面变为手游')]
        with patch('assistant.desktop.automation.calendar', side_effect=ValueError('图标未就绪')) as recognize:
            with self.assertRaisesRegex(StopAutomation, '手游'):
                self.adapter.wait_calendar(self.snap)
        self.assertEqual(1, recognize.call_count)
        self.click.assert_not_called()

    def test_verification_during_refresh_is_not_swallowed(self):
        self.adapter.snapshot.side_effect = StopAutomation('manual_required', '页面要求安全验证')
        with patch('assistant.desktop.automation.calendar', side_effect=ValueError('图标未就绪')):
            result = self.adapter.check(self.adapter.config)
        self.assertEqual('manual_required', result.status)
        self.assertTrue(self.adapter.keep_windows)
        self.click.assert_not_called()

    def test_cancelled_wait_stops_before_capture_or_input(self):
        self.adapter.guard.side_effect = StopAutomation('interrupted', '用户停止')
        with self.assertRaisesRegex(StopAutomation, '用户停止'):
            self.adapter.wait_calendar(self.snap)
        self.adapter.snapshot.assert_not_called()
        self.click.assert_not_called()

    def test_sign_rechecks_loading_then_skips_already_signed_without_click(self):
        ready = Calendar('already_signed', (100, 100), 50, 'verified icon')
        with patch('assistant.desktop.automation.calendar', side_effect=[ValueError('图标未就绪'), ready]):
            result = self.adapter.sign_calendar(self.adapter.config)
        self.assertEqual('already_signed', result.status)
        self.assertTrue(result.verified)
        self.assertEqual(2, self.adapter.snapshot.call_count)
        self.click.assert_not_called()
