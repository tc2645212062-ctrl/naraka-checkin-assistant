from __future__ import annotations

import ctypes
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

import cv2
import mss
import numpy as np
import psutil
import win32api
import win32con
import win32gui
import win32process
from pywinauto import keyboard, mouse

from .models import WindowInfo


WECHAT_PROCESS = "weixin.exe"
WECHAT_OWNED_PROCESSES = {WECHAT_PROCESS, "wechat.exe", "wechatappex.exe"}
WECHAT_EXECUTABLE = Path(r"D:\Weixin\Weixin.exe")


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds() -> float:
    info = _LastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        raise AutomationSafetyError("Cannot verify whether the user is currently using the desktop.")
    now = ctypes.windll.kernel32.GetTickCount()
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0


def desktop_available() -> bool:
    """Read the input desktop name without attempting to unlock/switch it."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    handle = user32.OpenInputDesktop(0, False, 1)
    if not handle:
        return False
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        return bool(user32.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed))) and name.value.casefold() == "default"
    finally:
        user32.CloseDesktop(handle)


class AutomationSafetyError(RuntimeError):
    """Raised when an action cannot be performed unambiguously."""


def enable_dpi_awareness() -> None:
    """Use physical pixels so screenshots and input coordinates agree."""
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


# Must run before any window is measured, captured, or clicked. Without this,
# Windows can virtualize Win32 rectangles while MSS captures physical pixels.
enable_dpi_awareness()


def wechat_processes() -> list[psutil.Process]:
    processes: list[psutil.Process] = []
    for process in psutil.process_iter(["pid", "name", "exe", "create_time"]):
        try:
            if (process.info.get("name") or "").casefold() in {WECHAT_PROCESS, "wechat.exe"}:
                processes.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return sorted(processes, key=lambda p: p.pid)


def wechat_owned_processes() -> list[psutil.Process]:
    """Return the desktop client and its official mini-program host processes."""
    processes: list[psutil.Process] = []
    for process in psutil.process_iter(["pid", "name", "exe", "create_time"]):
        try:
            if (process.info.get("name") or "").casefold() in WECHAT_OWNED_PROCESSES:
                processes.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return sorted(processes, key=lambda p: p.pid)


def _window_for_handle(handle: int) -> WindowInfo:
    _, pid = win32process.GetWindowThreadProcessId(handle)
    placement = win32gui.GetWindowPlacement(handle)
    return WindowInfo(
        handle=handle,
        pid=pid,
        title=win32gui.GetWindowText(handle),
        class_name=win32gui.GetClassName(handle),
        visible=bool(win32gui.IsWindowVisible(handle)),
        minimized=placement[1] == win32con.SW_SHOWMINIMIZED,
        rect=tuple(win32gui.GetWindowRect(handle)),
    )


def enumerate_wechat_windows() -> list[WindowInfo]:
    pids = {process.pid for process in wechat_owned_processes()}
    windows: list[WindowInfo] = []

    def callback(handle: int, _: object) -> None:
        try:
            _, pid = win32process.GetWindowThreadProcessId(handle)
            if pid in pids:
                windows.append(_window_for_handle(handle))
        except win32gui.error:
            return

    win32gui.EnumWindows(callback, None)
    return sorted(windows, key=lambda item: (-item.area, item.handle))


def choose_main_window(windows: Iterable[WindowInfo]) -> WindowInfo:
    # The tray helper is a hidden Qt window which retains a stale, desktop-sized
    # rectangle.  It is not a recoverable main client, even though its area is
    # larger than the minimized main window.  The desktop client itself uses a
    # QWindowIcon class in the current client (or WeChatMainWndForPC in older
    # versions); article readers and mini-program hosts use Chrome classes.
    def is_desktop_client(window: WindowInfo) -> bool:
        return window.class_name.endswith("QWindowIcon") or window.class_name == "WeChatMainWndForPC"

    desktop_windows = [window for window in windows if is_desktop_client(window)]
    candidates = [
        window
        for window in desktop_windows
        if window.minimized or (window.visible and window.area >= 300_000)
    ]
    # A hidden client is only a fallback when there is no visibly recoverable
    # desktop window.  Keep the size guard for this exceptional case.
    if not candidates:
        candidates = [window for window in desktop_windows if window.area >= 300_000]
    if not candidates:
        raise AutomationSafetyError(
            "Weixin.exe is running, but no recoverable main window was found; "
            "refusing to start another instance."
        )

    def score(window: WindowInfo) -> tuple[int, int, int]:
        title_score = 3 if window.title in {"微信", "Weixin"} else 0
        # When the client is minimized to tray, Win32 reports it as visible
        # with a tiny off-screen rectangle.  Prefer it over stale hidden Qt
        # helper windows even if their rectangles are larger.
        state_score = 2 if window.minimized else 1 if window.visible else 0
        return state_score, title_score, window.area

    ranked = sorted(candidates, key=score, reverse=True)
    if len(ranked) > 1 and score(ranked[0]) == score(ranked[1]):
        raise AutomationSafetyError(
            "Multiple equally likely WeChat main windows were found; refusing ambiguous input."
        )
    return ranked[0]


def ensure_wechat_main_window(timeout: float = 40.0) -> tuple[WindowInfo, bool]:
    """Reuse a running WeChat; launch only when *no* Weixin.exe exists."""
    if wechat_processes():
        return choose_main_window(enumerate_wechat_windows()), False
    if not WECHAT_EXECUTABLE.is_file():
        raise AutomationSafetyError(f"WeChat executable is unavailable: {WECHAT_EXECUTABLE}")
    subprocess.Popen([str(WECHAT_EXECUTABLE)], cwd=str(WECHAT_EXECUTABLE.parent))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if wechat_processes():
            try:
                return choose_main_window(enumerate_wechat_windows()), True
            except AutomationSafetyError:
                pass
        time.sleep(0.5)
    raise AutomationSafetyError("WeChat launched but its main window did not appear.")


def restore_existing_wechat(window: WindowInfo) -> WindowInfo:
    if not wechat_processes():
        raise AutomationSafetyError("WeChat is not running; launch is intentionally disabled.")
    if not win32gui.IsWindow(window.handle):
        raise AutomationSafetyError("The selected WeChat window is no longer valid.")

    if not win32gui.IsWindowVisible(window.handle):
        from .tray import activate_wechat_tray
        try:
            activate_wechat_tray()
        except Exception as exc:
            raise AutomationSafetyError(str(exc)) from exc
        for _ in range(20):
            if win32gui.IsWindowVisible(window.handle):
                break
            time.sleep(.2)
        else:
            raise AutomationSafetyError("微信托盘入口未唤醒原窗口")
    if win32gui.IsIconic(window.handle):
        win32gui.ShowWindow(window.handle, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(window.handle)
    except win32gui.error:
        # Windows sometimes blocks foreground transfer. The ALT release is a
        # standard, reversible foreground activation workaround.
        keyboard.send_keys("%")
        win32gui.SetForegroundWindow(window.handle)
    time.sleep(0.4)
    restored = _window_for_handle(window.handle)
    if not restored.visible or restored.minimized or restored.area < 300_000:
        raise AutomationSafetyError("The existing WeChat window could not be restored safely.")
    return restored


def focus_window(window: WindowInfo) -> WindowInfo:
    """Bring a known WeChat-owned window to the foreground."""
    if not win32gui.IsWindow(window.handle):
        raise AutomationSafetyError("The selected WeChat window is no longer valid.")
    if not win32gui.IsWindowVisible(window.handle) and window.title == "微信" and window.class_name.startswith("Qt"):
        return restore_existing_wechat(window)
    if win32gui.IsIconic(window.handle) or not win32gui.IsWindowVisible(window.handle):
        win32gui.ShowWindow(window.handle, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(window.handle)
    except win32gui.error:
        keyboard.send_keys("%")
        win32gui.SetForegroundWindow(window.handle)
    time.sleep(0.35)
    return _window_for_handle(window.handle)


def hide_window(window: WindowInfo) -> None:
    """Hide one known window without terminating its WeChat process."""
    if win32gui.IsWindow(window.handle):
        win32gui.ShowWindow(window.handle, win32con.SW_HIDE)


def close_window(window: WindowInfo) -> None:
    """Request closure of one auxiliary window; never terminate a process."""
    if win32gui.IsWindow(window.handle):
        win32gui.PostMessage(window.handle, win32con.WM_CLOSE, 0, 0)
        time.sleep(0.35)


def refresh_window(window: WindowInfo) -> WindowInfo:
    if not win32gui.IsWindow(window.handle):
        raise AutomationSafetyError("The selected WeChat window is no longer valid.")
    return _window_for_handle(window.handle)


def capture_window(window: WindowInfo, output: Path) -> Path:
    if not win32gui.IsWindow(window.handle):
        raise AutomationSafetyError("Cannot capture a stale window handle.")
    current = _window_for_handle(window.handle)
    left, top, right, bottom = current.rect
    if right <= left or bottom <= top:
        raise AutomationSafetyError("The target window has an invalid capture rectangle.")
    output.parent.mkdir(parents=True, exist_ok=True)
    monitor = {"left": left, "top": top, "width": right - left, "height": bottom - top}
    with mss.mss() as capturer:
        bgra = np.asarray(capturer.grab(monitor))
    bgr = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
    ok, encoded = cv2.imencode(".png", bgr)
    if not ok:
        raise RuntimeError(f"Failed to write screenshot: {output}")
    encoded.tofile(str(output))
    return output


def foreground_pid() -> int:
    handle = win32gui.GetForegroundWindow()
    return win32process.GetWindowThreadProcessId(handle)[1]


def target_or_owned_popup(handle: int, window: WindowInfo) -> bool:
    root = win32gui.GetAncestor(handle, win32con.GA_ROOT)
    if root == window.handle:
        return True
    _, pid = win32process.GetWindowThreadProcessId(root)
    return pid == window.pid and win32gui.GetWindow(root, win32con.GW_OWNER) == window.handle


def assert_target_foreground(window: WindowInfo) -> None:
    if not target_or_owned_popup(win32gui.GetForegroundWindow(), window):
        raise AutomationSafetyError("WeChat lost foreground focus; refusing input.")


def click_window_point(window: WindowInfo, x: int, y: int) -> None:
    current = _window_for_handle(window.handle)
    if not (0 <= x < current.width and 0 <= y < current.height):
        raise AutomationSafetyError(f"Point ({x}, {y}) is outside the target window.")
    assert_target_foreground(current)
    screen_x = current.rect[0] + x
    screen_y = current.rect[1] + y
    if not target_or_owned_popup(win32gui.WindowFromPoint((screen_x, screen_y)), current):
        raise AutomationSafetyError("The click point is covered by a non-WeChat window.")
    # Use the native down/up pair after positioning the cursor.  The current
    # pywinauto mouse helper mixes normalized and pixel coordinates under DPI
    # scaling, which can leave a click at an unrelated desktop location.
    win32api.SetCursorPos((screen_x, screen_y))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def press_keys(window: WindowInfo, keys: str) -> None:
    assert_target_foreground(window)
    keyboard.send_keys(keys, pause=0.03)


def scroll_window(window: WindowInfo, wheel_dist: int) -> None:
    current = refresh_window(window)
    assert_target_foreground(current)
    x = current.rect[0] + current.width // 2
    y = current.rect[1] + current.height // 2
    _, point_pid = win32process.GetWindowThreadProcessId(win32gui.WindowFromPoint((x, y)))
    if point_pid not in {process.pid for process in wechat_owned_processes()}:
        raise AutomationSafetyError("The scroll point is covered by a non-WeChat window.")
    mouse.scroll(coords=(x, y), wheel_dist=wheel_dist)


@contextmanager
def preserved_clipboard():
    import win32clipboard

    previous: str | None = None
    try:
        win32clipboard.OpenClipboard()
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            previous = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        yield
    finally:
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            if previous is not None:
                win32clipboard.SetClipboardText(previous, win32con.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
        except Exception:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass


def paste_text(window: WindowInfo, text: str) -> None:
    import win32clipboard

    assert_target_foreground(window)
    with preserved_clipboard():
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        keyboard.send_keys("^v")
        time.sleep(0.15)
