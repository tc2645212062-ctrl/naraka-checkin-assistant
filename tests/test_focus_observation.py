import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from assistant.config import Config
from assistant.game_login import BrokerClient,GameLoginError
from assistant.desktop import windows as win
from assistant.desktop.automation import DesktopAdapter
from assistant.desktop.models import WindowInfo


class FocusObservationTests(unittest.TestCase):
    reason='游戏/启动器窗口未恢复到可截图尺寸；未发送游戏操作'

    def request(self,command,status='precondition',reason=None):
        client=BrokerClient();client.pipe=1;client.buffer=b'{"ok":true}\n'
        with patch('assistant.game_login.focus_game_window',side_effect=GameLoginError(status,reason or self.reason)), \
             patch('win32file.WriteFile') as send:
            try:result=client.request(command)
            except GameLoginError as exc:result=exc
        return result,send

    def test_native_observe_can_restore_after_normal_privilege_dimension_failure(self):
        result,send=self.request('observe')
        self.assertIsInstance(result,dict);self.assertTrue(result['ok'])
        self.assertEqual(b'observe\n',send.call_args.args[1])

    def test_click_still_stops_before_any_native_command(self):
        result,send=self.request('act:launcher_start')
        self.assertIsInstance(result,GameLoginError);send.assert_not_called()

    def test_unknown_precondition_and_modifier_guard_never_delegate(self):
        for status,reason in [('precondition','different failure'),('desktop_busy','用户正在按住修饰键'),('desktop_busy','桌面不可交互')]:
            with self.subTest(reason=reason):
                result,send=self.request('observe',status,reason)
                self.assertIsInstance(result,GameLoginError);send.assert_not_called()

    def test_foreground_guard_loss_is_busy_without_click_or_raw_error(self):
        window=WindowInfo(10,20,'微信','QtQWindowIcon',True,False,(0,0,1400,1000))
        with tempfile.TemporaryDirectory() as folder:
            adapter=DesktopAdapter(Path(folder),manual=True);adapter.guard=Mock()
            adapter.welfare=Mock(side_effect=win.ForegroundUnavailable('WeChat lost foreground focus; refusing input.'))
            with patch('assistant.platform.InstanceLock',return_value=Mock(acquire=lambda:True)), \
                 patch('assistant.desktop.session.WindowSession',return_value=Mock()), \
                 patch('assistant.desktop.automation.win.click_window_point') as click:
                result=adapter.check(Config())
            self.assertEqual('desktop_busy',result.status);click.assert_not_called()
            self.assertTrue((Path(folder)/'foreground-busy.json').exists())

    def test_low_level_focus_loss_is_typed_as_temporary_busy(self):
        window=WindowInfo(10,20,'微信','Qt',True,False,(0,0,1400,1000))
        with patch.object(win.win32gui,'GetForegroundWindow',return_value=99), \
             patch.object(win,'target_or_owned_popup',return_value=False), \
             patch.object(win.win32process,'GetWindowThreadProcessId',return_value=(1,30)), \
             patch.object(win.psutil,'Process',return_value=SimpleNamespace(name=lambda:'LootHoarder.exe',exe=lambda:r'E:\Naraka\tool\LootHoarder.exe')), \
             patch.object(win,'idle_seconds',return_value=45):
            with self.assertRaises(win.ForegroundUnavailable) as caught:win.assert_target_foreground(window)
        self.assertIs(caught.exception.details['input_sent'],False)
        self.assertEqual('LootHoarder.exe',caught.exception.details['foreground']['process_name'])
        self.assertEqual(45,caught.exception.details['user_idle_seconds'])
