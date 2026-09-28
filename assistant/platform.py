from __future__ import annotations

import os
import sys
from pathlib import Path


def discover_wechat():
    candidates = []
    if sys.platform == "win32":
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for name in (r"Software\Tencent\Weixin", r"Software\Tencent\WeChat", r"Software\WOW6432Node\Tencent\WeChat"):
                try:
                    with winreg.OpenKey(root, name) as key:
                        folder = winreg.QueryValueEx(key, "InstallPath")[0]
                        candidates.extend(Path(folder)/exe for exe in ("Weixin.exe", "WeChat.exe"))
                except OSError:
                    pass
    for base in (os.environ.get("ProgramFiles", "C:/Program Files"), os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"), "D:/"):
        for folder in ("Tencent/Weixin", "Tencent/WeChat", "Weixin", "WeChat"):
            candidates.extend(Path(base)/folder/exe for exe in ("Weixin.exe", "WeChat.exe"))
    return next((str(p) for p in candidates if p.is_file()), "")


class InstanceLock:
    def __init__(self, path):
        self.path = path
        self.handle = None

    def acquire(self):
        import msvcrt
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.handle.close()
            self.handle = None
            return False
        return True

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None
