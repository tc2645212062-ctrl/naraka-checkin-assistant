import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from assistant.desktop.recovery import restart_main

class RecoveryTests(unittest.TestCase):
    def test_once_per_day_and_only_matching_main_process(self):
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'Weixin.exe';exe.touch(); marker=Path(folder)/'state.json'
            window=SimpleNamespace(title='微信',class_name='Qt51514QWindowIcon',pid=42)
            process=Mock();process.exe.return_value=str(exe)
            with patch('assistant.desktop.recovery.psutil.Process',return_value=process), patch('assistant.desktop.recovery.subprocess.Popen') as start:
                restart_main(window,str(exe),marker,'2026-09-26')
                with self.assertRaisesRegex(RuntimeError,'今天已恢复'):restart_main(window,str(exe),marker,'2026-09-26')
                process.terminate.assert_called_once();process.wait.assert_called_once_with(timeout=10);start.assert_called_once()
                restart_main(window,str(exe),marker,'2026-09-27')
                self.assertEqual(2,start.call_count)

    def test_wrong_executable_never_terminated(self):
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'Weixin.exe';exe.touch();marker=Path(folder)/'state.json'
            process=Mock();process.exe.return_value=str(Path(folder)/'other.exe')
            window=SimpleNamespace(title='微信',class_name='Qt51514QWindowIcon',pid=42)
            with patch('assistant.desktop.recovery.psutil.Process',return_value=process):
                with self.assertRaises(RuntimeError):restart_main(window,str(exe),marker,'2026-09-26')
            process.terminate.assert_not_called();self.assertFalse(marker.exists())

    def test_failed_termination_also_consumes_daily_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'Weixin.exe';exe.touch();marker=Path(folder)/'state.json'
            process=Mock();process.exe.return_value=str(exe);process.terminate.side_effect=RuntimeError('access denied')
            window=SimpleNamespace(title='微信',class_name='Qt51514QWindowIcon',pid=42)
            with patch('assistant.desktop.recovery.psutil.Process',return_value=process),patch('assistant.desktop.recovery.subprocess.Popen') as start:
                with self.assertRaisesRegex(RuntimeError,'access denied'):restart_main(window,str(exe),marker,'2026-09-26')
                with self.assertRaisesRegex(RuntimeError,'今天已恢复'):restart_main(window,str(exe),marker,'2026-09-26')
                start.assert_not_called();process.terminate.assert_called_once()
