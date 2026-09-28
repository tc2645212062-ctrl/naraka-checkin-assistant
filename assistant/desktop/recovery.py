"""Bounded recovery for a recognized, unresponsive WeChat main window."""
import json
import subprocess
from pathlib import Path

import psutil


def restart_main(window, executable, marker, day, event=lambda message: None):
    if window.title != "微信" or not window.class_name.startswith("Qt"):
        raise RuntimeError("无法核对微信主窗口，未重启")
    executable = Path(executable).resolve()
    process = psutil.Process(window.pid)
    if not executable.is_file() or Path(process.exe()).resolve() != executable:
        raise RuntimeError("微信实际路径与配置不符，未重启")
    if marker.exists():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        if previous.get("day") == str(day):
            raise RuntimeError("今天已恢复过微信，停止重复重启；请查看失败日志")
    # The caller holds automation.lock. Claim before termination, so a crash
    # cannot turn a restart into an unbounded loop. Never delete WeChat data.
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(json.dumps({"day": str(day), "pid": window.pid}), encoding="utf-8")
    temporary.replace(marker)
    event("微信主窗口连续未响应导航；执行当天唯一一次进程恢复，保留本地数据")
    process.terminate()
    process.wait(timeout=10)
    subprocess.Popen([str(executable)], cwd=str(executable.parent), creationflags=0x08000000)
