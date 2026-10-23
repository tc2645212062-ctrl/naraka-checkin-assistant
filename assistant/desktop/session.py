"""Bounded cleanup for windows actually used by one desktop run."""
from types import SimpleNamespace
from . import windows as win


class WindowSession:
    def __init__(self, event=lambda message: None):
        self.event = event
        self.initial = {w.handle: w for w in win.enumerate_wechat_windows()}
        self.touched = {}
        self.initial_games = {w.handle: w for w in self.game_windows()}
        self.games = {}

    @staticmethod
    def game_windows():
        import psutil
        allowed = {r'e:\naraka\program\narakabladepoint.exe', r'e:\naraka\tool\loothoarder.exe'}
        found = []
        def collect(handle, _):
            if not win.win32gui.IsWindowVisible(handle):
                return
            try:
                pid = win.win32process.GetWindowThreadProcessId(handle)[1]
                if psutil.Process(pid).exe().lower() in allowed:
                    window = win.refresh_window(SimpleNamespace(handle=handle))
                    if window.width > 700 and window.height > 400:
                        found.append(window)
            except (psutil.Error, win.win32gui.error, win.AutomationSafetyError):
                pass
        win.win32gui.EnumWindows(collect, None)
        return found

    def remember(self, window):
        self.touched[window.handle] = window

    def remember_started_game(self):
        for window in self.game_windows():
            before = self.initial_games.get(window.handle)
            if not before or before.pid != window.pid:
                self.games[window.handle] = window

    @staticmethod
    def same_window(left, right):
        return (left.pid, left.class_name, left.title) == (right.pid, right.class_name, right.title)

    @staticmethod
    def is_client(window):
        return window.title == '微信' and (
            window.class_name.endswith('QWindowIcon')
            or window.class_name == 'WeChatMainWndForPC'
            or 'LoginWindow' in window.class_name)

    def minimize_started_games(self):
        """Release foreground after a verified lobby, before returning to WeChat."""
        if not win.desktop_available():
            return
        current_games = {w.handle: w for w in self.game_windows()}
        for handle, used in self.games.items():
            actual = current_games.get(handle)
            if actual and self.same_window(actual, used):
                try:
                    win.win32gui.ShowWindow(handle, win.win32con.SW_MINIMIZE)
                    self.event('已收起本轮启动的游戏窗口，保留游戏进程')
                except Exception as exc:
                    self.event('游戏窗口清理失败：' + str(exc))
        self.games.clear()

    def finish(self):
        if not win.desktop_available():
            self.event('窗口清理暂缓：桌面当前不可交互')
            return
        current = {w.handle: w for w in win.enumerate_wechat_windows()}
        for handle, used in reversed(list(self.touched.items())):
            actual = current.get(handle)
            if not actual or not self.same_window(actual, used):
                continue  # Closed or reused HWND; never target a different window.
            try:
                before = self.initial.get(handle)
                if before and self.same_window(before, actual):
                    if before.minimized:
                        win.win32gui.ShowWindow(handle, win.win32con.SW_MINIMIZE)
                    elif not before.visible:
                        win.hide_window(actual)
                    elif actual.minimized or not actual.visible:
                        win.win32gui.ShowWindow(handle, win.win32con.SW_SHOWNOACTIVATE)
                    self.event('已恢复原微信窗口显示状态，保留原会话')
                elif self.is_client(actual):
                    # Login can replace its HWND with the main client HWND.
                    # WM_CLOSE on that new window can exit the whole client.
                    if 'LoginWindow' not in actual.class_name:
                        win.win32gui.ShowWindow(handle, win.win32con.SW_MINIMIZE)
                    self.event('保留本轮出现的微信主窗口及登录会话，未关闭客户端')
                else:
                    win.close_window(actual)
                    self.event('已关闭本轮新开的微信任务窗口')
            except Exception as exc:
                self.event('窗口清理失败，保留执行结果：' + str(exc))
        self.minimize_started_games()
        self.touched.clear()
        self.games.clear()
