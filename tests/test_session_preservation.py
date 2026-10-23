import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from assistant.config import Config
from assistant.desktop.automation import DesktopAdapter, StopAutomation
from assistant.desktop.models import WindowInfo
from assistant.desktop.session import WindowSession


def window(handle, kind='Qt51514QWindowIcon', **values):
    return WindowInfo(handle, values.get('pid', 20), '微信', kind,
                      values.get('visible', True), values.get('minimized', False),
                      (0, 0, 1500, 1000))


class SessionPreservationTests(unittest.TestCase):
    def finish(self, initial, used, current):
        with patch('assistant.desktop.session.win.enumerate_wechat_windows', side_effect=[initial, current]), \
             patch.object(WindowSession, 'game_windows', return_value=[]), \
             patch('assistant.desktop.session.win.desktop_available', return_value=True), \
             patch('assistant.desktop.session.win.close_window') as close, \
             patch('assistant.desktop.session.win.win32gui.ShowWindow') as show, \
             patch('assistant.desktop.session.win.hide_window') as hide:
            session = WindowSession()
            session.remember(used)
            session.finish()
        return close, show, hide

    def test_main_created_after_saved_login_is_never_closed(self):
        login, main = window(10, 'QtLoginWindow'), window(30)
        close, show, _ = self.finish([login], main, [main])
        close.assert_not_called()
        show.assert_called_once_with(30, 6)

    def test_new_login_window_is_never_closed_by_general_cleanup(self):
        login = window(10, 'QtLoginWindow')
        close, _, _ = self.finish([], login, [login])
        close.assert_not_called()

    def test_original_visible_main_stays_visible(self):
        main = window(30)
        close, show, hide = self.finish([main], main, [main])
        close.assert_not_called(); show.assert_not_called(); hide.assert_not_called()

    def test_original_hidden_main_returns_to_hidden_state(self):
        before, main = window(30, visible=False), window(30)
        close, show, hide = self.finish([before], main, [main])
        close.assert_not_called(); show.assert_not_called(); hide.assert_called_once_with(main)

    def test_recovery_preserves_existing_process_and_does_not_launch_another(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); executable = root/'Weixin.exe'; executable.touch()
            process = Mock(); process.exe.return_value = str(executable)
            adapter = DesktopAdapter(root/'evidence'); adapter.config = Config()
            adapter.config.wechat_exe = str(executable)
            adapter.guard = Mock(); adapter.ensure_main = Mock(return_value=window(30))
            with patch('assistant.config.data_directory', return_value=root), \
                 patch('assistant.desktop.recovery.psutil.Process', return_value=process), \
                 patch('subprocess.Popen') as start, \
                 patch('assistant.desktop.automation.time.sleep'), \
                 patch('assistant.desktop.automation.win.restore_existing_wechat', return_value=window(30)) as restore:
                self.assertEqual(window(30), adapter.recover_wechat(window(30)))
            process.terminate.assert_not_called(); start.assert_not_called()
            restore.assert_called_once_with(window(30))


class MiniLaunchEvidenceTests(unittest.TestCase):
    def test_configured_shortcut_does_not_launch_another_client(self):
        from assistant.desktop.models import OcrItem
        with tempfile.TemporaryDirectory() as folder, patch('os.startfile') as start:
            adapter=DesktopAdapter(Path(folder));adapter.config=Config()
            adapter.config.shortcut='existing-mini-shortcut.lnk'
            main=window(30)
            labels=['最近使用','永劫无间','我的常用']
            items=[OcrItem(text,.99,((600,100+i*100),(700,100+i*100),(700,120+i*100),(600,120+i*100)))
                   for i,text in enumerate(labels)]
            adapter.existing_mini=Mock(return_value=None);adapter.ensure_main=Mock(return_value=main)
            adapter.mini_list_snapshot=Mock(return_value=(main,Path('offline'),items))
            adapter.click=Mock();adapter.wait_mini=Mock(return_value=main)
            self.assertEqual(main,adapter.open_mini())
            start.assert_not_called();adapter.click.assert_called_once()

    def test_final_observation_accepts_late_window_without_second_click(self):
        with tempfile.TemporaryDirectory() as folder, patch('assistant.desktop.automation.time.sleep'):
            adapter = DesktopAdapter(Path(folder)); adapter.config = Config(); adapter.guard = Mock()
            adapter.existing_mini = Mock(side_effect=[None]*24 + [window(40, 'Chrome_WidgetWin_0')])
            self.assertEqual(40, adapter.wait_mini().handle)

    def test_timeout_records_window_inventory_and_original_page(self):
        with tempfile.TemporaryDirectory() as folder, patch('assistant.desktop.automation.time.sleep'), \
             patch('assistant.desktop.automation.win.enumerate_wechat_windows', return_value=[window(30)]):
            root = Path(folder)
            adapter = DesktopAdapter(root); adapter.config = Config(); adapter.guard = Mock()
            adapter.existing_mini = Mock(return_value=None)
            adapter.mini_launch_window = window(30)
            adapter.snapshot = Mock(return_value=(window(30), root/'mini-open-timeout.png', []))
            with self.assertRaises(StopAutomation) as caught:
                adapter.wait_mini()
            self.assertEqual('entry_unavailable', caught.exception.status)
            record = json.loads((root/'mini-launch.json').read_text(encoding='utf-8'))
            self.assertEqual('timeout', record['status'])
            self.assertEqual(30, record['windows'][0]['handle'])
            adapter.snapshot.assert_called_once_with(window(30), 'mini-open-timeout')

    def test_timeout_page_challenge_is_preserved_instead_of_entry_error(self):
        with tempfile.TemporaryDirectory() as folder, patch('assistant.desktop.automation.time.sleep'), \
             patch('assistant.desktop.automation.win.enumerate_wechat_windows', return_value=[window(30)]):
            adapter = DesktopAdapter(Path(folder)); adapter.config = Config(); adapter.guard = Mock()
            adapter.existing_mini = Mock(return_value=None); adapter.mini_launch_window = window(30)
            adapter.snapshot = Mock(side_effect=StopAutomation('manual_required', '请扫码登录'))
            with self.assertRaises(StopAutomation) as caught:
                adapter.wait_mini()
            self.assertEqual('manual_required', caught.exception.status)
