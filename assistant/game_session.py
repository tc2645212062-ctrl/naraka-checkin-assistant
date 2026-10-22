"""Own and clean up only processes started by this automatic game session."""
from __future__ import annotations

import json
import time
from pathlib import Path

import psutil


GAME = r'e:\naraka\program\narakabladepoint.exe'
LAUNCHER = r'e:\naraka\tool\loothoarder.exe'


class GameProcessSession:
    def __init__(self, evidence, event=lambda message: None):
        self.evidence = Path(evidence)
        self.event = event
        self.initial = {row['pid']: row for row in self.inventory()}
        self.owned = {}
        self.launchers = {row['pid']: row for row in self.initial.values() if row['exe'] == LAUNCHER}
        self.launcher_requested_at = None
        self.game_requested_at = None
        self.game_launch_accepted = False
        self.game_identity = None
        self.errors = []
        self.native_expected = False
        self.native_report = None
        self.write('initial')

    @staticmethod
    def identity(process):
        return {'pid': process.pid, 'created': process.create_time(), 'exe': process.exe().lower(),
                'ppid': process.ppid()}

    @classmethod
    def inventory(cls):
        rows = []
        for process in psutil.process_iter(['name']):
            if (process.info.get('name') or '').lower() not in {'narakabladepoint.exe', 'loothoarder.exe'}:
                continue
            try:
                row = cls.identity(process)
                if row['exe'] in {GAME, LAUNCHER}:
                    rows.append(row)
            except psutil.Error:
                pass
        return rows

    @staticmethod
    def same_process(row):
        try:
            current = psutil.Process(row['pid'])
            return current if current.create_time() == row['created'] and current.exe().lower() == row['exe'] else None
        except psutil.NoSuchProcess:
            return None

    def write(self, phase, **extra):
        self.evidence.mkdir(parents=True, exist_ok=True)
        target = self.evidence / 'process-session.json'
        temporary = target.with_suffix('.tmp')
        record = {'phase': phase, 'observed_at': time.time(), 'initial': list(self.initial.values()),
                  'owned': list(self.owned.values()), 'errors': self.errors, **extra}
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(target)

    def expect_launcher(self):
        self.launcher_requested_at = time.time()

    def expect_game(self):
        self.game_requested_at = time.time()

    def accepted_game(self):
        self.game_launch_accepted = True
        self.discover()

    def discover(self):
        candidates = []
        for row in self.inventory():
            original = self.initial.get(row['pid'])
            if original and original['created'] == row['created']:
                continue  # A user-owned process stays outside this session.
            if row['exe'] == LAUNCHER and self.launcher_requested_at is not None and row['created'] >= self.launcher_requested_at:
                self.launchers[row['pid']] = row
                self.owned[row['pid']] = row
            if (row['exe'] == GAME and self.game_launch_accepted and self.game_requested_at is not None
                    and row['created'] >= self.game_requested_at and self.game_identity is None):
                process = self.same_process(row)
                try:
                    parents = process.parents() if process else []
                    if any(parent.pid in self.launchers
                           and parent.create_time() == self.launchers[parent.pid]['created']
                           and parent.exe().lower() == LAUNCHER for parent in parents):
                        candidates.append(row)
                except psutil.Error:
                    pass
        if len(candidates) == 1:
            self.game_identity = candidates[0]
            self.owned[candidates[0]['pid']] = candidates[0]
        self.write('observing')

    @staticmethod
    def close_windows(pids):
        import win32con
        import win32gui
        import win32process
        def close(handle, _):
            if win32process.GetWindowThreadProcessId(handle)[1] in pids:
                try:
                    win32gui.PostMessage(handle, win32con.WM_CLOSE, 0, 0)
                except win32gui.error:
                    pass  # A denied close still needs an explicit residual result.
        win32gui.EnumWindows(close, None)

    def cleanup(self, close=True):
        self.discover()
        targets = {}
        unreadable = []
        for row in self.owned.values():
            try:
                if self.same_process(row):
                    targets[row['pid']] = row
            except psutil.Error:
                unreadable.append(row)
        # Snapshot descendants while the root identity is still verified. Never
        # find or terminate targets by process name, parent PID alone or old PID.
        for row in list(targets.values()):
            process = self.same_process(row)
            if process:
                try:
                    for child in process.children(recursive=True):
                        identity = self.identity(child)
                        initial = self.initial.get(child.pid)
                        if identity['created'] >= row['created'] and not (initial and initial['created'] == identity['created']):
                            targets[child.pid] = identity
                except psutil.Error as exc:
                    self.errors.append(type(exc).__name__ + ': enumerate owned descendants')
        if close and targets:
            self.event('清理本轮确认启动的游戏进程；先请求正常退出，限定等待后检查残留')
            try:
                self.close_windows(set(targets))
                processes = [process for row in targets.values() if (process := self.same_process(row))]
                psutil.wait_procs(processes, timeout=5)
                # Children first, root last. terminate() on Windows is bounded
                # TerminateProcess; no global taskkill and no privilege changes.
                for row in sorted(targets.values(), key=lambda item:item['created'], reverse=True):
                    process = self.same_process(row)
                    if process:
                        try:
                            process.terminate()
                        except psutil.NoSuchProcess:
                            pass
                        except psutil.Error as exc:
                            self.errors.append(type(exc).__name__ + ': terminate owned PID ' + str(row['pid']))
                processes = [process for row in targets.values() if (process := self.same_process(row))]
                psutil.wait_procs(processes, timeout=3)
            except psutil.Error as exc:
                self.errors.append(type(exc).__name__ + ': owned cleanup')
        remaining = list(unreadable)
        for row in targets.values():
            try:
                if self.same_process(row):
                    remaining.append(row)
            except psutil.Error:
                remaining.append(row)  # Unreadable is not proof of exit.
        if close and self.native_expected:
            if not self.native_report:
                remaining.append('native-cleanup-unverified')
            elif (self.native_report.get('remaining') or self.native_report.get('unverified_new_pids')
                  or any(error.startswith(('child:', 'cleanup:', 'verify:')) for error in self.native_report.get('errors', []))):
                remaining.append('native-cleanup-residual-or-unverified')
        self.write('finished' if close else 'retained-by-setting', remaining=remaining, cleanup_requested=close)
        if close:
            self.event('本轮游戏进程残留：' + str(len(remaining)))
        return remaining if close else []
