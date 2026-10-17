from __future__ import annotations

import threading
from datetime import timedelta

from .adapters import LABELS, Result
from .config import BEIJING, Config, now
from .storage import Store

COMPLETE = {"success", "already_signed"}
REPORTABLE = COMPLETE | {"manual_required", "network_error", "activity_missing", "awaiting_confirmation"}


class Engine:
    def __init__(self, store: Store, clock=now):
        self.store, self.clock = store, clock
        self.lock = threading.Lock()

    def run(self, config: Config, adapter, trigger="manual"):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("已有任务正在执行，请稍后查看结果")
        try:
            instant = self.clock().astimezone(BEIJING)
            day, stamp = instant.date().isoformat(), instant.isoformat(timespec="seconds")
            run_id = self.store.create_run(day, config.scope, stamp, trigger)
            log = lambda message: self.store.log(run_id, self.clock().isoformat(timespec="seconds"), message)
            if hasattr(adapter, "event"):
                adapter.event = log
            if hasattr(adapter, "task_event"):
                adapter.task_event = lambda task_id, title, status, reason: self.store.record_task(run_id, task_id, title, status, reason)
            log(f"任务：{config.task_name}；账号：{config.account}；{config.game}；先检查本地当天完成记录。")
            prior = self.store.completion(day, config.scope)
            if prior:
                result = Result("already_signed", f"当天已有完成记录（#{prior['run_id']}，来源 {prior['source']}），未打开入口或重复操作。", prior["source"], True)
            elif config.start_date and day < config.start_date:
                result = Result("activity_missing", f"尚未到配置的活动开始日期 {config.start_date}。", "configuration")
            elif config.end_date and day > config.end_date:
                result = Result("activity_missing", f"已超过配置的活动结束日期 {config.end_date}。", "configuration")
            else:
                try:
                    log("本地无完成记录。这不表示服务端未签到；交由适配器检查，无法判断则停止并报错。")
                    result = adapter.check(config)
                    log(f"检查返回 {result.status}，来源 {result.source}。")
                    if result.status == "not_signed":
                        if not result.verified:
                            result = Result("unrecognized", "未签到状态没有可信证据，停止自动操作。", result.source)
                        else:
                            log("已确认未签到，执行适配器动作并要求操作后验证。")
                            result = adapter.sign(config)
                except (TimeoutError, ConnectionError) as exc:
                    result = Result("network_error", f"连接失败：{exc}")
                except Exception as exc:
                    result = Result("error", f"{type(exc).__name__}: {exc}")
            if result.status not in LABELS or result.status in ("running", "idle", "not_signed"):
                result = Result("error", f"适配器没有给出有效终态：{result.status}")
            if result.status in COMPLETE and not result.verified:
                result = Result("unrecognized", "适配器没有提供操作后的成功证据；没有记为成功。", result.source)
            ended = self.clock().astimezone(BEIJING)
            if ended.date().isoformat() != day and result.status in COMPLETE:
                result = Result("unrecognized", "执行跨越了北京时间零点，请重新检查今天的状态。", result.source)
            self.store.finish(run_id, ended.isoformat(timespec="seconds"), result.status, result.reason,
                              result.source, result.status in COMPLETE)
            return self.store.get_run(run_id)
        finally:
            try:
                if hasattr(adapter, "close"):
                    adapter.close()
            finally:
                self.lock.release()
                # Optional local repair outbox. Its failure must never change the
                # business result, and delivery waits until workbench cleanup ends.
                try:
                    from .repair_bridge import kick
                    kick(self.store.path.parent, locals().get('run_id'))
                except Exception:
                    pass

    def report(self, run_id: int, config: Config, status: str, note: str = ""):
        with self.lock:
            if status not in REPORTABLE:
                raise ValueError("不支持的人工反馈")
            record = self.store.get_run(run_id)
            instant = self.clock().astimezone(BEIJING)
            if not record or record["scope"] != config.scope:
                raise ValueError("账号或任务已切换，请在当前配置下重新执行")
            if record["day"] != instant.date().isoformat():
                raise ValueError("执行记录不是今天的，请重新执行后核对")
            if record["source"] == "simulation":
                raise ValueError("不能把模拟记录确认为真实结果")
            if record["status"] in COMPLETE:
                raise ValueError("已有完成记录；如确认有误，请先撤销今日确认")
            reason = f"用户根据小程序页面反馈：{LABELS[status]}。" + (f"备注：{note.strip()}" if note.strip() else "")
            self.store.finish(run_id, instant.isoformat(timespec="seconds"), status, reason, "manual", status in COMPLETE)
            return self.store.get_run(run_id)

    def due(self, config: Config):
        instant = self.clock().astimezone(BEIJING)
        if not config.enabled or instant.strftime("%H:%M") < config.daily_time:
            return False
        return self.store.claim_schedule(instant.date().isoformat(), config.scope)

    def next_run(self, config: Config):
        if not config.enabled:
            return "已暂停"
        instant = self.clock().astimezone(BEIJING)
        hour, minute = map(int, config.daily_time.split(":"))
        target = instant.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if self.store.schedule_claimed(instant.date().isoformat(), config.scope):
            target += timedelta(days=1)
        elif instant >= target:
            return "即将补执行今天的任务"
        return target.strftime("%m-%d %H:%M") + " 北京时间"
