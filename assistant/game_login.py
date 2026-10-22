"""Normal-privilege orchestration for the installed, bounded game input broker."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


TASK = 'NarakaCheckinAssistant-GameInput'
BROKER = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'NarakaCheckinAssistant' / 'GameInputBroker.exe'


@dataclass(frozen=True)
class GameState:
    status: str
    reason: str
    action: str = ''


def classify_page(kind: str, lines: list[str]) -> GameState:
    text = ''.join(''.join(lines).split())
    for token in ('验证码', '安全验证', '扫码登录', '扫描二维码登录', '输入密码', '账号受限', '异地登录'):
        if token in text:
            return GameState('manual_required', '游戏要求人工处理：' + token)
    for token in ('网络连接失败', '连接超时', '连接服务器失败', '网络异常'):
        if token in text:
            return GameState('network_error', '游戏提示：' + token)
    if kind == 'launcher':
        if '内存' in text and '继续启动' in text and ('当前可用' in text or '清理' in text):
            return GameState('precondition', '官方启动器提示可用内存不足；未确认继续启动，等待内存释放后再试')
        if '启动游戏' in text:
            return GameState('advance', '官方启动器已就绪', 'launcher_start')
        return GameState('waiting', '等待启动器检查或更新；不自动确认安装提示')
    if kind != 'game':
        return GameState('unrecognized', '无法识别目标程序')
    if any(token in text for token in ('当前怪物等级', '暗域正在蔓延', '暗域蔓延倒计时')) or ('击败首领' in text and '已强化次数' in text):
        return GameState('desktop_busy', '检测到正在对局，停止操作并保留游戏')
    if '今日不再提示' in text and ('Esc' in text or 'ESC' in text) and '返回' in text:
        return GameState('advance', '关闭已识别的活动宣传弹窗', 'dismiss_promotion')
    if '白泽月卡' in text and ('白泽币' in text or '月卡奖励' in text):
        return GameState('advance', '关闭已识别的月卡弹窗', 'dismiss_monthly')
    if '公告' in text and '继续' in text and ('Space' in text or 'space' in text):
        return GameState('advance', '继续游戏公告', 'announcement')
    if '使用其他账号登录' in text:
        # The saved-account dialog overlays the title screen. Its button OCR
        # can be wrong while the underlying title remains legible. Never click
        # through the dialog; the broker still verifies its fixed button image.
        if text.count('使用其他账号登录') != 1:
            return GameState('unrecognized', '保存账号登录弹窗不唯一，未选择底层标题页')
        return GameState('advance', '识别保存账号登录弹窗，验证按钮后进入', 'saved_account')
    if '点击屏幕进入' in text:
        return GameState('advance', '进入游戏大厅', 'title_enter')
    if any(button in text for button in ('开始游戏', '开始征神', '出战准备')) and '大厅' in text and any(label in text for label in ('英雄', '通行证', '武器', '商城', '兵器')):
        return GameState('ready', '已识别游戏大厅；还需在小程序验证并领取积分')
    return GameState('waiting', '等待游戏加载或识别当前页面')


class GameLoginError(RuntimeError):
    def __init__(self, status: str, reason: str, details: dict | None = None):
        self.status = status
        self.details = details or {}
        super().__init__(reason)


def require_broker_version():
    import subprocess
    checked = subprocess.run([str(BROKER), '--version'], capture_output=True,
                             timeout=5, creationflags=0x08000000)
    if checked.returncode or checked.stdout.strip() != b'5':
        raise GameLoginError('precondition', '游戏操作模块需要更新：请运行 scripts/Install-GameInput.ps1 完成 Windows 管理员授权；本次未启动游戏')


def focus_game_window():
    """Select only known game executables and verify foreground transfer."""
    import psutil
    import win32api
    import win32con
    import win32gui
    import win32process
    from .desktop.windows import desktop_available
    if not desktop_available():
        raise GameLoginError('desktop_busy', '桌面不可交互，停止切换游戏窗口')
    games, launchers = [], []
    allowed = {r'e:\naraka\program\narakabladepoint.exe': games, r'e:\naraka\tool\loothoarder.exe': launchers}
    def collect(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            path = psutil.Process(pid).exe().lower()
            rect = win32gui.GetWindowRect(hwnd)
            if path in allowed and (win32gui.IsIconic(hwnd) or (rect[2]-rect[0] > 700 and rect[3]-rect[1] > 400)):
                allowed[path].append(hwnd)
        except (psutil.Error, win32gui.error):
            pass
    win32gui.EnumWindows(collect, None)
    choices = games or launchers
    if not choices:
        return  # Broker reports no window; caller may start the official launcher.
    if len(choices) != 1:
        raise GameLoginError('unrecognized', '发现多个游戏窗口，停止切换')
    hwnd = choices[0]
    def has_focus():
        foreground = win32gui.GetForegroundWindow()
        if foreground == hwnd:
            return True
        try:
            return (win32gui.GetClassName(foreground) == 'MPAY_LOGIN'
                    and win32process.GetWindowThreadProcessId(foreground)[1] == win32process.GetWindowThreadProcessId(hwnd)[1]
                    and psutil.Process(win32process.GetWindowThreadProcessId(foreground)[1]).exe().lower()
                        == r'e:\naraka\program\narakabladepoint.exe')
        except (psutil.Error, win32gui.error):
            return False
    minimized = win32gui.IsIconic(hwnd)
    if has_focus() and not minimized:
        return
    if any(win32api.GetAsyncKeyState(key) & 0x8000 for key in (win32con.VK_MENU,win32con.VK_CONTROL,win32con.VK_SHIFT)):
        raise GameLoginError('desktop_busy', '用户正在按住修饰键，停止自动操作')
    if minimized:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        for attempt in range(8):
            if not desktop_available():
                raise GameLoginError('desktop_busy', '桌面不可交互，停止等待游戏窗口')
            rect = win32gui.GetWindowRect(hwnd)
            if not win32gui.IsIconic(hwnd) and rect[2]-rect[0] > 700 and rect[3]-rect[1] > 400:
                break
            if attempt < 7:
                time.sleep(.2)
        else:
            raise GameLoginError('precondition', '游戏/启动器窗口未恢复到可截图尺寸；未发送游戏操作')
        if has_focus():
            return
    try:
        win32gui.SetForegroundWindow(hwnd)
    except win32gui.error:
        pass
    if not has_focus():
        # Same reversible foreground-transfer method used by the existing
        # WeChat adapter. No Windows policy or security setting is changed.
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        try:
            time.sleep(.02)
        finally:
            # Release before entering the higher-integrity window; releasing
            # afterwards can be blocked by UIPI and leave Alt logically held.
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except win32gui.error:
            pass
    time.sleep(.2)
    if not has_focus():
        raise GameLoginError('desktop_busy', '游戏窗口未能切到前台，未发送游戏操作')


class BrokerClient:
    def __init__(self, cancel=None):
        self.cancel = cancel
        self.pipe = None
        self.buffer = b''
        self.game_session = None
        self.keep_owned_game = False

    def guard(self):
        if self.cancel and self.cancel.is_set():
            raise GameLoginError('interrupted', '已停止游戏登录')

    def __enter__(self):
        import win32com.client
        import win32security
        import win32file
        import win32con
        from ctypes import wintypes
        self.guard()
        if not BROKER.is_file():
            raise GameLoginError('precondition', '尚未安装游戏自动操作模块，请运行 scripts/Install-GameInput.ps1')
        require_broker_version()
        sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(
            win32security.OpenProcessToken(__import__('win32api').GetCurrentProcess(), win32con.TOKEN_QUERY), win32security.TokenUser)[0])
        service = win32com.client.Dispatch('Schedule.Service')
        service.Connect()
        try:
            task = service.GetFolder('\\').GetTask(TASK)
            definition = task.Definition
            action = definition.Actions.Item(1)
            user = definition.Principal.UserId
            task_sid = user if user.startswith('S-1-') else win32security.ConvertSidToStringSid(win32security.LookupAccountName(None, user)[0])
            if (definition.Actions.Count != 1 or Path(action.Path) != BROKER or action.Arguments
                    or definition.Principal.LogonType != 3 or definition.Principal.RunLevel != 1
                    or task_sid != sid or definition.Triggers.Count):
                raise GameLoginError('precondition', '游戏操作任务配置与授权内容不符')
            task.Run(None)
        except GameLoginError:
            raise
        except Exception as exc:
            raise GameLoginError('precondition', '无法调用已授权游戏模块：' + str(exc)) from exc
        deadline = time.monotonic() + 20
        while self.pipe is None and time.monotonic() < deadline:
            self.guard()
            try:
                self.pipe = win32file.CreateFile(r'\\.\pipe\NarakaGameInput-' + sid,
                    win32con.GENERIC_READ | win32con.GENERIC_WRITE, 0, None, win32con.OPEN_EXISTING, 0, None)
            except Exception:
                time.sleep(.2)
        if self.pipe is None:
            raise GameLoginError('precondition', '已授权模块未建立连接，请查看 Windows 任务记录')
        # Reject another same-user process impersonating the broker pipe.
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetNamedPipeServerProcessId.restype = wintypes.BOOL
        pid = wintypes.DWORD()
        try:
            if not kernel.GetNamedPipeServerProcessId(int(self.pipe), ctypes.byref(pid)):
                raise RuntimeError('cannot verify server PID')
            import psutil
            if Path(psutil.Process(pid.value).exe()) != BROKER:
                raise RuntimeError('unexpected pipe server')
        except Exception as exc:
            self.pipe.Close(); self.pipe = None
            raise GameLoginError('precondition', '无法核对受保护操作模块：' + str(exc)) from exc
        if self.game_session:
            self.game_session.native_expected = True
        return self

    def request(self, command: str):
        import win32file
        import win32pipe
        self.guard()
        if command not in {'observe', 'act:launcher_start', 'act:announcement', 'act:saved_account', 'act:title_enter', 'act:dismiss_monthly', 'act:dismiss_promotion', 'act:exit_lobby'}:
            raise ValueError('Unsupported game operation')
        try:
            focus_game_window()
        except GameLoginError as exc:
            # Observation may ask the already-authorized broker to perform its
            # own focus check. Keep the pipe alive during a slow cold launch;
            # actions and unavailable desktops still stop immediately.
            if not (command == 'observe' and exc.status == 'desktop_busy'
                    and '未能切到前台' in str(exc)):
                raise
        if command == 'act:launcher_start' and self.game_session:
            self.game_session.expect_game()
        win32file.WriteFile(self.pipe, (command + '\n').encode('ascii'))
        deadline = time.monotonic() + 30
        while b'\n' not in self.buffer:
            self.guard()
            if time.monotonic() > deadline:
                raise GameLoginError('unrecognized', '游戏操作模块响应超时')
            available = win32pipe.PeekNamedPipe(self.pipe, 0)[1]
            if available:
                self.buffer += win32file.ReadFile(self.pipe, min(available, 65536))[1]
                if len(self.buffer) > 24_000_000:
                    raise GameLoginError('unrecognized', '游戏画面数据超出上限')
            else:
                time.sleep(.03)
        line, self.buffer = self.buffer.split(b'\n', 1)
        result = json.loads(line)
        if not result.get('ok'):
            reason = result.get('error', 'Unknown error')
            status = reason.split(':', 1)[0]
            if status not in {'desktop_busy', 'unrecognized', 'entry_unavailable', 'timeout'}:
                status = 'unrecognized'
            raise GameLoginError('precondition' if status == 'timeout' else status, reason, result)
        if self.game_session:
            if command == 'act:launcher_start':
                self.game_session.accepted_game()
            else:
                self.game_session.discover()
        return result

    def __exit__(self, *_):
        if self.pipe is not None:
            import win32file
            import win32pipe
            try:
                # Cleanup is process management for this owned session. It must
                # run even after cancellation; no game input or focus is sent.
                win32file.WriteFile(self.pipe, b'stop:keep\n' if self.keep_owned_game else b'stop\n')
                until = time.monotonic() + 30
                received = self.buffer
                while time.monotonic() < until:
                    if b'\n' in received:
                        line, received = received.split(b'\n', 1)
                        report = json.loads(line)
                        if report.get('cleanup_report') is True:
                            if self.game_session:
                                self.game_session.native_report = report
                                (self.game_session.evidence / 'native-cleanup.json').write_text(
                                    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                            break
                        continue  # A prior observation can arrive after cancel.
                    available = win32pipe.PeekNamedPipe(self.pipe, 0)[1]
                    if available:
                        received += win32file.ReadFile(self.pipe, min(available, 65536))[1]
                        if len(received) > 24_000_000:
                            raise ValueError('Cleanup response exceeded bound')
                    else:
                        time.sleep(.05)
            except Exception as exc:
                if self.game_session:
                    self.game_session.errors.append('Native cleanup response: ' + type(exc).__name__)
            finally:
                self.pipe.Close()
                self.pipe = None


def wait_for_game_exit(cancel=None, timeout=20):
    import psutil
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any((p.info['name'] or '').lower() == 'narakabladepoint.exe'
                   for p in psutil.process_iter(['name'])):
            return True
        if cancel and cancel.wait(.25):
            raise GameLoginError('interrupted', '用户停止了游戏退出等待')
        if not cancel:
            time.sleep(.25)
    return False


def ensure_game_login(evidence: Path, event=lambda message: None, cancel=None,
                      client_factory=BrokerClient, read_page=None, timeout=360,
                      close_started_game=True, session_factory=None) -> GameState:
    """Run the bounded flow and always reclaim only session-owned processes."""
    if session_factory is None and client_factory is BrokerClient:
        from .game_session import GameProcessSession
        session_factory = GameProcessSession
    session = session_factory(evidence, event) if session_factory else None
    def factory(stop):
        client = client_factory(stop)
        client.game_session = session
        return client
    state = None
    remaining = []
    try:
        state = _drive_game_login(evidence, event, cancel, factory, read_page, timeout, close_started_game)
    finally:
        if session:
            try:
                remaining = session.cleanup(close=close_started_game or state is None or state.status != 'ready')
            except Exception as exc:
                event('本轮游戏清理未能确认：' + type(exc).__name__ + ': ' + str(exc))
                remaining = ['cleanup-unverified']
    if remaining:
        reason = (state.reason if state else '游戏流程异常') + '；本轮游戏清理仍有残留或缺少退出证据'
        return GameState('manual_required' if state and state.status == 'manual_required' else 'precondition', reason)
    return state


def _drive_game_login(evidence: Path, event, cancel, client_factory, read_page, timeout,
                     close_started_game) -> GameState:
    """Only returns ready for an observed lobby; never claims mini-program points."""
    if read_page is None:
        from .desktop.vision import read_text
        read_page = lambda path: [item.text for item in read_text(path)]
    evidence.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    counts = {}
    unknown_frames = 0
    loading_until = 0.0
    launcher_requested = False
    exit_rejections = 0
    control_rejections = {}
    transition_until = {}
    transition_logged = set()
    try:
        with client_factory(cancel) as broker:
            for step in range(80):
                if cancel and cancel.is_set():
                    return GameState('interrupted', '用户停止了游戏登录')
                if time.monotonic() >= deadline:
                    return GameState('precondition', '等待游戏登录超时，下次计划再检查')
                try:
                    focus_attempts = 45 if time.monotonic() < loading_until else 4
                    for focus_attempt in range(focus_attempts):
                        try:
                            observed = broker.request('observe')
                            break
                        except GameLoginError as focus_error:
                            focus_transition = ('未能切到前台' in str(focus_error) or 'cannot focus game' in str(focus_error))
                            missing_window = (focus_error.status == 'entry_unavailable'
                                              and str(focus_error) == 'entry_unavailable: no game window')
                            now_tick = time.monotonic()
                            loading_gap = missing_window and now_tick < loading_until
                            small_window = (focus_error.status == 'unrecognized'
                                            and str(focus_error) == 'unrecognized: window dimensions')
                            can_wait = (focus_error.status == 'desktop_busy' and focus_transition) or loading_gap or small_window
                            if not can_wait or focus_attempt == focus_attempts-1 or now_tick >= deadline:
                                if small_window:
                                    raise GameLoginError('precondition', '游戏/启动器窗口未恢复到可截图尺寸；未发送游戏操作') from focus_error
                                if missing_window and loading_until:
                                    raise GameLoginError('precondition', '已请求启动游戏，但限定等待内未出现游戏窗口；稍后复查，不重复点击启动') from focus_error
                                raise
                            if focus_attempt in {0, 10, 20, 30, 40}:
                                event("等待游戏启动后的窗口就绪（未发送点击）：" + str(focus_error))
                            if cancel and cancel.wait(2):
                                return GameState('interrupted', '用户停止了游戏登录')
                            if not cancel:
                                time.sleep(2)
                except GameLoginError as exc:
                    if exc.status != 'entry_unavailable' or step != 0:
                        raise
                    # This existing task directly starts only the official launcher.
                    import subprocess
                    script = Path(__file__).resolve().parents[1] / 'scripts' / 'Game-LauncherTask.ps1'
                    system = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0'
                    launch_env = dict(os.environ, PSModulePath=str(system/'Modules'))
                    if getattr(broker, 'game_session', None):
                        broker.game_session.expect_launcher()
                    launched = subprocess.run([str(system/'powershell.exe'), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-Mode', 'Start'], capture_output=True, timeout=25, creationflags=0x08000000, env=launch_env)
                    if launched.returncode:
                        return GameState('precondition', '已授权的官方启动器任务未能启动')
                    launcher_requested = True
                    if cancel and cancel.wait(5):
                        return GameState('interrupted', '用户停止了游戏登录')
                    if not cancel:
                        time.sleep(5)
                    loading_until = min(deadline, time.monotonic() + 90)
                    event('已请求官方启动器，等待窗口出现；不会重复启动')
                    continue
                image = evidence / f'game-{step:02d}.png'
                image.write_bytes(base64.b64decode(observed['png'], validate=True))
                state = classify_page(observed['kind'], read_page(image))
                event('游戏登录：' + state.reason)
                if state.status == 'ready' and close_started_game and (launcher_requested or counts.get('launcher_start')):
                    event('已确认游戏大厅，退出本轮启动的游戏后再领取小程序积分')
                    try:
                        broker.request('act:exit_lobby')
                    except GameLoginError as exc:
                        details = exc.details
                        # Only this native response guarantees rejection BEFORE
                        # any input. Unknown errors must never replay a close.
                        if not (exc.status == 'unrecognized' and details.get('code') == 'control_not_ready'
                                and details.get('action') == 'exit_lobby' and details.get('kind') == 'game'
                                and details.get('input_sent') is False):
                            raise
                        exit_rejections += 1
                        rejected = evidence / f'game-exit-rejected-{exit_rejections}.png'
                        rejected.write_bytes(base64.b64decode(details['png'], validate=True))
                        metadata = {k: v for k, v in details.items() if k != 'png'}
                        rejected.with_suffix('.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
                        event(f'大厅退出画面校验未通过（{exit_rejections}/3），本次未发送退出请求：' + str(exc))
                        if exit_rejections >= 3:
                            return GameState('unrecognized', '已识别大厅，但退出画面连续三次未通过校验；保留游戏与失败截图，未重复发送关闭请求')
                        unknown_frames = 0
                        if cancel and cancel.wait(2):
                            return GameState('interrupted', '用户停止了游戏登录')
                        if not cancel:
                            time.sleep(2)
                        continue  # Fresh observation/OCR must confirm lobby again.
                    if not wait_for_game_exit(cancel):
                        return GameState('precondition', '已确认登录，但游戏未在限定时间内退出；保留现场，稍后复查')
                    event('已确认游戏进程退出，释放内存并返回微信')
                    return GameState('ready', '已登录并退出本轮启动的游戏；还需在小程序验证并领取积分')
                if state.status in {'ready', 'manual_required', 'network_error', 'unrecognized', 'desktop_busy', 'precondition'}:
                    if state.status == 'ready' and not close_started_game:
                        broker.keep_owned_game = True
                    return state
                unknown_frames = unknown_frames + 1 if state.status == 'waiting' else 0
                if unknown_frames >= 5 and time.monotonic() >= loading_until:
                    return GameState('unrecognized', '连续多次无法识别游戏页面，停止抢占窗口；保留游戏现状')
                if state.action:
                    # No repeated clicks when the first action's result is unclear.
                    if counts.get(state.action, 0):
                        if time.monotonic() < transition_until.get(state.action, 0):
                            if state.action not in transition_logged:
                                event('已发送操作，等待页面变化；不重复输入：' + state.action)
                                transition_logged.add(state.action)
                            if cancel and cancel.wait(3):
                                return GameState('interrupted', '用户停止了游戏登录')
                            if not cancel:
                                time.sleep(3)
                            continue
                        return GameState('unrecognized', '操作后仍停在同一游戏页面，停止重复输入：' + state.action)
                    try:
                        broker.request('act:' + state.action)
                    except GameLoginError as exc:
                        details = exc.details
                        # Animated labels may not yet match the fresh capture.
                        # Retry only an explicit rejection before any input,
                        # after another observation verifies the current page.
                        if not (state.action in {'title_enter', 'saved_account'} and exc.status == 'unrecognized'
                                and details.get('code') == 'control_not_ready'
                                and details.get('action') == state.action and details.get('kind') == 'game'
                                and details.get('input_sent') is False):
                            raise
                        rejected_count = control_rejections.get(state.action, 0) + 1
                        control_rejections[state.action] = rejected_count
                        label = '标题页' if state.action == 'title_enter' else '已保存账号'
                        prefix = 'title' if state.action == 'title_enter' else 'saved-account'
                        rejected = evidence / f'game-{prefix}-rejected-{rejected_count}.png'
                        rejected.write_bytes(base64.b64decode(details['png'], validate=True))
                        metadata = {k: v for k, v in details.items() if k != 'png'}
                        rejected.with_suffix('.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
                        event(f'{label}控件尚未通过画面校验（{rejected_count}/3），本次未发送点击，等待后重新观察：' + str(exc))
                        if rejected_count >= 3:
                            return GameState('unrecognized', f'{label}控件连续三次未通过画面校验；保留游戏与失败截图，未发送点击')
                        if cancel and cancel.wait(2):
                            return GameState('interrupted', '用户停止了游戏登录')
                        if not cancel:
                            time.sleep(2)
                        continue
                    counts[state.action] = 1
                    if state.action in {'launcher_start', 'title_enter'}:
                        loading_until = min(deadline, time.monotonic() + 90)
                        transition_until[state.action] = loading_until
                    else:
                        transition_until[state.action] = min(deadline, time.monotonic() + 30)
                delay = 6 if state.action else 3
                if cancel:
                    if cancel.wait(delay):
                        return GameState('interrupted', '用户停止了游戏登录')
                else:
                    time.sleep(delay)
        return GameState('precondition', '游戏页面未能在限定时间内进入大厅')
    except GameLoginError as exc:
        return GameState(exc.status, str(exc))
    except Exception as exc:
        return GameState('unrecognized', '游戏登录模块错误：' + str(exc))
