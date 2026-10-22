import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from assistant.game_login import BrokerClient


class CleanupPipeTests(unittest.TestCase):
    def run_stop(self, keep=False, stale=False, failure=False):
        report = {'ok':True,'cleanup_report':True,'owned':[],'remaining':[],
                  'unverified_new_pids':[],'errors':[],'retained':keep}
        stream = (b'{"ok":true,"kind":"game"}\n' if stale else b'') + json.dumps(report).encode() + b'\n'
        with tempfile.TemporaryDirectory() as folder:
            client = BrokerClient()
            client.pipe = Mock(); pipe = client.pipe
            client.keep_owned_game = keep
            client.cancel = Mock(is_set=lambda:True)
            client.game_session = SimpleNamespace(evidence=Path(folder), native_report=None, errors=[])
            with patch('win32file.WriteFile') as write, \
                 patch('win32pipe.PeekNamedPipe', side_effect=OSError('lost pipe') if failure else None,
                       return_value=(b'',len(stream),0)), \
                 patch('win32file.ReadFile', return_value=(0, stream)), \
                 patch('assistant.game_login.focus_game_window') as focus:
                client.__exit__(None, None, None)
            saved = client.game_session.native_report
            errors = list(client.game_session.errors)
            self.assertIsNone(client.pipe)
            pipe.Close.assert_called_once(); focus.assert_not_called()
        return write, saved, errors

    def test_cancel_does_not_skip_cleanup_or_send_game_input(self):
        write, report, errors = self.run_stop()
        self.assertEqual(b'stop\n', write.call_args.args[1])
        self.assertEqual([], report['remaining'])
        self.assertEqual([], errors)

    def test_stale_observation_after_cancel_is_not_mistaken_for_cleanup(self):
        _, report, errors = self.run_stop(stale=True)
        self.assertTrue(report['cleanup_report'])
        self.assertEqual([], errors)

    def test_keep_setting_uses_only_fixed_keep_stop_command(self):
        write, report, _ = self.run_stop(keep=True)
        self.assertEqual(b'stop:keep\n', write.call_args.args[1])
        self.assertTrue(report['retained'])

    def test_missing_cleanup_response_is_not_claimed_as_verified(self):
        _, report, errors = self.run_stop(failure=True)
        self.assertIsNone(report)
        self.assertTrue(errors)
