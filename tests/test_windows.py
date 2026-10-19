import unittest
import tempfile
from pathlib import Path
import numpy as np
from unittest.mock import patch

from assistant.desktop.models import WindowInfo
from assistant.desktop.windows import target_or_owned_popup
from assistant.desktop.windows import capture_window


class WindowOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.target = WindowInfo(10,20,"微信","Qt",True,False,(0,0,1000,800))

    def test_main_window_allowed(self):
        with patch("assistant.desktop.windows.win32gui.GetAncestor", return_value=10):
            self.assertTrue(target_or_owned_popup(10,self.target))

    def test_owned_search_popup_allowed(self):
        with patch("assistant.desktop.windows.win32gui.GetAncestor", return_value=11), patch("assistant.desktop.windows.win32process.GetWindowThreadProcessId", return_value=(0,20)), patch("assistant.desktop.windows.win32gui.GetWindow", return_value=10):
            self.assertTrue(target_or_owned_popup(11,self.target))

    def test_unrelated_same_process_window_rejected(self):
        with patch("assistant.desktop.windows.win32gui.GetAncestor", return_value=11), patch("assistant.desktop.windows.win32process.GetWindowThreadProcessId", return_value=(0,20)), patch("assistant.desktop.windows.win32gui.GetWindow", return_value=12):
            self.assertFalse(target_or_owned_popup(11,self.target))

    def test_other_process_rejected(self):
        with patch("assistant.desktop.windows.win32gui.GetAncestor", return_value=11), patch("assistant.desktop.windows.win32process.GetWindowThreadProcessId", return_value=(0,99)):
            self.assertFalse(target_or_owned_popup(11,self.target))

    def test_unicode_screenshot_path(self):
        class Capture:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def grab(self,monitor): return np.zeros((40,40,4),dtype="uint8")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"中文目录"/"截图.png"
            with patch("assistant.desktop.windows.win32gui.IsWindow",return_value=True), patch("assistant.desktop.windows._window_for_handle",return_value=self.target), patch("assistant.desktop.windows.mss.mss",return_value=Capture()):
                capture_window(self.target,path)
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size,0)


if __name__ == "__main__":
    unittest.main()


class WeChatActivationTests(unittest.TestCase):
    def test_tray_hidden_qt_window_is_activated_through_wechat(self):
        from unittest.mock import Mock
        from assistant.desktop.windows import restore_existing_wechat
        before=WindowInfo(10,20,'微信','Qt51514QWindowIcon',False,False,(0,0,1000,800))
        after=WindowInfo(10,20,'微信','Qt51514QWindowIcon',True,False,(0,0,1000,800))
        with patch('assistant.desktop.windows.wechat_processes',return_value=[Mock()]), patch('assistant.desktop.windows.win32gui.IsWindow',return_value=True),patch('assistant.desktop.windows.win32gui.IsWindowVisible',side_effect=[False,True]),patch('assistant.desktop.windows.win32gui.IsIconic',return_value=False),patch('assistant.desktop.windows.psutil.Process',return_value=Mock(exe=lambda:r'D:\Weixin\Weixin.exe')),patch('assistant.desktop.tray.activate_wechat_tray') as activate,patch('assistant.desktop.windows.win32gui.ShowWindow') as native_show,patch('assistant.desktop.windows.win32gui.SetForegroundWindow'),patch('assistant.desktop.windows.win32gui.GetForegroundWindow',return_value=10),patch('assistant.desktop.windows.win32gui.GetAncestor',return_value=10),patch('assistant.desktop.windows.win32api.GetAsyncKeyState',return_value=0),patch('assistant.desktop.windows.desktop_available',return_value=True),patch('assistant.desktop.windows.time.sleep'),patch('assistant.desktop.windows._window_for_handle',return_value=after):
            self.assertEqual(after,restore_existing_wechat(before))
            activate.assert_called_once();native_show.assert_not_called()
