from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .config import Config

TITLES = {"calendar": "日历签到", "login": "登录游戏领取", "card": "名片任务", "poster": "海报任务", "read": "阅读文章", "key": "金钥匙答题"}

LABELS = {
    "idle": "尚未执行", "running": "正在检查", "success": "签到成功",
    "already_signed": "今日已签到", "awaiting_confirmation": "等待人工检查 / 签到",
    "manual_required": "需要人工验证", "activity_missing": "活动不存在 / 不在有效期",
    "network_error": "网络失败", "entry_unavailable": "入口不可用",
    "error": "执行失败", "interrupted": "执行中断", "revoked": "已撤销本地确认",
    "not_signed": "尚未签到",
    "desktop_busy": "桌面忙碌 / 已锁屏", "unrecognized": "页面无法可靠识别",
    "partial": "部分任务未完成", "precondition": "游戏前置条件未满足",
    "waiting_release": "等待有效金钥匙",
}


@dataclass(frozen=True)
class Result:
    status: str
    reason: str
    source: str = "adapter"
    verified: bool = False


class Adapter(Protocol):
    """A real automatic adapter must check BEFORE acting and verify AFTER acting."""
    def check(self, config: Config) -> Result: ...
    def sign(self, config: Config) -> Result: ...


class AssistAdapter:
    """Opening a page never supplies sign-in evidence."""
    def __init__(self, opener):
        self.opener = opener

    def check(self, config):
        opened, detail = self.opener(config)
        if not opened:
            return Result("entry_unavailable", detail, "local")
        return Result("awaiting_confirmation", detail + " 请先核对当天是否已签到；助手无法读取服务端状态。", "local")

    def sign(self, config):
        raise RuntimeError("协助模式没有自动点击能力；必须由用户核对并完成签到")


class MockAdapter:
    """Only used with a separate temporary database by the demo and tests."""
    def __init__(self, scenario):
        if scenario not in ("success", "already_signed", "manual_required", "network_error", "activity_missing"):
            raise ValueError("未知模拟场景")
        self.scenario = scenario
        self.check_calls = 0
        self.sign_calls = 0

    def check(self, config):
        self.check_calls += 1
        if self.scenario == "success":
            return Result("not_signed", "模拟：当前未签到", "simulation", True)
        return Result(self.scenario, f"模拟：{LABELS[self.scenario]}", "simulation", True)

    def sign(self, config):
        self.sign_calls += 1
        return Result("success", "模拟：操作后的页面确认签到成功", "simulation", True)
