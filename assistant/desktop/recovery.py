"""Bounded restoration of an existing WeChat client; never restart its process."""
import json
from pathlib import Path

import psutil
from . import windows as win


def restore_main(window, executable, marker, day, event=lambda message: None):
    if window.title != "微信" or not (window.class_name.endswith('QWindowIcon')
                                      or window.class_name == 'WeChatMainWndForPC'):
        raise RuntimeError("无法核对微信主窗口，未恢复")
    executable = Path(executable).resolve()
    process = psutil.Process(window.pid)
    if not executable.is_file() or Path(process.exe()).resolve() != executable:
        raise RuntimeError("微信实际路径与配置不符，未恢复")
    if marker.exists():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        if previous.get("day") == str(day):
            raise RuntimeError("今天已恢复过微信，停止重复恢复；请查看失败日志")
    # Reserve the bounded attempt first. Never terminate/relaunch a user client.
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(json.dumps({"day": str(day), "pid": window.pid, "mode": "restore_only"}), encoding="utf-8")
    temporary.replace(marker)
    event("微信导航未就绪；当天仅恢复现有主窗口一次，不退出或重启微信")
    return win.restore_existing_wechat(window)
