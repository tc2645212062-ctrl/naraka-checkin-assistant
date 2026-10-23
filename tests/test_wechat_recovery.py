import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from assistant.desktop.recovery import restore_main


class RecoveryTests(unittest.TestCase):
    def run_case(self, mode):
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'Weixin.exe';exe.touch();marker=Path(folder)/'state.json'
            window=SimpleNamespace(title='微信',class_name='Qt51514QWindowIcon',pid=42)
            process=Mock();process.exe.return_value=str(exe if mode!='wrong_path' else exe.parent/'other.exe')
            with patch('assistant.desktop.recovery.psutil.Process',return_value=process), \
                 patch('assistant.desktop.recovery.win.restore_existing_wechat',return_value=window) as restore:
                if mode=='wrong_path':
                    with self.assertRaises(RuntimeError):restore_main(window,str(exe),marker,'2026-10-23')
                    restore.assert_not_called();self.assertFalse(marker.exists())
                else:
                    if mode=='failure':restore.side_effect=RuntimeError('focus denied')
                    if mode=='failure':
                        with self.assertRaisesRegex(RuntimeError,'focus denied'):restore_main(window,str(exe),marker,'2026-10-23')
                    else:
                        self.assertIs(window,restore_main(window,str(exe),marker,'2026-10-23'))
                    with self.assertRaisesRegex(RuntimeError,'今天已恢复'):restore_main(window,str(exe),marker,'2026-10-23')
                    restore.assert_called_once_with(window)
                    self.assertEqual('restore_only',json.loads(marker.read_text(encoding='utf-8'))['mode'])
                process.terminate.assert_not_called();process.wait.assert_not_called()

    def test_restores_once_without_exiting_existing_process(self):self.run_case('ok')
    def test_wrong_executable_never_restored_or_terminated(self):self.run_case('wrong_path')
    def test_failed_restore_also_consumes_daily_budget(self):self.run_case('failure')
