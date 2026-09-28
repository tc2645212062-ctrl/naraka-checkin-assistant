from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QUrl
from PySide6.QtGui import QKeySequence, QShortcut, QDesktopServices, QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QStackedWidget, QScrollArea,
    QLineEdit, QComboBox, QCheckBox, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFileDialog, QMessageBox, QGraphicsOpacityEffect,
    QSystemTrayIcon, QMenu, QSplitter)
from .adapters import TITLES, MockAdapter
from .config import Config, now, save_config
from .engine import Engine
from .storage import Store
from .presentation import dashboard, STATUS, SOURCE
from .appearance import apply_theme, load_preferences, save_preferences, system_reduced_motion, TaskStrip

ROOT = Path(__file__).resolve().parent.parent
KEY_LIMIT = "金钥匙仅提交来源和有效期已核实的口令，完成状态以小程序复查结果为准。"


def label(text="", name="", wrap=False):
    item = QLabel(text)
    item.setTextFormat(Qt.PlainText)
    item.setObjectName(name)
    item.setWordWrap(wrap)
    item.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return item


def button(text, action, name=""):
    item = QPushButton(text)
    item.setObjectName(name)
    item.clicked.connect(lambda checked=False: action())
    return item


def column(parent=None, margin=0, spacing=12):
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(spacing)
    return layout


def row():
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    return layout


def panel():
    item = QFrame()
    item.setObjectName("panel")
    return item, column(item, 20)


def app_icon():
    pix = QPixmap(64, 64)
    pix.fill(QColor("#242932"))
    painter = QPainter(pix)
    painter.setPen(QColor("#D0AE65"))
    font = painter.font()
    font.setFamily("SimSun")
    font.setPixelSize(43)
    painter.setFont(font)
    painter.drawText(pix.rect(), Qt.AlignCenter, "劫")
    painter.end()
    return QIcon(pix)


class Window(QMainWindow):
    controller = None

    def closeEvent(self, event):
        if self.controller and not self.controller.request_close():
            event.ignore()
        else:
            event.accept()


class App:
    """Qt view/controller; Engine, Config and Store retain their existing formats."""
    def __init__(self, root, data, config, minimized=False, *, preview=False, adapter_factory=None):
        self.root, self.data, self.config = root, Path(data), config
        self.preview, self.adapter_factory = preview, adapter_factory
        self.data.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data/"history.sqlite3")
        if not preview:
            self.store.recover(now().isoformat(timespec="seconds"))
        self.engine = Engine(self.store)
        self.messages, self.cancel = queue.Queue(), threading.Event()
        self.busy, self.active_run, self.worker = False, None, None
        self.preferences = load_preferences(self.data)
        self.system_motion_off = system_reduced_motion()
        self._day, self._history_signature = now().date(), None
        self.force_close, self._dirty, self.tray = False, False, None
        self.daily_task_ready = False
        root.controller = self
        root.setWindowTitle("永劫无间 · 签到助手" + (" — 模拟预览" if preview else ""))
        root.setWindowIcon(app_icon())
        root.setMinimumSize(840, 600)
        screen = QApplication.primaryScreen().availableGeometry()
        root.resize(min(1140, screen.width()-50), min(900, screen.height()-60))
        central = QWidget()
        central.setObjectName("body")
        root.setCentralWidget(central)
        shell = QHBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(176)
        nav = column(sidebar, 16, 8)
        nav.addSpacing(16)
        nav.addWidget(label("永劫无间", "brand"))
        nav.addWidget(label("每日签到助手", "muted"))
        nav.addSpacing(32)
        self.navigation = []
        for index, name in enumerate(("今日概览", "执行计划", "每日任务", "执行日志")):
            item = button(name, lambda i=index: self.navigate(i), "nav")
            item.setCheckable(True)
            nav.addWidget(item)
            self.navigation.append(item)
        nav.addStretch()
        self.plan_badge = label("", "gold", True)
        nav.addWidget(self.plan_badge)
        nav.addWidget(label("本机运行\n北京时间 UTC+8", "caption"))
        nav.addWidget(button("使用说明", lambda: self.open_path(ROOT/"README.md")))
        shell.addWidget(sidebar)
        content = QWidget()
        body = column(content, 24, 14)
        header = row()
        self.page_title = label("今日概览", "pageTitle")
        header.addWidget(self.page_title)
        header.addStretch()
        header.addWidget(label("模拟预览 / 数据隔离" if preview else "真实任务 / 本机记录", "gold"))
        body.addLayout(header)
        self.notice_frame = QFrame()
        self.notice_frame.setObjectName("notice")
        line = QHBoxLayout(self.notice_frame)
        self.notice_text = label("", wrap=True)
        line.addWidget(self.notice_text, 1)
        line.addWidget(button("关闭提示", self.notice_frame.hide))
        body.addWidget(self.notice_frame)
        self.notice_frame.hide()
        self.stack = QStackedWidget()
        self.stack.setAccessibleName("助手主要页面")
        body.addWidget(self.stack, 1)
        body.addWidget(label("电脑开机、桌面解锁、微信登录有效时可自动运行。最小化后继续执行。", "caption", True))
        shell.addWidget(content, 1)
        for name in ("overview", "settings", "tasks", "logs"):
            getattr(self, f"build_{name}")()
        self.effect = QGraphicsOpacityEffect(self.stack)
        self.stack.setGraphicsEffect(self.effect)
        self.fade = QPropertyAnimation(self.effect, b"opacity", root)
        self.fade.setDuration(140)
        self.fade.setEasingCurve(QEasingCurve.OutCubic)
        self.fade.finished.connect(lambda: self.effect.setOpacity(1))
        self.effect.setOpacity(1)
        self.install_shortcuts()
        self.apply_appearance()
        QApplication.styleHints().colorSchemeChanged.connect(self.apply_appearance)
        if not preview and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(root.windowIcon(), root)
            self.tray.setToolTip("永劫无间签到助手")
            menu = QMenu(root)
            menu.addAction("打开助手", self.show_window)
            menu.addAction("停止本轮", self.stop)
            menu.addAction("退出", root.close)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason: self.show_window() if reason == QSystemTrayIcon.DoubleClick else None)
            self.tray.messageClicked.connect(self.show_window)
            self.tray.show()
        self.refresh()
        self.navigate(0, animate=False)
        self.timer = QTimer(root)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        if preview:
            self.notify("模拟预览", "隔离数据，不会操作微信或写入真实签到记录。")
        else:
            self.sync_daily_task()
        root.showMinimized() if minimized else root.show()

    def sync_daily_task(self):
        if self.preview:
            return
        try:
            from .daily_scheduler import sync_daily_task
            sync_daily_task(self.config, self.data)
            self.daily_task_ready = True
        except Exception as exc:
            self.daily_task_ready = False
            (self.data/"schedule-error.log").write_text(f"{now().isoformat()} {exc}\n", encoding="utf-8")
            self.notify("Windows 定时启动未配置", "当前助手内的定时仍有效；退出后无法保证自动启动。" + str(exc))

    @property
    def reduced_motion(self):
        return self.preferences["reduce_motion"] or self.system_motion_off

    def scroll_page(self):
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page = QWidget()
        layout = column(page, 0, 16)
        scroll.setWidget(page)
        self.stack.addWidget(scroll)
        return layout

    def build_overview(self):
        layout = self.scroll_page()
        hero, box = panel()
        line = row()
        self.today_label, self.count_label = label("", "muted"), label("", "gold")
        line.addWidget(self.today_label)
        line.addStretch()
        line.addWidget(self.count_label)
        box.addLayout(line)
        self.status, self.detail = label("尚未检查", "heroTitle", True), label("", "muted", True)
        box.addWidget(self.status)
        box.addWidget(self.detail)
        self.strip = TaskStrip()
        box.addWidget(self.strip)
        line = row()
        self.run_button = button("立即执行", self.run, "primary")
        self.toggle_button = button("启用定时", self.toggle)
        self.cancel_button = button("停止本轮", self.stop)
        self.cancel_button.setEnabled(False)
        for item in (self.run_button, self.toggle_button, self.cancel_button):
            line.addWidget(item)
        line.addStretch()
        box.addLayout(line)
        self.next, self.last = label("", "muted", True), label("", "caption", True)
        box.addWidget(self.next)
        box.addWidget(self.last)
        layout.addWidget(hero)
        line = row()
        line.addWidget(label("今日任务", "sectionTitle"))
        line.addStretch()
        line.addWidget(button("查看本轮日志", self.open_latest_log))
        layout.addLayout(line)
        card, box = panel()
        box.setSpacing(0)
        box.setContentsMargins(16, 2, 16, 2)
        self.task_rows = {}
        for key, title in TITLES.items():
            item = QWidget()
            grid = QGridLayout(item)
            grid.setContentsMargins(0, 6, 0, 6)
            grid.setHorizontalSpacing(16)
            status, reason, source = label(), label("", "muted", True), label("", "caption")
            status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            source.setAlignment(Qt.AlignRight | Qt.AlignTop)
            grid.addWidget(label(title, "sectionTitle"), 0, 0)
            grid.addWidget(status, 0, 1)
            grid.addWidget(reason, 1, 0)
            grid.addWidget(source, 1, 1)
            grid.setColumnStretch(0, 1)
            box.addWidget(item)
            self.task_rows[key] = (status, reason, source)
        layout.addWidget(card)
        layout.addWidget(label(KEY_LIMIT, "caption", True))
        layout.addStretch()

    def build_settings(self):
        layout = self.scroll_page()
        layout.addWidget(label("计划与账号", "sectionTitle"))
        card, box = panel()
        self.fields = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(14)
        fields = [("daily_time", "每日执行时间", "HH:MM，北京时间"), ("account", "账号标记", "区分本地记录"),
                  ("game", "游戏版本", ""), ("expected_role", "角色关键词", "可选，核对绑定角色"),
                  ("task_name", "任务名称", ""), ("task_id", "任务标识", "更换活动时修改"),
                  ("start_date", "活动开始", "可空，YYYY-MM-DD"), ("end_date", "活动结束", "可空，YYYY-MM-DD")]
        for index, (key, title, hint) in enumerate(fields):
            group = QWidget()
            inner = column(group, 0, 5)
            widget = QComboBox() if key == "game" else QLineEdit()
            if key == "game":
                widget.addItems(["端游", "手游"])
                widget.setCurrentText(self.config.game)
                widget.currentTextChanged.connect(self.mark_dirty)
            else:
                widget.setText(getattr(self.config, key))
                widget.setPlaceholderText(hint)
                widget.textEdited.connect(self.mark_dirty)
            self.fields[key] = widget
            title_widget = label(title)
            title_widget.setBuddy(widget)
            widget.setAccessibleName(title)
            inner.addWidget(title_widget)
            inner.addWidget(widget)
            grid.addWidget(group, index//2, index%2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        box.addLayout(grid)
        layout.addWidget(card)
        layout.addWidget(label("微信入口", "sectionTitle"))
        card, box = panel()
        form = QFormLayout()
        form.setSpacing(12)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        for key, title in (("wechat_exe", "微信程序"), ("shortcut", "小程序快捷方式"),
                           ("mini_title", "小程序名称"), ("welfare_label", "福利入口文字")):
            widget = QLineEdit(getattr(self.config, key))
            widget.setAccessibleName(title)
            widget.textEdited.connect(self.mark_dirty)
            self.fields[key] = widget
            line = row()
            line.addWidget(widget)
            if key in ("wechat_exe", "shortcut"):
                line.addWidget(button("选择文件", lambda k=key: self.browse(k)))
            form.addRow(title, line)
        box.addLayout(form)
        box.addWidget(label("小程序快捷方式可留空；不要选择同名游戏启动器。", "caption", True))
        layout.addWidget(card)
        layout.addWidget(label("游戏登录", "sectionTitle"))
        self.auto_game_login = QCheckBox("登录积分未完成时，自动启动并登录端游")
        self.auto_game_login.setChecked(self.config.auto_game_login)
        self.auto_game_login.toggled.connect(self.mark_dirty)
        layout.addWidget(self.auto_game_login)
        layout.addWidget(label("需先安装已授权的游戏操作模块。验证码或页面不明时停止；不自动开始对局或关闭游戏。", "caption", True))
        layout.addWidget(label("外观与辅助功能", "sectionTitle"))
        card, box = panel()
        line = row()
        line.addWidget(label("显示主题"))
        self.theme = QComboBox()
        for title, value in (("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")):
            self.theme.addItem(title, value)
        self.theme.setCurrentIndex(self.theme.findData(self.preferences["theme"]))
        self.theme.setAccessibleName("显示主题")
        self.theme.currentIndexChanged.connect(self.change_preferences)
        line.addWidget(self.theme)
        line.addStretch()
        self.motion = QCheckBox("减少动画")
        self.motion.setChecked(self.preferences["reduce_motion"])
        self.motion.toggled.connect(self.change_preferences)
        line.addWidget(self.motion)
        box.addLayout(line)
        box.addWidget(label("系统关闭动画时自动减少动效。外观即时保存，不改变签到计划。", "caption", True))
        layout.addWidget(card)
        line = row()
        self.save_button = button("保存设置", self.save, "primary")
        self.startup_button = button("设置登录自动启动", self.install_startup)
        self.startup_button.setEnabled(not self.preview)
        line.addWidget(self.save_button)
        line.addWidget(self.startup_button)
        line.addWidget(button("验证模拟状态", self.demo))
        line.addStretch()
        layout.addLayout(line)
        layout.addStretch()

    def build_tasks(self):
        layout = self.scroll_page()
        layout.addWidget(label("按当前小程序配置任务。已完成项目会自动跳过。", "muted", True))
        self.task_vars, self.label_vars = {}, {}
        card, box = panel()
        hints = {"calendar": "识别当天日期与勾选图标", "login": "可配置自动登录端游；领取后核对小程序已完成",
                 "card": "生成名片并核对状态，不发送消息", "poster": "生成海报预览并核对状态",
                 "read": "打开文章、阅读并返回核对", "key": "识别官方口令及有效期，过期不提交"}
        for key, title in TITLES.items():
            block = column(spacing=5)
            check = QCheckBox(title)
            check.setChecked(key in self.config.enabled_tasks)
            check.toggled.connect(self.mark_dirty)
            self.task_vars[key] = check
            block.addWidget(check)
            block.addWidget(label(hints[key], "caption", True))
            if key != "calendar":
                editor = QLineEdit(self.config.task_labels[key])
                editor.setAccessibleName(title + "识别文字")
                editor.textEdited.connect(self.mark_dirty)
                self.label_vars[key] = editor
                block.addWidget(editor)
            box.addLayout(block)
        layout.addWidget(card)
        layout.addWidget(label(KEY_LIMIT, "gold", True))
        layout.addWidget(button("保存全部任务设置", self.save, "primary"), alignment=Qt.AlignLeft)
        layout.addStretch()

    def build_logs(self):
        page = QWidget()
        layout = column(page)
        line = row()
        self.log_filter = QComboBox()
        self.log_filter.addItems(["全部结果", "未完成 / 失败", "已完成", "模拟记录"])
        self.log_filter.setAccessibleName("筛选执行结果")
        self.log_filter.currentIndexChanged.connect(lambda: self.refresh_history(force=True))
        line.addWidget(self.log_filter)
        line.addStretch()
        line.addWidget(button("导出 JSON", self.export))
        line.addWidget(button("数据目录", lambda: self.open_path(self.data)))
        layout.addLayout(line)
        splitter = QSplitter(Qt.Vertical)
        self.history = QTableWidget(0, 4)
        self.history.setHorizontalHeaderLabels(["执行时间", "账号 / 版本", "结果", "来源"])
        self.history.verticalHeader().hide()
        self.history.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history.setSelectionMode(QAbstractItemView.SingleSelection)
        self.history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history.setShowGrid(False)
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.history.itemSelectionChanged.connect(self.select_run)
        self.history.setAccessibleName("执行历史，选择后查看下方详细日志")
        splitter.addWidget(self.history)
        details = QWidget()
        inner = column(details)
        self.log_heading = label("选择记录查看任务结果和详细步骤", "sectionTitle", True)
        inner.addWidget(self.log_heading)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setAccessibleName("详细执行日志")
        self.log_text.setPlaceholderText("尚无执行记录。可以立即执行或等待定时计划。")
        inner.addWidget(self.log_text)
        splitter.addWidget(details)
        splitter.setSizes([230, 390])
        layout.addWidget(splitter, 1)
        self.stack.addWidget(page)

    def install_shortcuts(self):
        self.shortcuts = []
        actions = [(f"Ctrl+{i+1}", lambda n=i: self.navigate(n)) for i in range(4)]
        actions += [("Ctrl+S", self.save), ("Escape", self.stop), ("F5", self.refresh)]
        for sequence, action in actions:
            key = QShortcut(QKeySequence(sequence), self.root)
            key.activated.connect(action)
            self.shortcuts.append(key)

    def navigate(self, index, animate=True):
        self.fade.stop()
        self.effect.setOpacity(1)
        changed = self.stack.currentIndex() != index
        self.stack.setCurrentIndex(index)
        for i, nav in enumerate(self.navigation):
            nav.setChecked(i == index)
        self.page_title.setText(self.navigation[index].text())
        self.navigation[index].setFocus(Qt.ShortcutFocusReason)
        if changed and animate and not self.reduced_motion:
            self.fade.setStartValue(.45)
            self.fade.setEndValue(1.0)
            self.fade.start()

    def apply_appearance(self, *_):
        self.colors = apply_theme(self.root, self.preferences["theme"])
        self.strip.set_states(self.strip.states, self.colors, self.reduced_motion)
        if self.reduced_motion:
            self.fade.stop()
            self.effect.setOpacity(1)

    def change_preferences(self, *_):
        self.preferences = {"theme": self.theme.currentData(), "reduce_motion": self.motion.isChecked()}
        self.apply_appearance()
        try:
            save_preferences(self.data, self.preferences)
        except OSError as exc:
            self.notify("外观未保存", str(exc))

    def mark_dirty(self, *_):
        self._dirty = True
        self.save_button.setText("保存设置 *")

    def browse(self, key):
        pattern = "Windows 程序 (*.exe)" if key == "wechat_exe" else "小程序快捷方式 (*.lnk)"
        path, _ = QFileDialog.getOpenFileName(self.root, "选择微信程序或小程序入口", "", pattern)
        if path:
            self.fields[key].setText(path)
            self.mark_dirty()

    def save(self):
        if self.busy:
            self.notify("正在执行", "请在本轮结束后保存任务设置。")
            return False
        try:
            values = {k: (v.currentText() if isinstance(v, QComboBox) else v.text()).strip() for k,v in self.fields.items()}
            config = replace(self.config, **values, auto_game_login=self.auto_game_login.isChecked(), enabled_tasks=[k for k,v in self.task_vars.items() if v.isChecked()],
                             task_labels={k:v.text().strip() for k,v in self.label_vars.items()})
            save_config(self.data/"config.json", config)
            self.config = config
            self.sync_daily_task()
            self._dirty = False
            self.save_button.setText("保存设置")
            self.refresh()
            self.notify("设置已保存", "定时器使用北京时间。正常执行无需人工确认。")
            return True
        except (ValueError, OSError) as exc:
            self.notify("设置未保存", str(exc))
            return False

    def toggle(self):
        candidate = replace(self.config, enabled=not self.config.enabled)
        try:
            save_config(self.data/"config.json", candidate)
            self.config = candidate
            self.sync_daily_task()
            self.refresh()
        except (ValueError, OSError) as exc:
            self.notify("启停未保存", str(exc))

    def run(self, trigger="manual"):
        if self.busy:
            return
        if self.preview and self.adapter_factory is None:
            self.notify("模拟预览", "此窗口不会执行真实微信任务。可在执行计划中验证模拟状态。")
            return
        self.busy = True
        self.cancel.clear()
        snapshot_config = replace(self.config)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText("正在检查今日任务")
        self.detail.setText("逐项读取页面状态；识别不明确时停止。按 Esc 可停止本轮。")
        if not self.preview:
            self.root.showMinimized()
        def work():
            com_initialized = False
            try:
                if self.cancel.wait(.02 if self.preview else 3):
                    self.messages.put(("cancelled", None))
                    return
                if self.adapter_factory:
                    adapter = self.adapter_factory(self.cancel)
                else:
                    from .desktop.automation import DesktopAdapter
                    import pythoncom
                    pythoncom.CoInitialize()
                    com_initialized = True
                    evidence = self.data/"evidence"/now().strftime("%Y%m%d-%H%M%S-%f")
                    adapter = DesktopAdapter(evidence, cancel=self.cancel, manual=trigger == "manual")
                record = self.engine.run(snapshot_config, adapter, "simulation" if self.preview else trigger)
                self.messages.put(("result", record))
            except Exception as exc:
                self.messages.put(("error", f"{type(exc).__name__}: {exc}"))
            finally:
                if com_initialized:
                    pythoncom.CoUninitialize()
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def stop(self):
        if self.busy:
            self.cancel.set()
            self.detail.setText("已请求停止，当前识别结束后停止操作。")
            self.cancel_button.setEnabled(False)

    def tick(self):
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "startup":
                    self.startup_button.setEnabled(not self.preview)
                    self.notify(*value)
                    continue
                self.busy = False
                self.run_button.setEnabled(True)
                self.cancel_button.setEnabled(False)
                if kind == "result":
                    self.active_run = value["id"]
                    self.refresh()
                    self.notify(("模拟 · " if value["source"] == "simulation" else "") + STATUS.get(value["status"],value["status"]), value["reason"], result=True)
                elif kind == "error":
                    self.store.log(None, now().isoformat(), value)
                    self.status.setText("执行失败")
                    self.detail.setText(value)
                    self.notify("执行失败", value, result=True)
                else:
                    self.status.setText("已停止")
                    self.detail.setText("本轮尚未开始操作微信。")
        except queue.Empty:
            pass
        try:
            motion_off = system_reduced_motion()
            if motion_off != self.system_motion_off:
                self.system_motion_off = motion_off
                self.apply_appearance()
            if self._day != now().date() or self.busy:
                self._day = now().date()
                self.refresh()
            if not self.preview and not self.busy:
                if self.engine.due(self.config):
                    self.run("scheduled")
                elif self.retry_due():
                    self.run("scheduled-retry")
            self.next.setText("下次执行  " + self.next_text())
        except Exception as exc:
            self.notify("定时器异常", str(exc))

    def refresh(self):
        self.view = view = dashboard(self.store, self.config, now().date().isoformat(), self.preview)
        self.today_label.setText(f"{now():%m 月 %d 日}  /  {self.config.game}")
        self.count_label.setText(f"{'模拟完成' if self.preview else '页面证据完成'}  {view['done']} / {view['total']}")
        current = view["latest"]
        if not self.busy:
            text = STATUS.get(current["status"],current["status"]) if current else "今日尚未检查"
            if view["done"] == view["total"] and view["total"]:
                text = "今日任务已完成"
            elif current and current["status"] in {"success", "already_signed"}:
                text = "完成记录待逐项核对"
            elif view["done"] and current:
                text = "还有任务需要处理"
            self.status.setText(("模拟 · " if self.preview else "") + text)
            self.detail.setText(current["reason"] if current else "到达计划时间后自动检查；也可以现在开始。")
        self.strip.set_states([t["complete"] for t in view["tasks"]], self.colors, self.reduced_motion)
        previous = view["previous"]
        self.last.setText("最近执行  " + (previous["started"].replace("T", " ")[:19] + "  /  " + SOURCE.get(previous["source"],previous["source"]) if previous else "暂无记录"))
        self.next.setText("下次执行  " + self.next_text())
        self.plan_badge.setText(f"{'定时已启用' if self.config.enabled else '定时已暂停'}\n每日 {self.config.daily_time}")
        self.toggle_button.setText("暂停定时" if self.config.enabled else "启用定时")
        for task in view["tasks"]:
            status, reason, source = self.task_rows[task["task_id"]]
            caption = STATUS.get(task["status"], task["status"])
            status.setText(("模拟 · " if task["source"] == "simulation" else "") + caption)
            tone = "success" if task["complete"] else "warning" if task["status"] in {"waiting_release", "precondition", "manual_required"} else "error" if task["status"] in {"error", "network_error", "unrecognized"} else ""
            status.setProperty("tone", tone)
            status.style().unpolish(status)
            status.style().polish(status)
            reason.setText(task["reason"])
            source.setText(SOURCE.get(task["source"], "—"))
        self.refresh_history()

    def refresh_history(self, force=False):
        rows = self.store.history(300)
        choice = self.log_filter.currentIndex()
        if choice == 1:
            rows = [r for r in rows if r["status"] not in {"success", "already_signed"}]
        elif choice == 2:
            rows = [r for r in rows if r["status"] in {"success", "already_signed"}]
        elif choice == 3:
            rows = [r for r in rows if r["source"] == "simulation"]
        signature = repr(rows)
        if not force and signature == self._history_signature:
            return
        self._history_signature = signature
        selected = self.active_run
        self.history.blockSignals(True)
        self.history.setRowCount(len(rows))
        for index, record in enumerate(rows):
            scope = json.loads(record["scope"])
            values = (record["started"].replace("T"," ")[:19], f"{scope[0]} / {scope[1]}",
                      STATUS.get(record["status"],record["status"]), SOURCE.get(record["source"],record["source"]))
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, record["id"])
                item.setToolTip(value)
                self.history.setItem(index, col, item)
            self.history.setRowHeight(index, 42)
        self.history.blockSignals(False)
        chosen = next((i for i,r in enumerate(rows) if r["id"] == selected), 0)
        if rows:
            self.history.selectRow(chosen)
            self.show_logs(rows[chosen]["id"])
        else:
            self.log_heading.setText("没有符合筛选条件的记录")
            self.log_text.clear()

    def select_run(self):
        items = self.history.selectedItems()
        if items:
            self.active_run = items[0].data(Qt.UserRole)
            self.show_logs(self.active_run)

    def show_logs(self, run_id):
        record = self.store.get_run(run_id)
        if not record:
            return
        self.log_heading.setText(f"#{run_id}  {STATUS.get(record['status'],record['status'])}  /  {SOURCE.get(record['source'],record['source'])}")
        lines = [record["reason"], ""]
        for task in self.store.tasks(run_id):
            lines.extend([f"{task['title']}  {STATUS.get(task['status'],task['status'])}", task["reason"], ""])
        for event in self.store.events(run_id):
            lines.append(f"{event['time']}\n{event['message']}\n")
        self.log_text.setPlainText("\n".join(lines))

    def open_latest_log(self):
        self.navigate(3)
        if self.view["latest"]:
            self.active_run = self.view["latest"]["id"]
            self.log_filter.setCurrentIndex(0)
            self.refresh_history(force=True)

    def notify(self, title, detail, result=False):
        self.notice_text.setText(f"{title}\n{detail}")
        self.notice_frame.show()
        if result and self.tray:
            self.tray.showMessage(title, detail, QSystemTrayIcon.Information, 10000)
        if result and not self.preview:
            try:
                import winsound
                winsound.MessageBeep()
            except (ImportError, RuntimeError):
                pass

    def show_window(self):
        self.root.showNormal()
        self.root.raise_()
        self.root.activateWindow()

    @staticmethod
    def open_path(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def export(self):
        filename, _ = QFileDialog.getSaveFileName(self.root, "导出执行日志", "签到日志.json", "JSON 文件 (*.json)")
        if filename:
            try:
                self.store.export(Path(filename))
                self.notify("日志已导出", filename)
            except OSError as exc:
                self.notify("导出失败", str(exc))

    def install_startup(self):
        if self.preview:
            return
        self.startup_button.setEnabled(False)
        def install():
            try:
                result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT/"scripts"/"Install-Startup.ps1"), "-Python", sys.executable, "-DataDir", str(self.data)], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode:
                    raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
                message = ("登录自动启动已设置", "下次登录 Windows 后启动助手；请同时启用定时。")
            except Exception as exc:
                message = ("设置失败", str(exc))
            self.messages.put(("startup", message))
        threading.Thread(target=install, daemon=True).start()

    def demo(self):
        output = []
        with tempfile.TemporaryDirectory(prefix="naraka-demo-") as folder:
            for scenario in ("success", "already_signed", "manual_required", "network_error"):
                result = Engine(Store(Path(folder)/f"{scenario}.db")).run(Config(), MockAdapter(scenario), "simulation")
                output.append(f"{STATUS[scenario]}：{result['reason']}")
        self.notify("四种模拟已运行，未写入真实记录", "\n".join(output))

    def request_close(self):
        if self.force_close:
            self.timer.stop()
            return True
        if self.busy:
            self.notify("任务正在执行", "请先停止本轮，等待结束后退出；或最小化继续运行。")
            return False
        if not self.preview and (self.config.enabled or self._dirty):
            detail = (("退出后本轮重试停止；Windows 将在下次每日时间启动助手。要停用任务，请先暂停定时。" if self.daily_task_ready else "退出后定时器停止。") if self.config.enabled else "") + ("尚有未保存设置。" if self._dirty else "")
            answer = QMessageBox.question(self.root, "退出助手", detail+"\n确定退出？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return False
        self.timer.stop()
        if self.tray:
            self.tray.hide()
        return True


    def retry_at(self):
        if not self.config.enabled:
            return None
        rows = [r for r in self.store.history(500) if r['day'] == now().date().isoformat() and r['scope'] == self.config.scope]
        if not rows or self.store.completion(now().date().isoformat(), self.config.scope):
            return None
        latest = rows[0]
        if not latest['finished']:
            return None
        retries = sum((r['trigger'] == 'scheduled-retry' for r in rows))
        if latest['status'] in ('network_error', 'desktop_busy') and retries < 3:
            return datetime.fromisoformat(latest['finished']) + timedelta(minutes=5)
        pending = {t['status'] for t in self.store.tasks(latest['id'])} - {'success', 'already_signed'}
        if latest['status'] == 'partial' and pending and (pending <= {'waiting_release', 'precondition'}) and (retries < 12):
            target = datetime.fromisoformat(latest['finished']) + timedelta(minutes=30)
            return target if target.date() == now().date() else None
        return None

    def retry_due(self):
        target = self.retry_at()
        return target is not None and now() >= target

    def next_text(self):
        daily = self.engine.next_run(self.config)
        retry = self.retry_at()
        return daily + (f'；待完成任务 {retry:%H:%M} 重查' if retry else '') + ('（模拟，不会自动执行）' if getattr(self, 'preview', False) else '')
