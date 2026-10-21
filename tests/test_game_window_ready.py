import base64
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from assistant.game_login import ensure_game_login, focus_game_window, GameLoginError


class WindowObservationTests(unittest.TestCase):
    def flow(self, rejects=2, lines=None, action_error=False, reason='unrecognized: window dimensions'):
        commands = []
        class Broker:
            def __init__(self, _): self.observations = 0
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def request(self, command):
                commands.append(command)
                if command == 'observe':
                    self.observations += 1
                    if self.observations <= rejects:
                        raise GameLoginError('unrecognized', reason)
                    return {'kind': 'launcher' if self.observations == rejects+1 else 'game',
                            'png': base64.b64encode(b'frame').decode()}
                if action_error:
                    raise GameLoginError('unrecognized', 'unrecognized: window dimensions')
                return {}
        pages = iter([lines or ['启动游戏'], ['开始游戏', '大厅', '英雄']])
        with tempfile.TemporaryDirectory() as folder, patch('assistant.game_login.time.sleep'):
            outcome = ensure_game_login(Path(folder), client_factory=Broker, read_page=lambda _:next(pages), close_started_game=False)
        return outcome, commands

    def test_small_observation_is_retried_without_replaying_actions(self):
        result, commands = self.flow()
        self.assertEqual('ready', result.status)
        self.assertEqual(4, commands.count('observe'))
        self.assertEqual(['act:launcher_start'], [c for c in commands if c.startswith('act:')])

    def test_permanent_small_window_is_bounded_and_not_code_success(self):
        result, commands = self.flow(rejects=99)
        self.assertEqual('precondition', result.status)
        self.assertEqual(['observe'] * 4, commands)

    def test_new_challenge_after_wait_stops_without_input(self):
        result, commands = self.flow(lines=['验证码', '启动游戏'])
        self.assertEqual('manual_required', result.status)
        self.assertFalse(any(c.startswith('act:') for c in commands))

    def test_other_native_rejections_are_not_blindly_retried(self):
        result, commands = self.flow(reason='unrecognized: multiple game windows')
        self.assertEqual('unrecognized', result.status)
        self.assertEqual(['observe'], commands)

    def test_dimension_rejection_of_an_action_is_never_replayed(self):
        result, commands = self.flow(rejects=0, action_error=True)
        self.assertEqual('unrecognized', result.status)
        self.assertEqual(['observe', 'act:launcher_start'], commands)


class MinimizedFocusTests(unittest.TestCase):
    def flow(self, persistent=False, held=False):
        state = {'minimized': True}
        def restore(*_):
            if not persistent:
                state['minimized'] = False
        with ExitStack() as stack:
            values = {
                'win32gui.EnumWindows': {'side_effect': lambda callback,arg:callback(123,arg)},
                'win32gui.IsWindowVisible': {'return_value': True},
                'win32gui.IsIconic': {'side_effect': lambda _:state['minimized']},
                'win32gui.GetWindowRect': {'side_effect': lambda _:(0,0,200,39) if state['minimized'] else (0,0,1536,930)},
                'win32process.GetWindowThreadProcessId': {'return_value': (1,2)},
                'psutil.Process': {'return_value': Mock(exe=lambda:r'E:\Naraka\tool\LootHoarder.exe')},
                'win32gui.GetForegroundWindow': {'return_value': 123},
                'win32api.GetAsyncKeyState': {'return_value': 0x8000 if held else 0},
                'win32gui.ShowWindow': {'side_effect': restore},
                'win32gui.SetForegroundWindow': {}, 'win32api.keybd_event': {},
                'assistant.game_login.time.sleep': {},
                'assistant.desktop.windows.desktop_available': {'return_value': True},
            }
            handles = {name: stack.enter_context(patch(name, **options)) for name,options in values.items()}
            try:
                result = focus_game_window()
            except GameLoginError as exc:
                result = exc
            return result, handles['win32gui.ShowWindow'].call_count, handles['win32api.keybd_event'].call_count

    def test_already_foreground_but_minimized_still_restores_before_return(self):
        result, restored, keys = self.flow()
        self.assertIsNone(result)
        self.assertEqual(1, restored)
        self.assertEqual(0, keys)

    def test_held_modifiers_prevent_restoration(self):
        result, restored, keys = self.flow(held=True)
        self.assertEqual('desktop_busy', result.status)
        self.assertEqual(0, restored)
        self.assertEqual(0, keys)

    def test_failed_restoration_stops_before_game_input(self):
        result, restored, keys = self.flow(persistent=True)
        self.assertEqual('precondition', result.status)
        self.assertEqual(1, restored)
        self.assertEqual(0, keys)
