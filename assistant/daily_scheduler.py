"""Start the local assistant daily in the current user's interactive session."""
import subprocess
import sys
from pathlib import Path

from .config import now

TASK_NAME = "NarakaCheckinAssistant-Daily"


def sync_daily_task(config, data, service=None):
    config.validate()
    if service is None:
        import win32com.client
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
    import win32api
    import win32con
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
    root = Path(__file__).resolve().parents[1]
    python = Path(sys.executable).with_name("pythonw.exe")
    if not python.is_file():
        raise RuntimeError("未找到本机 Python 界面运行环境")
    definition = service.NewTask(0)
    definition.RegistrationInfo.Description = "按助手配置启动本地每日任务；普通权限，仅在当前用户已登录时运行。"
    definition.Principal.UserId = sid
    definition.Principal.LogonType = 3  # InteractiveToken; no password stored.
    definition.Principal.RunLevel = 0
    definition.Settings.Enabled = config.enabled
    definition.Settings.StartWhenAvailable = True
    definition.Settings.DisallowStartIfOnBatteries = False
    definition.Settings.StopIfGoingOnBatteries = False
    definition.Settings.MultipleInstances = 2  # IgnoreNew; app has an instance lock too.
    definition.Settings.ExecutionTimeLimit = "PT0S"
    trigger = definition.Triggers.Create(2)  # Daily
    trigger.DaysInterval = 1
    trigger.StartBoundary = f"{now().date()}T{config.daily_time}:00+08:00"
    trigger.Enabled = True
    action = definition.Actions.Create(0)
    action.Path = str(python)
    action.Arguments = subprocess.list2cmdline([str(root/"app.py"), "--minimized", "--scheduled-start", "--data-dir", str(Path(data).resolve())])
    action.WorkingDirectory = str(root)
    service.GetFolder("\\").RegisterTaskDefinition(TASK_NAME, definition, 6, sid, None, 3)
