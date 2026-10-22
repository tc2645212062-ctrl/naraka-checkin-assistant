import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from assistant.game_login import classify_page, ensure_game_login, GameLoginError


class SavedDialogTests(unittest.TestCase):
    def test_real_ocr_error_cannot_select_underlying_title(self):
        state = classify_page('game', ['网易游戏', '进入学戏', '使用其他账号登录', '点击屏幕进入'])
        self.assertEqual('saved_account', state.action)

    def test_missing_button_text_still_requires_native_button_verification(self):
        self.assertEqual('saved_account', classify_page('game', ['网易游戏', '使用其他账号登录', '点击屏幕进入']).action)

    def test_challenge_and_active_match_keep_priority_over_saved_dialog(self):
        for labels, expected in ((['安全验证'], 'manual_required'), (['当前怪物等级'], 'desktop_busy')):
            state = classify_page('game', labels + ['使用其他账号登录', '点击屏幕进入'])
            self.assertEqual(expected, state.status)
            self.assertEqual('', state.action)

    def test_ambiguous_saved_dialog_is_not_a_title_page(self):
        state = classify_page('game', ['使用其他账号登录', '使用其他账号登录', '点击屏幕进入'])
        self.assertEqual('unrecognized', state.status)
        self.assertEqual('', state.action)

    def run_flow(self, pages, reject=0, wrong_details=False):
        calls = []
        clock = [0.0]
        iterator = iter(pages)
        class Broker:
            def __init__(self, _): self.rejected = 0
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def request(self, command):
                calls.append(command)
                if command == 'observe':
                    return {'kind': 'game', 'png': base64.b64encode(b'frame').decode()}
                if command == 'act:saved_account' and self.rejected < reject:
                    self.rejected += 1
                    raise GameLoginError('unrecognized', 'control label loading', {
                        'code': 'control_not_ready', 'action': 'saved_account', 'kind': 'game',
                        'input_sent': True if wrong_details else False,
                        'png': base64.b64encode(b'rejected').decode()})
                return {}
        with tempfile.TemporaryDirectory() as folder, \
             patch('assistant.game_login.time.monotonic', side_effect=lambda:clock[0]), \
             patch('assistant.game_login.time.sleep', side_effect=lambda delay:clock.__setitem__(0, clock[0]+delay)):
            result = ensure_game_login(Path(folder), client_factory=Broker,
                                       read_page=lambda _:next(iterator), close_started_game=False)
        return result, calls

    def test_delayed_saved_login_transition_waits_without_resending(self):
        dialog = ['进入学戏', '使用其他账号登录', '点击屏幕进入']
        result, calls = self.run_flow([dialog] * 4 + [['开始游戏', '英雄', '大厅']])
        self.assertEqual('ready', result.status)
        self.assertEqual(['act:saved_account'], [call for call in calls if call.startswith('act:')])

    def test_unchanged_dialog_wait_is_bounded_without_duplicate_click(self):
        dialog = ['进入学戏', '使用其他账号登录', '点击屏幕进入']
        result, calls = self.run_flow([dialog] * 50)
        self.assertEqual('unrecognized', result.status)
        self.assertNotIn('模块错误', result.reason)
        self.assertLess(calls.count('observe'), 30)
        self.assertEqual(['act:saved_account'], [call for call in calls if call.startswith('act:')])

    def test_explicit_preinput_rejection_reobserves_before_a_later_click(self):
        dialog = ['进入学戏', '使用其他账号登录', '点击屏幕进入']
        result, calls = self.run_flow([dialog] * 3 + [['开始游戏', '英雄', '大厅']], reject=2)
        self.assertEqual('ready', result.status)
        self.assertEqual(['observe', 'act:saved_account'] * 3 + ['observe'], calls)

    def test_persistent_preinput_rejection_stops_at_three(self):
        result, calls = self.run_flow([['进入学戏', '使用其他账号登录']] * 4, reject=99)
        self.assertEqual('unrecognized', result.status)
        self.assertEqual(3, calls.count('act:saved_account'))

    def test_uncertain_action_rejection_is_never_replayed(self):
        result, calls = self.run_flow([['使用其他账号登录', '进入游戏']], reject=99, wrong_details=True)
        self.assertEqual('unrecognized', result.status)
        self.assertEqual(['observe', 'act:saved_account'], calls)

