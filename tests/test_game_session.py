import base64
from contextlib import ExitStack
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import psutil

from assistant.game_login import ensure_game_login
from assistant.game_session import GameProcessSession, GAME, LAUNCHER


class Process:
    def __init__(self, pid, exe, created, parents=(), children=(), denied=False):
        self.pid, self.path, self.created = pid, exe, created
        self.ancestors, self.descendants = list(parents), list(children)
        self.alive, self.denied = True, denied
        self.terminate = Mock(side_effect=self.end)
    def exe(self): return self.path
    def create_time(self): return self.created
    def ppid(self): return self.ancestors[0].pid if self.ancestors else 0
    def parents(self): return self.ancestors
    def children(self, recursive=False): return [p for p in self.descendants if p.alive]
    def end(self):
        if self.denied:
            raise psutil.AccessDenied(self.pid)
        self.alive = False


class OwnershipTests(unittest.TestCase):
    def run_session(self, mode):
        launcher = Process(100, LAUNCHER, 100)
        child = Process(201, r'e:\naraka\program\helper.exe', 1003)
        game = Process(200, GAME, 1002, [launcher], [child], denied=mode == 'denied')
        initial = [launcher, game] if mode == 'preexisting' else [launcher]
        current = list(initial)
        processes = {p.pid:p for p in [launcher, game, child]}
        def lookup(pid):
            process = processes[pid]
            if not process.alive:
                raise psutil.NoSuchProcess(pid)
            return process
        def inventory():
            return [GameProcessSession.identity(p) for p in current if p.alive]
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            stack.enter_context(patch.object(GameProcessSession, 'inventory', side_effect=inventory))
            stack.enter_context(patch('assistant.game_session.psutil.Process', side_effect=lookup))
            stack.enter_context(patch('assistant.game_session.psutil.wait_procs', return_value=([], [])))
            stack.enter_context(patch('assistant.game_session.time.time', return_value=1001))
            close = stack.enter_context(patch.object(GameProcessSession, 'close_windows'))
            session = GameProcessSession(Path(folder))
            session.expect_game()
            current[:] = [launcher, game]
            if mode != 'no-accepted-launch':
                session.accepted_game()
            if mode == 'pid-reused':
                processes[200] = Process(200, GAME, 2000)
            if mode == 'normal-close':
                close.side_effect = lambda pids:[setattr(processes[pid], 'alive', False) for pid in pids]
            remaining = session.cleanup(close=mode != 'retain-success')
            record = (Path(folder) / 'process-session.json').read_text(encoding='utf-8')
        return remaining, close, launcher, game, child, record

    def test_new_game_and_verified_descendants_are_force_closed_after_bounded_soft_wait(self):
        remaining, close, launcher, game, child, record = self.run_session('owned')
        self.assertEqual([], remaining)
        self.assertEqual({200, 201}, close.call_args.args[0])
        game.terminate.assert_called_once(); child.terminate.assert_called_once()
        launcher.terminate.assert_not_called()
        self.assertIn('"remaining": []', record)

    def test_existing_user_game_is_never_closed(self):
        remaining, close, _, game, child, _ = self.run_session('preexisting')
        self.assertEqual([], remaining)
        close.assert_not_called(); game.terminate.assert_not_called(); child.terminate.assert_not_called()

    def test_pid_reuse_is_not_an_ownership_match(self):
        remaining, close, _, game, child, _ = self.run_session('pid-reused')
        self.assertEqual([], remaining)
        # The original target identity vanished; neither its replacement nor
        # the former children can be enumerated or terminated through that PID.
        game.terminate.assert_not_called(); child.terminate.assert_not_called()
        close.assert_not_called()

    def test_unaccepted_start_does_not_claim_an_unrelated_new_game(self):
        _, close, _, game, child, _ = self.run_session('no-accepted-launch')
        close.assert_not_called(); game.terminate.assert_not_called(); child.terminate.assert_not_called()

    def test_success_keep_setting_does_not_close_owned_game(self):
        _, close, _, game, child, _ = self.run_session('retain-success')
        close.assert_not_called(); game.terminate.assert_not_called(); child.terminate.assert_not_called()

    def test_graceful_exit_does_not_force_terminate(self):
        remaining, _, _, game, child, _ = self.run_session('normal-close')
        self.assertEqual([], remaining)
        game.terminate.assert_not_called(); child.terminate.assert_not_called()

    def test_denied_termination_retains_honest_residual(self):
        remaining, _, _, game, _, record = self.run_session('denied')
        self.assertEqual([200], [row['pid'] for row in remaining])
        game.terminate.assert_called_once()
        self.assertIn('AccessDenied', record)


class FlowCleanupTests(unittest.TestCase):
    def run_flow(self, mode, keep=False, residual=False):
        calls = []
        clock = [0.0]
        class Broker:
            def __init__(self, _): pass
            def __enter__(self): return self
            def __exit__(self, *_): calls.append('broker_closed')
            def request(self, command):
                calls.append(command)
                if mode == 'exception':
                    raise RuntimeError('injected task failure')
                return {'kind':'game', 'png':base64.b64encode(b'frame').decode()}
        session = Mock()
        session.cleanup.side_effect = lambda **kwargs:(calls.append('cleanup') or ([{'pid':200}] if residual else []))
        if mode == 'manual': labels = ['验证码', '点击屏幕进入']
        elif mode == 'ready': labels = ['开始游戏', '大厅', '英雄']
        else: labels = ['进入学戏', '使用其他账号登录', '点击屏幕进入']
        stop = threading.Event()
        if mode == 'cancel': stop.set()
        with tempfile.TemporaryDirectory() as folder, \
             patch('assistant.game_login.time.monotonic', side_effect=lambda:clock[0]), \
             patch('assistant.game_login.time.sleep', side_effect=lambda seconds:clock.__setitem__(0, clock[0]+seconds)):
            result = ensure_game_login(Path(folder), client_factory=Broker, read_page=lambda _:labels,
                        cancel=stop if mode == 'cancel' else None, timeout=20,
                        close_started_game=not keep, session_factory=lambda *_:session)
        return result, calls, session

    def test_login_never_advances_times_out_and_cleans_after_broker_exit(self):
        result, calls, session = self.run_flow('stuck')
        self.assertEqual('precondition', result.status)
        self.assertIn('超时', result.reason)
        session.cleanup.assert_called_once_with(close=True)
        self.assertEqual(['broker_closed', 'cleanup'], calls[-2:])
        self.assertEqual(1, calls.count('act:saved_account'))

    def test_manual_validation_stops_input_and_cleans_owned_game(self):
        result, calls, session = self.run_flow('manual')
        self.assertEqual('manual_required', result.status)
        self.assertFalse(any(call.startswith('act:') for call in calls))
        session.cleanup.assert_called_once_with(close=True)

    def test_task_exception_still_cleans(self):
        result, _, session = self.run_flow('exception')
        self.assertEqual('unrecognized', result.status)
        session.cleanup.assert_called_once_with(close=True)

    def test_cancel_still_cleans(self):
        result, _, session = self.run_flow('cancel')
        self.assertEqual('interrupted', result.status)
        session.cleanup.assert_called_once_with(close=True)

    def test_success_keeps_existing_setting(self):
        result, _, session = self.run_flow('ready', keep=True)
        self.assertEqual('ready', result.status)
        session.cleanup.assert_called_once_with(close=False)

    def test_success_is_not_returned_if_exit_has_unverified_residual(self):
        result, _, session = self.run_flow('ready', residual=True)
        self.assertEqual('precondition', result.status)
        self.assertIn('残留', result.reason)
        session.cleanup.assert_called_once_with(close=True)

