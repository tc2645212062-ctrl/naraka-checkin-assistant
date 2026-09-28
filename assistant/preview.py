"""Synthetic task adapter for UI previews. No desktop dependencies or production paths."""
from .adapters import Result, TITLES


class PreviewAdapter:
    def __init__(self, scenario="partial", cancel=None):
        self.scenario, self.cancel = scenario, cancel
        self.event = lambda message: None
        self.task_event = lambda *args: None

    def check(self, config):
        if self.scenario == "already_signed":
            self.record(config, "already_signed")
            return Result("already_signed", "模拟：六项任务已有完成记录，本轮跳过。", "simulation", True)
        if self.scenario in {"manual_required", "network_error", "activity_missing"}:
            self.record(config, self.scenario)
            reasons = {"manual_required": "模拟：微信要求安全验证，已停止操作。", "network_error": "模拟：网络连接超时，尚未提交。", "activity_missing": "模拟：活动入口已下线。"}
            return Result(self.scenario, reasons[self.scenario], "simulation")
        self.record(config, "not_signed")
        return Result("not_signed", "模拟：已检查任务，准备执行。", "simulation", True)

    def record(self, config, state):
        for key in config.enabled_tasks:
            self.task_event(key, TITLES[key], state, "模拟记录，仅用于检查界面反馈。")

    def sign(self, config):
        for key in config.enabled_tasks:
            if self.cancel and self.cancel.wait(.08):
                return Result("interrupted", "模拟任务已响应停止请求。", "simulation")
            state = "waiting_release" if self.scenario == "partial" and key == "key" else "success"
            reason = "口令已过期，未提交，等待有效来源。" if state == "waiting_release" else "模拟：操作后核对为已完成。"
            self.task_event(key, TITLES[key], state, reason)
            self.event(f"模拟 / {TITLES[key]}：{reason}")
        if self.scenario == "partial" and "key" in config.enabled_tasks:
            return Result("partial", "其余五项已完成；金钥匙口令已过期，本轮没有提交。", "simulation")
        return Result("success", "模拟：启用任务均已完成。不是游戏内成功证据。", "simulation", True)
