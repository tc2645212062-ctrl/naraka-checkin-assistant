import base64
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from assistant.game_login import classify_page, ensure_game_login


class GamePageTests(unittest.TestCase):
    def test_courier_lobby_uses_prepare_button_without_starting_match(self):
        state = classify_page('game', ['英雄兵器营地物资聚义厅', '大厅', '无间镖客', '出战准备'])
        self.assertEqual('ready', state.status)
        self.assertEqual('', state.action)
        self.assertEqual('waiting', classify_page('game', ['出战准备', '大厅']).status)
        self.assertEqual('desktop_busy', classify_page('game', ['暗域正在蔓延', '大厅', '兵器', '出战准备']).status)

    def test_memory_warning_is_not_the_underlying_start_button(self):
        state=classify_page('launcher',['游戏运行内存最小为3GB','您的当前可用内存为2.3GB','是否继续启动游戏','继续启动','取消','启动游戏'])
        self.assertEqual('precondition',state.status)
        self.assertEqual('',state.action)

    def test_memory_warning_stops_login_without_any_click(self):
        commands=[]
        class Broker:
            def __init__(self,_):pass
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def request(self,command):
                commands.append(command)
                return {'kind':'launcher','png':base64.b64encode(b'frame').decode()}
        with tempfile.TemporaryDirectory() as folder:
            result=ensure_game_login(Path(folder),client_factory=Broker,read_page=lambda _:['当前可用内存不足','继续启动','启动游戏'])
        self.assertEqual('precondition',result.status)
        self.assertEqual(['observe'],commands)

    def test_login_is_not_lobby_and_manual_validation_wins(self):
        self.assertEqual('saved_account', classify_page('game', ['进入游戏', '使用其他账号登录']).action)
        self.assertEqual('manual_required', classify_page('game', ['进入游戏', '使用其他账号登录', '验证码']).status)
        self.assertEqual('network_error', classify_page('game', ['连接服务器失败']).status)
        self.assertEqual('waiting', classify_page('game', ['英雄']).status)
        self.assertEqual('ready', classify_page('game', ['开始游戏', '英雄', '大厅']).status)
        self.assertEqual('ready', classify_page('game', ['开始征神', '英雄兵器', '大厅']).status)

    def test_all_observed_actions_and_no_match_button(self):
        for kind, lines, action in [
            ('launcher', ['启动游戏'], 'launcher_start'),
            ('game', ['公告', 'Space', '继续'], 'announcement'),
            ('game', ['点击屏幕进入'], 'title_enter'),
            ('game', ['白泽月卡', '白泽币'], 'dismiss_monthly'),
        ]:
            with self.subTest(action=action):
                self.assertEqual(action, classify_page(kind, lines).action)
        self.assertFalse(classify_page('game', ['开始游戏', '英雄']).action)

    def test_active_match_stops_without_input(self):
        result, commands = self.run_flow([['黄玉秘境·三人','当前怪物等级','击败首领，夺取宝物','已强化次数']])
        self.assertEqual('desktop_busy',result.status)
        self.assertFalse(any(c.startswith('act:') for c in commands))

    def test_unknown_page_stops_after_bounded_observations(self):
        result, commands = self.run_flow([['unknown']] * 5)
        self.assertEqual('unrecognized',result.status)
        self.assertEqual(5,commands.count('observe'))

    def run_flow(self, pages, cancel=None):
        commands = []
        class FakeBroker:
            def __init__(self, _): pass
            def __enter__(self): return self
            def __exit__(self, *_): commands.append('closed')
            def request(self, command):
                commands.append(command)
                return {'kind': 'game', 'png': base64.b64encode(b'fake image').decode()}
        iterator = iter(pages)
        with tempfile.TemporaryDirectory() as temp, patch('assistant.game_login.time.sleep'):
            result = ensure_game_login(Path(temp), cancel=cancel, client_factory=FakeBroker, read_page=lambda _: next(iterator))
        return result, commands

    def test_success_requires_observed_lobby(self):
        result, commands = self.run_flow([['公告','Space','继续'], ['进入游戏','使用其他账号登录'], ['点击屏幕进入'], ['开始游戏','英雄','大厅']])
        self.assertEqual('ready', result.status)
        self.assertEqual(['act:announcement', 'act:saved_account', 'act:title_enter'], [c for c in commands if c.startswith('act:')])
        self.assertEqual('closed', commands[-1])

    def test_cold_launch_can_wait_longer_than_four_focus_attempts(self):
        from assistant.game_login import GameLoginError
        commands=[]
        class ColdBroker:
            def __init__(self,_):self.observations=0
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def request(self,command):
                commands.append(command)
                if command.startswith('act:'):return {}
                self.observations+=1
                if 2 <= self.observations <= 8:
                    raise GameLoginError('desktop_busy','游戏窗口未能切到前台，未发送游戏操作')
                return {'kind':'launcher' if self.observations==1 else 'game','png':base64.b64encode(b'image').decode()}
        pages=iter([['启动游戏'],['开始游戏','大厅','英雄']])
        with tempfile.TemporaryDirectory() as folder, patch('assistant.game_login.time.sleep'):
            result=ensure_game_login(Path(folder),client_factory=ColdBroker,read_page=lambda _:next(pages),close_started_game=False)
        self.assertEqual('ready',result.status)
        self.assertEqual(['act:launcher_start'],[c for c in commands if c.startswith('act:')])

    def test_loading_after_title_waits_without_additional_input(self):
        result, commands = self.run_flow([['点击屏幕进入']] + [['加载画面']] * 8 + [['开始游戏','英雄','大厅']])
        self.assertEqual('ready', result.status)
        self.assertEqual(['act:title_enter'], [c for c in commands if c.startswith('act:')])

    def test_unchanged_page_does_not_repeat_input(self):
        result, commands = self.run_flow([['点击屏幕进入'], ['点击屏幕进入']])
        self.assertEqual('unrecognized', result.status)
        self.assertEqual(1, commands.count('act:title_enter'))

    def test_captcha_network_and_cancel_never_click(self):
        for page, expected in [(['验证码'], 'manual_required'), (['网络异常'], 'network_error')]:
            result, commands = self.run_flow([page])
            self.assertEqual(expected, result.status)
            self.assertFalse(any(c.startswith('act:') for c in commands))
        cancel=threading.Event(); cancel.set()
        result, commands=self.run_flow([],cancel)
        self.assertEqual('interrupted',result.status)
        self.assertNotIn('observe',commands)


class TitleControlRecoveryTests(unittest.TestCase):
    def run_rejected_title(self, rejected=1, pages=None, details_override=None,
                           structured=True, cancel=None):
        from assistant.game_login import GameLoginError
        commands, events = [], []
        class Broker:
            def __init__(self, _): self.attempts = 0
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def request(self, command):
                commands.append(command)
                if command == 'act:title_enter':
                    self.attempts += 1
                    if self.attempts <= rejected:
                        details = {
                            'code': 'control_not_ready', 'action': 'title_enter',
                            'kind': 'game', 'input_sent': False, 'match': .773,
                            'png': base64.b64encode(b'rejected title frame').decode(),
                        } if structured else {}
                        details.update(details_override or {})
                        raise GameLoginError('unrecognized',
                            'unrecognized: control template mismatch (0.773)', details)
                    return {'ok': True}
                return {'kind': 'game', 'png': base64.b64encode(b'observed frame').decode()}
        iterator = iter(pages or [['点击屏幕进入']] * (min(rejected, 3) + 1)
                        + [['开始游戏', '英雄', '大厅']])
        with tempfile.TemporaryDirectory() as folder, patch('assistant.game_login.time.sleep'):
            result = ensure_game_login(Path(folder), event=events.append, cancel=cancel,
                client_factory=Broker, read_page=lambda _: next(iterator))
            images = [p.read_bytes() for p in sorted(Path(folder).glob('game-title-rejected-*.png'))]
            metadata = [json.loads(p.read_text(encoding='utf-8'))
                        for p in sorted(Path(folder).glob('game-title-rejected-*.json'))]
        return result, commands, images, metadata, events

    def test_proven_rejection_reobserves_before_retry_and_preserves_evidence(self):
        result, commands, images, metadata, _ = self.run_rejected_title()
        self.assertEqual('ready', result.status)
        self.assertEqual(['observe', 'act:title_enter', 'observe', 'act:title_enter', 'observe'], commands)
        self.assertEqual([b'rejected title frame'], images)
        self.assertEqual(False, metadata[0]['input_sent'])
        self.assertEqual(.773, metadata[0]['match'])
        self.assertNotIn('png', metadata[0])

    def test_permanent_rejection_is_bounded_to_three_attempts(self):
        result, commands, images, _, _ = self.run_rejected_title(9)
        self.assertEqual('unrecognized', result.status)
        self.assertEqual(3, commands.count('act:title_enter'))
        self.assertEqual(3, commands.count('observe'))
        self.assertEqual(3, len(images))

    def test_fresh_verification_or_match_page_prevents_another_attempt(self):
        for page, expected in [(['安全验证', '验证码'], 'manual_required'),
                               (['暗域正在蔓延'], 'desktop_busy'),
                               (['网络异常'], 'network_error')]:
            with self.subTest(page=page):
                result, commands, _, _, _ = self.run_rejected_title(pages=[['点击屏幕进入'], page])
                self.assertEqual(expected, result.status)
                self.assertEqual(1, commands.count('act:title_enter'))

    def test_no_retry_without_exact_native_proof_that_no_input_was_sent(self):
        for override in ({'code': 'other'}, {'action': 'saved_account'},
                         {'kind': 'launcher'}, {'input_sent': True}, {'input_sent': 0}):
            with self.subTest(override=override):
                result, commands, images, _, _ = self.run_rejected_title(details_override=override)
                self.assertEqual('unrecognized', result.status)
                self.assertEqual(['observe', 'act:title_enter'], commands)
                self.assertEqual([], images)
        result, commands, _, _, _ = self.run_rejected_title(structured=False)
        self.assertEqual('unrecognized', result.status)
        self.assertEqual(['observe', 'act:title_enter'], commands)

    def test_accepted_action_is_never_replayed_even_after_rejected_attempt(self):
        result, commands, _, _, _ = self.run_rejected_title(pages=[['点击屏幕进入']] * 3)
        self.assertEqual('unrecognized', result.status)
        self.assertEqual(2, commands.count('act:title_enter'))  # One rejection, one accepted input.

    def test_loading_after_rejection_is_observed_without_another_click(self):
        result, commands, _, _, _ = self.run_rejected_title(
            pages=[['点击屏幕进入'], ['加载中'], ['开始游戏', '大厅', '英雄']])
        self.assertEqual('ready', result.status)
        self.assertEqual(1, commands.count('act:title_enter'))

    def test_cancel_during_rejection_wait_stops_before_reobservation(self):
        from unittest.mock import Mock
        cancel = Mock(is_set=lambda: False, wait=lambda _: True)
        result, commands, _, _, _ = self.run_rejected_title(cancel=cancel)
        self.assertEqual('interrupted', result.status)
        self.assertEqual(['observe', 'act:title_enter'], commands)


class ForegroundTests(unittest.TestCase):
    def test_alt_released_before_switching_to_high_integrity_window(self):
        import win32api, win32gui, win32con
        from unittest.mock import Mock
        from assistant.game_login import focus_game_window
        events=[]
        def foreground(_): events.append('focus')
        def key(vk, scan, flags, extra): events.append('up' if flags & win32con.KEYEVENTF_KEYUP else 'down')
        with patch('assistant.desktop.windows.desktop_available',return_value=True), \
             patch('win32gui.EnumWindows',side_effect=lambda callback, arg: callback(123,arg)), \
             patch('win32gui.IsWindowVisible',return_value=True), \
             patch('win32gui.IsIconic',return_value=False), \
             patch('win32gui.GetWindowRect',return_value=(0,0,1536,930)), \
             patch('win32process.GetWindowThreadProcessId',return_value=(1,2)), \
             patch('psutil.Process',return_value=Mock(exe=lambda:r'E:\Naraka\tool\LootHoarder.exe')), \
             patch('win32gui.GetForegroundWindow',side_effect=[999,999,123]), \
             patch('win32gui.GetClassName',return_value='OtherWindow'), \
             patch('win32api.GetAsyncKeyState',return_value=0), \
             patch('win32gui.ShowWindow'), \
             patch('win32gui.SetForegroundWindow',side_effect=foreground), \
             patch('win32api.keybd_event',side_effect=key), \
             patch('assistant.game_login.time.sleep'):
            focus_game_window()
        self.assertEqual(['focus','down','up','focus'],events)


ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / 'native' / 'dist' / 'GameInputBroker.exe'


@unittest.skipUnless(EXE.is_file(), 'Build-GameInput.ps1 required for native template tests')
class NativeTemplateTests(unittest.TestCase):
    def test_matching_controls_and_blank_rejection(self):
        from PIL import Image
        patches = {
            'launcher_start': (1538,932,1237,788), 'announcement':(2560,1600,1283,1224),
            'saved_account':(2560,1600,1225,886), 'title_enter':(2560,1600,1155,1343),
            'dismiss_monthly':(2560,1600,1860,525),
            'exit_lobby':(2560,1600,1188,22), 'lobby_nav':(2560,1600,904,23),
        }
        with tempfile.TemporaryDirectory() as temp:
            for action,(w,h,x,y) in patches.items():
                with self.subTest(action=action):
                    image=Image.new('RGB',(w,h),(25,25,25))
                    path=Path(temp)/'frame.png'; image.save(path)
                    def score():
                        result=subprocess.run([str(EXE),'--match',action,str(path)],capture_output=True,timeout=10)
                        self.assertEqual(0,result.returncode)
                        return float(result.stdout)
                    self.assertLess(score(),.78)
                    with Image.open(ROOT/'native'/'templates'/(action+'.png')) as template:
                        image.paste(template,(x,y))
                    image.save(path)
                    self.assertGreater(score(),.99)
                    image.resize((round(w*1.25),round(h*1.25))).save(path)
                    self.assertGreater(score(),.78)

    def test_small_window_border_shift_but_not_distant_control(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'shift.png'
            template = Image.open(ROOT/'native/templates/launcher_start.png')
            def score(x, y):
                image = Image.new('RGB', (1536,930), (25,25,25))
                image.paste(template, (x,y)); image.save(path)
                result = subprocess.run([str(EXE),'--match','launcher_start',str(path)],capture_output=True,timeout=10)
                self.assertEqual(0,result.returncode)
                return float(result.stdout)
            self.assertGreater(score(1234,785),.78)
            self.assertLess(score(1200,750),.78)


class BrokerFocusRecoveryTests(unittest.TestCase):
    def test_native_rejection_details_reach_orchestrator(self):
        from assistant.game_login import BrokerClient, GameLoginError
        details={'ok':False,'error':'unrecognized: control template mismatch (0.641)',
                 'code':'control_not_ready','action':'exit_lobby','kind':'game','input_sent':False,'png':'ZnJhbWU='}
        broker=BrokerClient();broker.pipe=1;broker.buffer=(json.dumps(details)+'\n').encode()
        with patch('assistant.game_login.focus_game_window'), patch('win32file.WriteFile'):
            with self.assertRaises(GameLoginError) as caught:broker.request('act:exit_lobby')
        self.assertEqual('unrecognized',caught.exception.status)
        self.assertEqual(details,caught.exception.details)

    def test_observe_keeps_broker_alive_but_actions_still_require_focus(self):
        from assistant.game_login import BrokerClient, GameLoginError
        for command,reason,allowed in [('observe','游戏窗口未能切到前台',True),('act:title_enter','游戏窗口未能切到前台',False),('observe','桌面不可交互',False)]:
            with self.subTest(command=command,reason=reason):
                b=BrokerClient();b.pipe=1;b.buffer=b'{"ok":true}\n'
                with patch('assistant.game_login.focus_game_window',side_effect=GameLoginError('desktop_busy',reason)), patch('win32file.WriteFile') as send:
                    if allowed:
                        self.assertTrue(b.request(command)['ok']);send.assert_called_once()
                    else:
                        with self.assertRaises(GameLoginError):b.request(command)
                        send.assert_not_called()


class MissingWindowLoadingTests(unittest.TestCase):
    def run_gap(self, gap, initially_missing=False):
        from assistant.game_login import GameLoginError
        from unittest.mock import Mock
        commands=[]
        class Broker:
            def __init__(self,_): self.observations=0
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def request(self,command):
                commands.append(command)
                if command.startswith('act:'):return {}
                self.observations+=1
                if (initially_missing and self.observations==1) or 2 <= self.observations <= gap+1:
                    raise GameLoginError('entry_unavailable','entry_unavailable: no game window')
                return {'kind':'launcher' if self.observations==1 else 'game','png':base64.b64encode(b'frame').decode()}
        pages=iter(([['启动游戏']] if not initially_missing else [])+[['开始游戏','大厅','英雄']])
        with tempfile.TemporaryDirectory() as folder,patch('assistant.game_login.time.sleep'),patch('subprocess.run',return_value=Mock(returncode=0)) as launch:
            result=ensure_game_login(Path(folder),client_factory=Broker,read_page=lambda _:next(pages),close_started_game=False)
        return result,commands,launch.call_count

    def test_missing_window_during_launch_is_waited_for_without_relaunch(self):
        result,commands,launches=self.run_gap(7)
        self.assertEqual('ready',result.status)
        self.assertEqual(1,commands.count('act:launcher_start'))
        self.assertEqual(0,launches)

    def test_initial_launcher_can_appear_after_more_than_five_seconds(self):
        result,commands,launches=self.run_gap(7,True)
        self.assertEqual('ready',result.status)
        self.assertEqual(1,launches)

    def test_permanently_missing_window_is_bounded_and_retryable(self):
        result,commands,launches=self.run_gap(99)
        self.assertEqual('precondition',result.status)
        self.assertLessEqual(commands.count('observe'),46)
        self.assertEqual(1,commands.count('act:launcher_start'))
        self.assertEqual(0,launches)


class AutomaticGameExitTests(unittest.TestCase):
    def test_rejected_exit_reobserves_lobby_and_never_repeats_accepted_close(self):
        result, commands, images, waits = self.run_rejected_exit(1)
        self.assertEqual('ready', result.status)
        self.assertEqual(['act:launcher_start', 'act:exit_lobby', 'act:exit_lobby'], [c for c in commands if c.startswith('act:')])
        self.assertEqual(['observe', 'act:exit_lobby', 'observe', 'act:exit_lobby'], commands[-4:])
        self.assertEqual(1, waits)
        self.assertEqual([b'rejected action frame'], images)

    def test_permanent_exit_rejection_is_bounded_and_does_not_wait_for_exit(self):
        result, commands, images, waits = self.run_rejected_exit(9)
        self.assertEqual('unrecognized', result.status)
        self.assertEqual(3, commands.count('act:exit_lobby'))
        self.assertEqual(3, len(images))
        self.assertEqual(0, waits)

    def test_exit_rejection_does_not_retry_if_new_page_requires_verification(self):
        result, commands, _, waits = self.run_rejected_exit(1, ['安全验证', '验证码'])
        self.assertEqual('manual_required', result.status)
        self.assertEqual(1, commands.count('act:exit_lobby'))
        self.assertEqual(0, waits)

    def test_exit_error_without_proof_no_input_was_sent_is_not_retried(self):
        result, commands, _, waits = self.run_rejected_exit(1, structured=False)
        self.assertEqual('unrecognized', result.status)
        self.assertEqual(1, commands.count('act:exit_lobby'))
        self.assertEqual(0, waits)

    def run_rejected_exit(self, rejected, next_page=None, structured=True):
        from assistant.game_login import GameLoginError
        commands=[]
        class Broker:
            def __init__(self,_): self.seen=0; self.rejected=0
            def __enter__(self): return self
            def __exit__(self,*_): pass
            def request(self,command):
                commands.append(command)
                if command=='act:exit_lobby':
                    self.rejected+=1
                    if self.rejected<=rejected:
                        if not structured:
                            raise GameLoginError('unrecognized', 'unrecognized: control template mismatch (0.641)')
                        raise GameLoginError('unrecognized', 'unrecognized: control template mismatch (0.641)', {
                            'code':'control_not_ready', 'action':'exit_lobby', 'kind':'game', 'input_sent':False,
                            'png':base64.b64encode(b'rejected action frame').decode(), 'match':0.641})
                    return {}
                if command.startswith('act:'): return {}
                self.seen+=1
                return {'kind':'launcher' if self.seen==1 else 'game', 'png':base64.b64encode(b'frame').decode()}
        pages=iter([['启动游戏'], ['开始游戏','大厅','英雄']] +
                   [next_page or ['开始游戏','大厅','英雄']]*4)
        with tempfile.TemporaryDirectory() as folder, patch('assistant.game_login.time.sleep'), \
                patch('assistant.game_login.wait_for_game_exit',return_value=True) as wait:
            result=ensure_game_login(Path(folder),client_factory=Broker,read_page=lambda _:next(pages))
            images=[p.read_bytes() for p in sorted(Path(folder).glob('game-exit-rejected-*.png'))]
        return result,commands,images,wait.call_count

    def test_only_game_started_by_this_run_exits_after_lobby(self):
        for started,closed,expected in [(True,True,'ready'),(False,True,'ready'),(True,False,'precondition')]:
            with self.subTest(started=started,closed=closed):
                commands=[]
                class Broker:
                    def __init__(self,_):self.seen=0
                    def __enter__(self):return self
                    def __exit__(self,*_):pass
                    def request(self,command):
                        commands.append(command)
                        if command.startswith('act:'):return {}
                        self.seen+=1
                        return {'kind':'launcher' if started and self.seen==1 else 'game','png':base64.b64encode(b'frame').decode()}
                pages=iter(([['启动游戏']] if started else [])+[['开始游戏','大厅','英雄']])
                with tempfile.TemporaryDirectory() as folder,patch('assistant.game_login.time.sleep'),patch('assistant.game_login.wait_for_game_exit',return_value=closed) as wait:
                    result=ensure_game_login(Path(folder),client_factory=Broker,read_page=lambda _:next(pages))
                self.assertEqual(expected,result.status)
                self.assertEqual(started,'act:exit_lobby' in commands)
                self.assertEqual(int(started),wait.call_count)

class PromotionRecoveryTests(unittest.TestCase):
    def test_promotion_esc_is_automatic_before_courier_lobby_exit(self):
        commands=[]
        class Broker:
            def __init__(self,_): self.seen=0
            def __enter__(self): return self
            def __exit__(self,*_): pass
            def request(self,command):
                commands.append(command)
                if command.startswith('act:'):return {}
                self.seen+=1
                return {'kind':'launcher' if self.seen==1 else 'game','png':base64.b64encode(b'frame').decode()}
        pages=iter([['启动游戏'], ['大厅','兵器','出战准备','今日不再提示','Esc','返回'], ['大厅','兵器','出战准备']])
        with tempfile.TemporaryDirectory() as folder, patch('assistant.game_login.time.sleep'), patch('assistant.game_login.wait_for_game_exit',return_value=True):
            result=ensure_game_login(Path(folder),client_factory=Broker,read_page=lambda _:next(pages))
        self.assertEqual('ready',result.status)
        self.assertEqual(['act:launcher_start','act:dismiss_promotion','act:exit_lobby'],[c for c in commands if c.startswith('act:')])

    @unittest.skipUnless(EXE.is_file(),'Build-GameInput.ps1 required')
    def test_both_lobby_layouts_pass_but_partial_controls_and_overlay_do_not(self):
        from PIL import Image
        def validate(path):
            result=subprocess.run([str(EXE),'--check-exit',str(path)],capture_output=True,timeout=15)
            self.assertEqual(0,result.returncode,result.stderr.decode(errors='replace'))
            return json.loads(result.stdout)
        for name in ('lobby-current','courier-lobby'):
            with self.subTest(name=name):
                result=validate(ROOT/'tests/fixtures'/f'{name}.png')
                self.assertTrue(result['allowed'],result)
                self.assertFalse(result['input_sent'])
        with tempfile.TemporaryDirectory() as folder:
            image=Image.new('RGB',(2560,1600),(25,25,25))
            path=Path(folder)/'frame.png'
            image.save(path)
            self.assertFalse(validate(path)['allowed'])
            image.paste(Image.open(ROOT/'native/templates/exit_lobby.png'),(1188,22))
            image.save(path)
            self.assertFalse(validate(path)['allowed'])
            image.paste(Image.open(ROOT/'native/templates/lobby_identity.png'),(1100,23))
            image.save(path)
            self.assertTrue(validate(path)['allowed'])
            image.paste(Image.open(ROOT/'native/templates/promotion_notice.png'),(2205,1545))
            image.save(path)
            self.assertFalse(validate(path)['allowed'])

    def test_real_courier_fixture_is_classified_by_local_ocr(self):
        from assistant.desktop.vision import read_text
        state=classify_page('game',[item.text for item in read_text(ROOT/'tests/fixtures/courier-lobby.png')])
        self.assertEqual('ready',state.status)
        self.assertEqual('',state.action)

    def test_outdated_broker_fails_before_starting_game(self):
        from assistant.game_login import require_broker_version,GameLoginError
        from unittest.mock import Mock
        with patch('subprocess.run',return_value=Mock(returncode=2,stdout=b'')):
            with self.assertRaises(GameLoginError) as caught:require_broker_version()
        self.assertEqual('precondition',caught.exception.status)
        with patch('subprocess.run',return_value=Mock(returncode=0,stdout=b'2\r\n')):
            with self.assertRaises(GameLoginError):require_broker_version()
        with patch('subprocess.run',return_value=Mock(returncode=0,stdout=b'3\r\n')):
            with self.assertRaises(GameLoginError):require_broker_version()
        with patch('subprocess.run',return_value=Mock(returncode=0,stdout=b'4\r\n')):require_broker_version()
    def test_startup_promotion_is_dismissed_before_underlying_lobby(self):
        lines=['大厅','英雄','开始游戏','今日不再提示','Esc','返回']
        state=classify_page('game',lines)
        self.assertEqual('advance',state.status)
        self.assertEqual('dismiss_promotion',state.action)

    def test_other_escape_labels_do_not_trigger_promotion_action(self):
        state=classify_page('game',['加载中','Esc','返回'])
        self.assertEqual('waiting',state.status)
        state=classify_page('game',['暗域正在蔓延','今日不再提示','Esc','返回'])
        self.assertEqual('desktop_busy',state.status)

    @unittest.skipUnless(EXE.is_file(),'Build-GameInput.ps1 required')
    def test_real_changed_lobby_and_promotion_controls(self):
        for action,name in [('lobby_nav_current','lobby-current'),('exit_lobby','lobby-current'),('dismiss_promotion','promotion-footer'),('promotion_notice','promotion-footer')]:
            with self.subTest(action=action):
                result=subprocess.run([str(EXE),'--match',action,str(ROOT/'tests/fixtures'/f'{name}.png')],capture_output=True,timeout=15)
                self.assertEqual(0,result.returncode)
                self.assertGreater(float(result.stdout),.78)
