"""Activate the existing WeChat through its visible notification-area UI."""
import time

import win32gui
from pywinauto import Desktop


def wechat_tray_present():
    """Read-only presence check; pinned taskbar launch buttons do not count."""
    desktop = Desktop(backend='uia')
    handle = win32gui.FindWindow('Shell_TrayWnd', None)
    if not handle:
        raise RuntimeError('未找到 Windows 任务栏，无法确认微信未运行')
    tray = desktop.window(handle=handle).wrapper_object()
    if any(x.window_text() in {'微信', 'WeChat', 'Weixin'}
           and x.parent().class_name() == 'ToolbarWindow32'
           for x in tray.descendants(control_type='Button')):
        return True
    overflow = win32gui.FindWindow('NotifyIconOverflowWindow', None)
    if overflow:
        panel = desktop.window(handle=overflow).wrapper_object()
        if any(x.window_text() in {'微信', 'WeChat', 'Weixin'}
               for x in panel.descendants(control_type='Button')):
            return True
    return False


def activate_wechat_tray():
    desktop = Desktop(backend="uia")
    tray_handle = win32gui.FindWindow("Shell_TrayWnd", None)
    if not tray_handle:
        raise RuntimeError("未找到 Windows 任务栏")
    tray = desktop.window(handle=tray_handle).wrapper_object()
    # Exclude pinned taskbar launch buttons, which can open another instance.
    icons = [x for x in tray.descendants(control_type="Button")
             if x.window_text() in {"微信", "WeChat", "Weixin"}
             and x.parent().class_name() == "ToolbarWindow32"]
    overflow = win32gui.FindWindow("NotifyIconOverflowWindow", None)
    if not icons:
        if not overflow or not win32gui.IsWindowVisible(overflow):
            expand = [x for x in tray.descendants(control_type="Button")
                      if x.parent().class_name() == "TrayNotifyWnd"
                      and x.class_name() == "Button"
                      and any(t in x.window_text() for t in ("通知", "隐藏", "hidden icons", "Chevron"))]
            if len(expand) != 1:
                raise RuntimeError("未唯一识别系统托盘展开按钮")
            expand[0].invoke()
            time.sleep(.6)
            overflow = win32gui.FindWindow("NotifyIconOverflowWindow", None)
        if overflow:
            panel = desktop.window(handle=overflow).wrapper_object()
            icons = [x for x in panel.descendants(control_type="Button")
                     if x.window_text() in {"微信", "WeChat", "Weixin"}]
    if len(icons) != 1:
        raise RuntimeError("未唯一识别微信托盘图标；未启动其他微信实例")
    icons[0].invoke()
    time.sleep(.5)
