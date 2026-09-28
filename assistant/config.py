from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8), "北京时间")


def now() -> datetime:
    return datetime.now(BEIJING)


def data_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "NarakaCheckinAssistant"


@dataclass
class Config:
    account: str = "我的账号"
    game: str = "端游"
    task_id: str = "welfare-daily"
    task_name: str = "福利中心每日签到"
    daily_time: str = "22:00"
    enabled: bool = False
    auto_game_login: bool = False
    start_date: str = ""
    end_date: str = ""
    shortcut: str = ""
    wechat_exe: str = ""
    mini_title: str = "永劫无间"
    welfare_label: str = "福利中心"
    expected_role: str = ""
    success_text: str = "签到成功"
    idle_seconds: int = 30
    enabled_tasks: list[str] = field(default_factory=lambda: ["calendar", "login", "card", "poster", "read", "key"])
    task_labels: dict[str, str] = field(default_factory=lambda: {"login": "每日登录永劫无间游戏", "card": "分享一次名片", "poster": "分享无间镖客海报", "read": "每日阅读文章", "key": "金钥匙"})
    instructions: str = (
        "微信 → 永劫无间官方小程序 → 选择正确的端游 / 手游 → 福利中心。\n"
        "先检查当天的日历格或“今日已签到”提示；已完成就不要再次点击。\n"
        "自动执行启用的每日任务，并验证页面结果。\n"
        "首次使用请完成微信登录、授权和游戏角色绑定；以当前页面为准。"
    )

    @property
    def scope(self) -> str:
        # JSON avoids delimiter collisions between account and task names.
        return json.dumps([self.account, self.game, self.task_id, sorted(self.enabled_tasks)], ensure_ascii=False)

    def validate(self) -> None:
        for field in ("account", "task_id", "task_name"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or len(value) > 120:
                raise ValueError(f"{field} 不能为空，且不能超过 120 个字符")
        if self.game not in ("端游", "手游"):
            raise ValueError("请选择端游或手游")
        if not isinstance(self.enabled, bool):
            raise ValueError("启停配置必须是布尔值")
        if not isinstance(self.auto_game_login, bool):
            raise ValueError("自动登录游戏配置必须是布尔值")
        if not isinstance(self.daily_time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", self.daily_time):
            raise ValueError("每日时间必须使用 HH:MM，例如 20:00")
        for field in ("start_date", "end_date"):
            value = getattr(self, field)
            if not isinstance(value, str):
                raise ValueError("活动日期必须是文本")
            if value:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError("活动日期应为 YYYY-MM-DD")
                date.fromisoformat(value)
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("活动结束日期不能早于开始日期")
        for field in ("shortcut", "wechat_exe", "instructions"):
            if not isinstance(getattr(self, field), str):
                raise ValueError(f"{field} 必须是文本")
        if self.shortcut and (Path(self.shortcut).suffix.lower() != ".lnk" or not Path(self.shortcut).is_file()):
            raise ValueError("小程序入口必须是本机存在的 .lnk 快捷方式")
        if self.wechat_exe and (Path(self.wechat_exe).name.lower() not in ("weixin.exe", "wechat.exe") or not Path(self.wechat_exe).is_file()):
            raise ValueError("微信路径应指向本机的 Weixin.exe 或 WeChat.exe")
        if len(self.instructions) > 4000:
            raise ValueError("入口说明不能超过 4000 个字符")
        for field in ("mini_title", "welfare_label", "success_text"):
            if not isinstance(getattr(self, field), str) or not getattr(self, field).strip():
                raise ValueError(f"{field} 不能为空")
        if not isinstance(self.expected_role, str):
            raise ValueError("角色关键词必须是文本")
        if type(self.idle_seconds) is not int or not 0 <= self.idle_seconds <= 600:
            raise ValueError("空闲等待秒数必须在 0 到 600 之间")
        if not isinstance(self.enabled_tasks, list) or not self.enabled_tasks or any(t not in {"calendar", "login", "card", "poster", "read", "key"} for t in self.enabled_tasks):
            raise ValueError("至少启用一项支持的每日任务")
        if not isinstance(self.task_labels, dict) or set(self.task_labels) != {"login", "card", "poster", "read", "key"} or any(not isinstance(v,str) or not v.strip() for v in self.task_labels.values()):
            raise ValueError("任务识别文字配置不完整")


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("配置必须为 JSON 对象")
        config = Config(**raw)
        # Removed shortcuts must not prevent the app from opening its settings.
        saved_paths = config.shortcut, config.wechat_exe
        config.shortcut = config.wechat_exe = ""
        config.validate()
        config.shortcut, config.wechat_exe = saved_paths
        if not all(isinstance(p, str) for p in saved_paths):
            raise ValueError("入口路径必须是文本")
        return config
    except (ValueError, TypeError) as exc:
        raise ValueError(f"配置文件损坏，请修复或备份后移走 {path}。原文件已保留。\n{exc}") from exc


def save_config(path: Path, config: Config) -> None:
    config.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(asdict(config), handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
