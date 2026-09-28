"""Read-only view models. Never turn a simulation or stale result into evidence."""
from .adapters import LABELS, TITLES

COMPLETE = {"success", "already_signed"}
STATUS = {**LABELS, "success": "已完成", "already_signed": "已完成 · 本轮跳过",
          "not_signed": "待执行", "idle": "尚未检查", "disabled": "未启用"}
SOURCE = {"desktop": "真实页面证据", "simulation": "模拟数据", "manual": "用户反馈",
          "local": "本地记录", "configuration": "活动配置", "adapter": "执行结果"}


def dashboard(store, config, day, preview=False):
    history = [r for r in store.history(500) if r["scope"] == config.scope
               and (preview or r["source"] != "simulation")]
    today = [r for r in history if r["day"] == day]
    latest = today[0] if today else None
    evidence = {}
    for run in today:
        for task in store.tasks(run["id"]):
            if task["task_id"] not in evidence:
                evidence[task["task_id"]] = {**task, "source": run["source"], "started": run["started"]}
    tasks = []
    for key, title in TITLES.items():
        task = evidence.get(key, {"status": "idle", "reason": "今日还没有此任务的检查记录。", "source": "", "started": ""})
        if key not in config.enabled_tasks:
            task = {"status": "disabled", "reason": "可在每日任务中重新启用。", "source": "", "started": ""}
        verified = task["status"] in COMPLETE and task["source"] == "desktop"
        simulated = task["status"] in COMPLETE and task["source"] == "simulation"
        tasks.append({**task, "task_id": key, "title": title, "verified": verified,
                      "complete": verified or (preview and simulated)})
    return {"latest": latest, "previous": history[0] if history else None, "tasks": tasks,
            "done": sum(t["complete"] for t in tasks), "total": len(config.enabled_tasks)}
