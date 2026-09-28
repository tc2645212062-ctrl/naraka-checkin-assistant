from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, day TEXT NOT NULL, scope TEXT NOT NULL,
                    started TEXT NOT NULL, finished TEXT, trigger TEXT NOT NULL,
                    status TEXT NOT NULL, reason TEXT NOT NULL, source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS completions (
                    day TEXT NOT NULL, scope TEXT NOT NULL, run_id INTEGER NOT NULL,
                    source TEXT NOT NULL, PRIMARY KEY(day, scope)
                );
                CREATE TABLE IF NOT EXISTS schedule_claims (
                    day TEXT NOT NULL, scope TEXT NOT NULL, PRIMARY KEY(day, scope)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, run_id INTEGER, time TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_results (
                    run_id INTEGER NOT NULL, task_id TEXT NOT NULL, title TEXT NOT NULL,
                    status TEXT NOT NULL, reason TEXT NOT NULL,
                    PRIMARY KEY(run_id, task_id)
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self, timestamp: str) -> None:
        with self.connect() as db:
            rows = db.execute("SELECT id FROM runs WHERE status='running'").fetchall()
            for row in rows:
                db.execute("UPDATE runs SET status='interrupted', reason=?, finished=? WHERE id=?",
                           ("上次执行被中断，结果未知。请检查小程序后再运行。", timestamp, row["id"]))
                db.execute("INSERT INTO events(run_id,time,message) VALUES(?,?,?)",
                           (row["id"], timestamp, "检测到未完成的执行记录，恢复为中断状态；没有记为成功。"))

    def create_run(self, day, scope, timestamp, trigger) -> int:
        with self.connect() as db:
            return db.execute("INSERT INTO runs(day,scope,started,trigger,status,reason,source) VALUES(?,?,?,?,?,?,?)",
                              (day, scope, timestamp, trigger, "running", "正在检查当天记录", "local")).lastrowid

    def log(self, run_id, timestamp, message):
        with self.connect() as db:
            db.execute("INSERT INTO events(run_id,time,message) VALUES(?,?,?)", (run_id, timestamp, message))

    def completion(self, day, scope):
        with self.connect() as db:
            row = db.execute("SELECT * FROM completions WHERE day=? AND scope=?", (day, scope)).fetchone()
            return dict(row) if row else None

    def finish(self, run_id, timestamp, status, reason, source, complete=False):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("执行记录不存在")
            db.execute("UPDATE runs SET finished=?, status=?, reason=?, source=? WHERE id=?",
                       (timestamp, status, reason, source, run_id))
            if complete:
                db.execute("INSERT OR IGNORE INTO completions VALUES(?,?,?,?)",
                           (row["day"], row["scope"], run_id, source))
            db.execute("INSERT INTO events(run_id,time,message) VALUES(?,?,?)",
                       (run_id, timestamp, f"结果={status}；来源={source}；{reason}"))

    def get_run(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def claim_schedule(self, day, scope) -> bool:
        with self.connect() as db:
            return db.execute("INSERT OR IGNORE INTO schedule_claims VALUES(?,?)", (day, scope)).rowcount == 1

    def schedule_claimed(self, day, scope):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM schedule_claims WHERE day=? AND scope=?", (day, scope)).fetchone() is not None

    def history(self, limit=100):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]

    def events(self, run_id=None, limit=500):
        with self.connect() as db:
            if run_id is None:
                rows = db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM events WHERE run_id=? ORDER BY id DESC LIMIT ?", (run_id, limit)).fetchall()
            return [dict(r) for r in reversed(rows)]

    def undo(self, day, scope, timestamp):
        with self.connect() as db:
            row = db.execute("SELECT run_id FROM completions WHERE day=? AND scope=?", (day, scope)).fetchone()
            db.execute("DELETE FROM completions WHERE day=? AND scope=?", (day, scope))
            if row:
                db.execute("UPDATE runs SET status='revoked', reason=? WHERE id=?", ("用户撤销了本地确认；未撤销游戏内签到。", row["run_id"]))
            db.execute("INSERT INTO events(time,message) VALUES(?,?)", (timestamp, f"撤销本地完成记录：{day} / {scope}"))

    def export(self, path: Path):
        with self.connect() as db:
            payload = {table: [dict(r) for r in db.execute(f"SELECT * FROM {table}")]
                       for table in ("runs", "events", "completions", "task_results")}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def record_task(self, run_id, task_id, title, status, reason):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO task_results VALUES(?,?,?,?,?)", (run_id, task_id, title, status, reason))

    def tasks(self, run_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM task_results WHERE run_id=?", (run_id,))]
