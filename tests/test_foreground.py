from contextlib import contextmanager, ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from assistant.config import Config
from assistant.desktop import windows as win
from assistant.desktop.automation import DesktopAdapter, StopAutomation
from assistant.desktop.models import WindowInfo


class ForegroundActivationTests(unittest.TestCase):
    target = WindowInfo(10, 20, '微信', 'Qt51514QWindowIcon', True, False, (0, 0, 1000, 800))

    @contextmanager
    def native(self):
        specs = {
            'desktop': ('desktop_available', True),
            'processes': ('wechat_processes', [Mock()]),
            'valid': ('win32gui.IsWindow', True),
            'visible': ('win32gui.IsWindowVisible', True),
            'iconic': ('win32gui.IsIconic', False),
            'foreground': ('win32gui.GetForegroundWindow', 10),
            'modifiers': ('win32api.GetAsyncKeyState', 0),
            'refresh': ('_window_for_handle', self.target),
        }
        with ExitStack() as stack:
            mocks = {name: stack.enter_context(patch('assistant.desktop.windows.' + path, return_value=value))
                     for name, (path, value) in specs.items()}
            for name, path in {'focus': 'win32gui.SetForegroundWindow', 'show': 'win32gui.ShowWindow',
                               'keys': 'win32api.keybd_event', 'legacy_keys': 'keyboard.send_keys',
                               'sleep': 'time.sleep'}.items():
                mocks[name] = stack.enter_context(patch('assistant.desktop.windows.' + path))
            mocks['owned'] = stack.enter_context(patch('assistant.desktop.windows.target_or_owned_popup',
                                                      side_effect=lambda handle, _: handle == 10))
            yield mocks

    def operations(self):
        return (win.focus_window, win.restore_existing_wechat)

    def test_already_focused_window_does_not_request_activation_or_send_alt(self):
        for operation in self.operations():
            with self.subTest(operation=operation.__name__), self.native() as native:
                self.assertEqual(self.target, operation(self.target))
                native['focus'].assert_not_called()
                native['keys'].assert_not_called()
                native['legacy_keys'].assert_not_called()

    def test_win32_error_is_accepted_only_when_actual_foreground_confirms_success(self):
        for operation in self.operations():
            with self.subTest(operation=operation.__name__), self.native() as native:
                native['foreground'].side_effect = [999, 10]
                native['focus'].side_effect = win.win32gui.error(0, 'SetForegroundWindow', 'No error message is available')
                self.assertEqual(self.target, operation(self.target))
                self.assertEqual(1, native['focus'].call_count)
                native['keys'].assert_not_called()
                native['legacy_keys'].assert_not_called()

    def test_fallback_releases_alt_before_second_request_and_rechecks_foreground(self):
        for operation in self.operations():
            with self.subTest(operation=operation.__name__), self.native() as native:
                events = []
                native['foreground'].side_effect = [999, 999, 10]
                def focus(_):
                    events.append('focus')
                    raise win.win32gui.error(0, 'SetForegroundWindow', 'No error message is available')
                native['focus'].side_effect = focus
                native['keys'].side_effect = lambda key, scan, flags, extra: events.append(
                    'up' if flags & win.win32con.KEYEVENTF_KEYUP else 'down')
                self.assertEqual(self.target, operation(self.target))
                self.assertEqual(['focus', 'down', 'up', 'focus'], events)
                native['legacy_keys'].assert_not_called()

    def test_permanent_denial_is_bounded_and_has_no_raw_win32_exception(self):
        for operation in self.operations():
            with self.subTest(operation=operation.__name__), self.native() as native:
                native['foreground'].return_value = 999
                native['focus'].side_effect = win.win32gui.error(0, 'SetForegroundWindow', 'No error message is available')
                with self.assertRaises(win.AutomationSafetyError):
                    operation(self.target)
                self.assertEqual(2, native['focus'].call_count)

    def test_locked_desktop_does_not_restore_activate_or_send_keys(self):
        for operation in self.operations():
            with self.subTest(operation=operation.__name__), self.native() as native:
                native['desktop'].return_value = False
                with self.assertRaises(win.AutomationSafetyError):
                    operation(self.target)
                for name in ('focus', 'show', 'keys', 'legacy_keys'):
                    native[name].assert_not_called()

    def test_held_modifier_does_not_activate_or_release_user_keys(self):
        for operation in self.operations():
            with self.subTest(operation=operation.__name__), self.native() as native:
                native['foreground'].return_value = 999
                native['modifiers'].return_value = 0x8000
                with self.assertRaises(win.AutomationSafetyError):
                    operation(self.target)
                for name in ('focus', 'show', 'keys', 'legacy_keys'):
                    native[name].assert_not_called()

    def test_reused_handle_cannot_activate_a_different_process(self):
        for operation in self.operations():
            with self.subTest(operation=operation.__name__), self.native() as native:
                native['refresh'].return_value = WindowInfo(10, 99, 'Other', 'Other', True, False, (0, 0, 1000, 800))
                with self.assertRaises(win.AutomationSafetyError):
                    operation(self.target)
                native['focus'].assert_not_called()
                native['keys'].assert_not_called()


class AdapterForegroundTests(unittest.TestCase):
    target = ForegroundActivationTests.target

    def denied(self):
        return getattr(win, 'ForegroundUnavailable', win.AutomationSafetyError)('微信窗口未能切到前台，未发送任务操作')

    def test_snapshot_denial_is_desktop_busy_without_capture_or_task_input(self):
        with tempfile.TemporaryDirectory() as folder:
            adapter = DesktopAdapter(Path(folder))
            with patch('assistant.desktop.automation.win.desktop_available', return_value=True), \
                 patch('assistant.desktop.automation.win.focus_window', side_effect=self.denied()), \
                 patch('assistant.desktop.automation.win.capture_window') as capture:
                with self.assertRaises(StopAutomation) as caught:
                    adapter.snapshot(self.target, 'test')
                self.assertEqual('desktop_busy', caught.exception.status)
                capture.assert_not_called()

    def test_main_restore_denial_stops_without_thirty_blind_activation_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            adapter = DesktopAdapter(Path(folder)); adapter.config = Config()
            with patch('assistant.desktop.automation.win.desktop_available', return_value=True), \
                 patch('assistant.desktop.automation.win.wechat_processes', return_value=[Mock()]), \
                 patch('pywinauto.Desktop', return_value=Mock(windows=lambda: [])), \
                 patch('assistant.desktop.automation.win.enumerate_wechat_windows', return_value=[self.target]), \
                 patch('assistant.desktop.automation.win.choose_main_window', return_value=self.target), \
                 patch('assistant.desktop.automation.win.restore_existing_wechat', side_effect=self.denied()) as restore, \
                 patch('assistant.desktop.automation.time.sleep'):
                with self.assertRaises(StopAutomation) as caught:
                    adapter.ensure_main()
                self.assertEqual('desktop_busy', caught.exception.status)
                restore.assert_called_once()
