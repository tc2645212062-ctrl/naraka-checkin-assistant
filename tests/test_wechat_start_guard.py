import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from assistant.desktop import windows as win


class WechatStartGuardTests(unittest.TestCase):
    def check(self, processes, tray=False, error=None):
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'Weixin.exe'; exe.touch()
            with patch.object(win, 'wechat_processes', side_effect=processes), \
                 patch.object(win, 'desktop_available', return_value=True), \
                 patch('assistant.desktop.tray.wechat_tray_present', return_value=tray, side_effect=error, create=True), \
                 patch('assistant.desktop.windows.subprocess.Popen') as start:
                result=win.start_wechat_if_absent(exe)
            return result, start

    def test_running_process_prevents_launch_even_with_no_window(self):
        result,start=self.check([[Mock()]])
        self.assertFalse(result);start.assert_not_called()

    def test_tray_icon_prevents_launch_even_if_process_snapshot_is_empty(self):
        result,start=self.check([[]],tray=True)
        self.assertFalse(result);start.assert_not_called()

    def test_process_arriving_during_tray_probe_prevents_launch(self):
        result,start=self.check([[],[Mock()]])
        self.assertFalse(result);start.assert_not_called()

    def test_unreadable_tray_never_falls_back_to_launch(self):
        with patch('assistant.desktop.windows.subprocess.Popen') as start:
            with self.assertRaises(win.AutomationSafetyError):self.check([[]],error=RuntimeError('tray unavailable'))
            start.assert_not_called()

    def test_confirmed_absent_client_can_start_once_without_ui_in_tests(self):
        result,start=self.check([[],[]])
        self.assertTrue(result);start.assert_called_once()

    def test_legacy_entry_uses_same_start_guard(self):
        with patch.object(win,'wechat_processes',side_effect=[[],[Mock()]]), \
             patch.object(win,'start_wechat_if_absent',return_value=False,create=True) as guard, \
             patch.object(win,'enumerate_wechat_windows',return_value=[]), \
             patch.object(win,'choose_main_window',return_value='original'), \
             patch.object(win,'WECHAT_EXECUTABLE') as exe, \
             patch('assistant.desktop.windows.subprocess.Popen') as start:
            exe.is_file.return_value=True
            self.assertEqual(('original',False),win.ensure_wechat_main_window())
            guard.assert_called_once();start.assert_not_called()

    def test_login_window_is_not_selected_as_a_main_client(self):
        from assistant.desktop.models import WindowInfo
        old=WindowInfo(10,20,'微信','QtQWindowIcon',False,False,(0,0,1000,800))
        login=WindowInfo(30,40,'微信','QtLoginWindowQWindowIcon',True,False,(0,0,1400,1000))
        self.assertEqual(old,win.choose_main_window([login,old]))

    def test_tray_presence_query_never_clicks_or_expands(self):
        from assistant.desktop.tray import wechat_tray_present
        icon=Mock();icon.window_text.return_value='微信'
        icon.parent.return_value.class_name.return_value='ToolbarWindow32'
        tray=Mock();tray.descendants.return_value=[icon]
        desktop=Mock();desktop.window.return_value.wrapper_object.return_value=tray
        with patch('assistant.desktop.tray.Desktop',return_value=desktop), \
             patch('assistant.desktop.tray.win32gui.FindWindow',return_value=10):
            self.assertTrue(wechat_tray_present())
        icon.invoke.assert_not_called();icon.click_input.assert_not_called()
