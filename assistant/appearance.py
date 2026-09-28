from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path
from PySide6.QtCore import Qt, Property, QPropertyAnimation, QEasingCurve, QRectF
from PySide6.QtGui import QColor, QPainter, QPalette, QFont
from PySide6.QtWidgets import QWidget, QApplication

LIGHT = dict(bg="#F5F6F8", panel="#FFFFFF", side="#EDEFF2", ink="#242932", muted="#626C7A",
             line="#D8DDE4", gold="#856421", goldbg="#F1E9D6", hover="#E5E9EF", green="#267053",
             red="#A43D3D", amber="#8B6218", selection="#DDE7F1")
DARK = dict(bg="#191E26", panel="#232B36", side="#151A21", ink="#E7EAF0", muted="#A8B2C1",
            line="#3A4555", gold="#D0AE65", goldbg="#3D3423", hover="#303C4C", green="#81C9A8",
            red="#F19E9E", amber="#E0BB70", selection="#34495F")


def system_reduced_motion():
    if sys.platform != "win32":
        return False
    enabled = ctypes.c_int(1)
    ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
    return bool(ok and not enabled.value)


def load_preferences(data: Path):
    defaults = {"theme": "system", "reduce_motion": False}
    path = data / "ui-settings.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("theme") in ("system", "light", "dark"):
            defaults["theme"] = raw["theme"]
        if type(raw.get("reduce_motion")) is bool:
            defaults["reduce_motion"] = raw["reduce_motion"]
    except (OSError, ValueError, AttributeError):
        pass  # Appearance corruption must not change the task configuration.
    return defaults


def save_preferences(data, preferences):
    path = data / "ui-settings.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(preferences, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def colors(theme):
    dark = theme == "dark" or (theme == "system" and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark)
    return DARK if dark else LIGHT


def apply_theme(window, theme):
    c = colors(theme)
    palette = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.WindowText, "ink"), (QPalette.Base, "panel"),
                      (QPalette.AlternateBase, "side"), (QPalette.Text, "ink"), (QPalette.Button, "panel"),
                      (QPalette.ButtonText, "ink"), (QPalette.Highlight, "selection"),
                      (QPalette.HighlightedText, "ink"), (QPalette.ToolTipBase, "panel"), (QPalette.ToolTipText, "ink")):
        palette.setColor(role, QColor(c[key]))
    QApplication.instance().setPalette(palette)
    window.setStyleSheet("""
        QWidget { color: %(ink)s; font-family: 'Microsoft YaHei UI'; font-size: 13px; }
        QMainWindow, QWidget#body, QScrollArea, QScrollArea > QWidget > QWidget { background: %(bg)s; }
        QFrame#sidebar { background: %(side)s; border-right: 1px solid %(line)s; }
        QFrame#panel, QFrame#taskList { background: %(panel)s; border: 1px solid %(line)s; border-radius: 10px; }
        QFrame#notice { background: %(goldbg)s; border-radius: 6px; }
        QLabel { background: transparent; }
        QLabel#brand { font-family: 'SimSun'; font-size: 24px; font-weight: bold; }
        QLabel#pageTitle { font-size: 25px; font-weight: 600; }
        QLabel#heroTitle { font-size: 27px; font-weight: 600; }
        QLabel#sectionTitle { font-size: 15px; font-weight: 600; }
        QLabel#muted, QLabel#caption { color: %(muted)s; }
        QLabel#caption { font-size: 11px; }
        QLabel#gold { color: %(gold)s; }
        QLabel[tone="success"] { color: %(green)s; }
        QLabel[tone="warning"] { color: %(amber)s; }
        QLabel[tone="error"] { color: %(red)s; }
        QPushButton { background: %(panel)s; border: 1px solid %(line)s; border-radius: 6px; padding: 8px 14px; min-height: 18px; }
        QPushButton:hover { background: %(hover)s; }
        QPushButton:pressed { background: %(selection)s; }
        QPushButton:focus, QLineEdit:focus, QComboBox:focus, QCheckBox:focus, QPlainTextEdit:focus, QTableWidget:focus { border: 2px solid %(gold)s; }
        QPushButton:disabled { color: %(muted)s; background: %(side)s; }
        QPushButton#primary { color: %(panel)s; background: %(ink)s; border-color: %(ink)s; font-weight: 600; }
        QPushButton#primary:hover { background: %(gold)s; border-color: %(gold)s; }
        QPushButton#nav { text-align: left; background: transparent; border: 1px solid transparent; padding: 12px 16px; }
        QPushButton#nav:checked { background: %(panel)s; border-left: 3px solid %(gold)s; font-weight: 600; }
        QPushButton#nav:hover { background: %(hover)s; }
        QPushButton#nav:focus { border: 2px solid %(gold)s; }
        QLineEdit, QComboBox, QSpinBox, QTimeEdit { background: %(panel)s; border: 1px solid %(line)s; border-radius: 5px; padding: 7px 9px; min-height: 20px; selection-background-color: %(selection)s; }
        QComboBox::drop-down { width: 24px; border: none; }
        QCheckBox { spacing: 10px; padding: 6px 2px; }
        QCheckBox::indicator { width: 17px; height: 17px; }
        QPlainTextEdit, QTableWidget { background: %(panel)s; border: 1px solid %(line)s; border-radius: 6px; padding: 4px; selection-background-color: %(selection)s; }
        QTableWidget { gridline-color: %(line)s; }
        QHeaderView::section { background: %(side)s; border: none; padding: 10px; text-align: left; color: %(muted)s; }
        QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
        QScrollBar::handle:vertical { background: %(line)s; min-height: 28px; border-radius: 3px; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        QToolTip { background: %(panel)s; color: %(ink)s; border: 1px solid %(line)s; padding: 6px; }
    """ % c)
    return c


class TaskStrip(QWidget):
    """Six positions correspond to the six task rows; no decorative endless loop."""
    def __init__(self):
        super().__init__()
        self.states, self.progress, self.palette_colors = [False] * 6, 1.0, LIGHT
        self.setFixedHeight(9)
        self.animation = QPropertyAnimation(self, b"reveal", self)
        self.animation.setDuration(180)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.setAccessibleName("六项任务完成状态，详情见下方任务列表")

    def get_reveal(self):
        return self.progress

    def set_reveal(self, value):
        self.progress = value
        self.update()

    reveal = Property(float, get_reveal, set_reveal)

    def set_states(self, states, c, reduced):
        changed = states != self.states
        self.states, self.palette_colors = states, c
        if changed or reduced:
            self.animation.stop()
            self.set_reveal(1.0)
        if changed and not reduced:
            self.animation.setStartValue(0.0)
            self.animation.setEndValue(1.0)
            self.animation.start()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        width = (self.width()-5*5)/6
        for index, done in enumerate(self.states):
            rect = QRectF(index*(width+5), 0, width, 6)
            p.setBrush(QColor(self.palette_colors["line"]))
            p.drawRoundedRect(rect, 3, 3)
            if done:
                rect.setWidth(width*self.progress)
                p.setBrush(QColor(self.palette_colors["gold"]))
                p.drawRoundedRect(rect, 3, 3)
