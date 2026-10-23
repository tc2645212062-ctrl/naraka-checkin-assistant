from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from assistant.config import Config
from assistant.desktop.automation import DesktopAdapter, StopAutomation
from assistant.desktop.models import WindowInfo
from assistant.desktop import windows as win


class LoginLoadingTests(unittest.TestCase):
    def flow(self, pages, focus_error=None, tray=False, tray_error=None, main_error=None):
        login = WindowInfo(10, 20, '微信', 'QtLoginWindow', True, False, (100, 100, 700, 800))
        main = WindowInfo(30, 20, '微信', 'QtQWindowIcon', True, False, (0, 0, 1400, 1000))
        observed = []
        current = {'page': None}
        iterator = iter(pages)
        def ui_windows():
            try:
                current['page'] = next(iterator)
            except StopIteration:
                pass
            page = current['page']
            observed.append(page)
            if page is None:
                return []
            def descendants(control_type):
                labels = page.get('buttons', []) if control_type == 'Button' else page.get('texts', [])
                return [Mock(window_text=lambda text=text: text,
                             is_visible=lambda text=text: text not in page.get('hidden_labels', []),
                             rectangle=lambda: SimpleNamespace(left=200, top=200, right=300, bottom=240))
                        for text in labels]
            return [Mock(handle=page.get('handle', 10), class_name=lambda: 'QtLoginWindow',
                         window_text=lambda: '微信', descendants=descendants)]
        def choose(_):
            if main_error:
                raise main_error
            page = current['page']
            if page is not None and not page.get('foreign') and not page.get('hidden') and not page.get('main_ready'):
                raise win.AutomationSafetyError('Main window is still loading')
            return main
        def owned():
            if current['page'] and current['page'].get('hidden'):
                return [WindowInfo(10, 20, '微信', 'QtLoginWindow', False, False, login.rect), main]
            return [login, main]
        with tempfile.TemporaryDirectory() as folder, \
             patch('assistant.desktop.automation.win.desktop_available', return_value=True), \
             patch('assistant.desktop.automation.win.wechat_processes', return_value=[Mock()]), \
             patch('assistant.desktop.tray.wechat_tray_present', return_value=tray), \
             patch('assistant.desktop.tray.activate_wechat_tray', side_effect=tray_error) as activate_tray, \
             patch('pywinauto.Desktop', return_value=Mock(windows=ui_windows)), \
             patch('assistant.desktop.automation.win.enumerate_wechat_windows', side_effect=owned), \
             patch('assistant.desktop.automation.win.choose_main_window', side_effect=choose) as selection, \
             patch('assistant.desktop.automation.win.focus_window', side_effect=focus_error or (lambda window: window)), \
             patch('assistant.desktop.automation.win.restore_existing_wechat', return_value=main), \
             patch('assistant.desktop.automation.win.click_window_point') as click, \
             patch('assistant.desktop.automation.time.sleep'):
            adapter = DesktopAdapter(Path(folder)); adapter.config = Config()
            adapter.snapshot = Mock(return_value=(main, Path('unused'), []))
            try:
                result = adapter.ensure_main()
            except StopAutomation as exc:
                result = exc
            record = Path(folder)/'wechat-login.json'
            self.last_login_evidence = __import__('json').loads(record.read_text(encoding='utf-8')) if record.exists() else None
            self.last_tray_calls = activate_tray.call_count
            return result, click.call_args_list, observed, selection.call_count

    def test_delayed_saved_button_waits_then_enters_once(self):
        result, clicks, observed, _ = self.flow([{}, {}, {'buttons': ['进入微信']}, None])
        self.assertIsInstance(result, WindowInfo)
        self.assertEqual(1, len(clicks))
        self.assertEqual(4, len(observed))

    def test_saved_button_already_loaded_keeps_existing_single_click(self):
        result, clicks, _, _ = self.flow([{'buttons': ['进入微信']}, None])
        self.assertIsInstance(result, WindowInfo)
        self.assertEqual(1, len(clicks))

    def test_empty_loading_window_never_claims_qr_or_phone_verification(self):
        result, clicks, observed, _ = self.flow([{}])
        self.assertEqual('precondition', result.status)
        self.assertEqual([], clicks)
        self.assertEqual(30, len(observed))

    def test_explicit_phone_confirmation_stops_after_saved_entry(self):
        result, clicks, observed, _ = self.flow([
            {'buttons': ['进入微信']}, {'texts': ['请在手机上确认登录']}])
        self.assertEqual('manual_required', result.status)
        self.assertIn('手机', str(result))
        self.assertEqual(1, len(clicks))
        self.assertEqual(2, len(observed))

    def test_explicit_qr_or_captcha_stops_before_any_saved_entry(self):
        for text in ('请使用手机微信扫码登录', '请输入验证码', '安全验证'):
            with self.subTest(text=text):
                result, clicks, observed, _ = self.flow([{'buttons': ['进入微信'], 'texts': [text]}])
                self.assertEqual('manual_required', result.status)
                self.assertEqual([], clicks)
                self.assertEqual(1, len(observed))

    def test_accepted_entry_is_not_repeated_while_main_window_loads(self):
        result, clicks, observed, _ = self.flow([{'buttons': ['进入微信']}])
        self.assertEqual('precondition', result.status)
        self.assertEqual(1, len(clicks))
        self.assertEqual(30, len(observed))

    def test_ambiguous_saved_entry_stops_without_guessing(self):
        result, clicks, _, _ = self.flow([{'buttons': ['进入微信', '进入微信']}])
        self.assertEqual('unrecognized', result.status)
        self.assertEqual([], clicks)

    def test_unrelated_or_hidden_login_window_does_not_force_relogin(self):
        for page in ({'handle': 999, 'foreign': True}, {'hidden': True}):
            with self.subTest(page=page):
                result, clicks, _, _ = self.flow([page])
                self.assertIsInstance(result, WindowInfo)
                self.assertEqual([], clicks)

    def test_login_focus_failure_is_desktop_busy_and_never_clicks(self):
        result, clicks, _, _ = self.flow([{'buttons': ['进入微信']}],
            focus_error=win.ForegroundUnavailable('微信窗口未能切到前台'))
        self.assertEqual('desktop_busy', result.status)
        self.assertEqual([], clicks)

    def test_account_switch_and_proxy_labels_are_not_verification_proof(self):
        result, clicks, observed, _ = self.flow([
            {'texts': ['切换账号', '网络代理设置']}, {'buttons': ['进入微信']}, None])
        self.assertIsInstance(result, WindowInfo)
        self.assertEqual(1, len(clicks))
        self.assertEqual(3, len(observed))

    def test_bare_qr_mode_label_during_transition_is_not_a_challenge(self):
        result, clicks, observed, _ = self.flow([
            {'buttons': ['进入微信']}, {'texts': ['扫码登录']}, None])
        self.assertIsInstance(result, WindowInfo)
        self.assertEqual(1, len(clicks))
        self.assertEqual(3, len(observed))

    def test_hidden_qr_instruction_does_not_override_saved_login(self):
        result, clicks, _, _ = self.flow([
            {'buttons': ['进入微信'], 'texts': ['请使用手机微信扫码登录'],
             'hidden_labels': ['请使用手机微信扫码登录']}, None])
        self.assertIsInstance(result, WindowInfo)
        self.assertEqual(1, len(clicks))

    def test_visible_qr_control_stops_and_records_proof_without_click(self):
        result, clicks, _, _ = self.flow([{'texts': ['扫码登录'], 'buttons': ['二维码']}])
        self.assertEqual('manual_required', result.status)
        self.assertEqual([], clicks)
        self.assertIsNotNone(self.last_login_evidence)
        proof = self.last_login_evidence['observations'][-1]
        self.assertTrue(proof['qr_control_visible'])
        self.assertEqual('扫码登录', proof['challenge'])

    def test_existing_main_is_preferred_over_another_qr_login_window(self):
        result, clicks, _, _ = self.flow([
            {'main_ready': True, 'texts': ['扫码登录'], 'buttons': ['二维码']}])
        self.assertIsInstance(result, WindowInfo)
        self.assertEqual([], clicks)

    def test_tray_restore_without_original_main_never_clicks_other_login(self):
        result, clicks, observed, _ = self.flow([
            {'texts': ['扫码登录'], 'buttons': ['二维码', '进入微信']}], tray=True)
        self.assertEqual('entry_unavailable', result.status)
        self.assertEqual([], clicks)
        self.assertEqual(1, self.last_tray_calls)
        self.assertEqual(2, len(observed))

    def test_tray_restore_failure_never_falls_back_to_saved_login(self):
        result, clicks, _, _ = self.flow([{'buttons': ['进入微信']}],
            tray=True, tray_error=RuntimeError('tray inaccessible'))
        self.assertEqual('entry_unavailable', result.status)
        self.assertEqual([], clicks)
        self.assertEqual(1, self.last_tray_calls)

    def test_ambiguous_existing_clients_never_fall_back_to_login(self):
        result, clicks, _, _ = self.flow([{'buttons': ['进入微信']}],
            main_error=win.MainWindowAmbiguous('multiple clients'))
        self.assertEqual('unrecognized', result.status)
        self.assertEqual([], clicks)
        self.assertEqual(0, self.last_tray_calls)
