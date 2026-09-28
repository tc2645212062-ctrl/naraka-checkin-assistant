"""Render real Qt widgets using synthetic records only; no desktop adapter is imported."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollArea
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from assistant.config import Config, load_config
from assistant.ui import App, Window
from assistant.preview import PreviewAdapter


def main():
    qt = QApplication.instance() or QApplication(sys.argv[:1])
    qt.setStyle("Fusion")
    assert "Microsoft YaHei UI" in QFontDatabase.families(), "Native Windows fonts required for screenshot verification"
    output = Path(__file__).resolve().parents[1]/"test-output"/os.environ.get("UI_SCREENSHOT_DIR", "qt")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="naraka-ui-smoke-") as folder:
        window = Window()
        app = App(window, Path(folder), Config(enabled=True), preview=True)
        app.timer.stop()
        app.engine.run(app.config, PreviewAdapter("partial"), "simulation")
        app.refresh()
        app.fields["daily_time"].setText("21:30")
        assert app.save()
        assert load_config(Path(folder)/"config.json").daily_time == "21:30"
        app.toggle()
        assert not app.config.enabled
        app.toggle()
        assert app.config.enabled
        app.notify("模拟预览", "五项完成、金钥匙过期均为示例数据，不是新的实机签到结果。")
        available = qt.primaryScreen().availableGeometry()
        window.resize(min(1140, available.width()-50), min(920, available.height()-60))
        for theme in ("light", "dark"):
            app.theme.setCurrentIndex(app.theme.findData(theme))
            for index, name in enumerate(("overview", "settings", "tasks", "logs")):
                app.navigate(index, animate=False)
                QTest.qWait(220)
                window.grab().save(str(output/f"{theme}-{name}.png"))
                page = app.stack.currentWidget()
                if isinstance(page, QScrollArea) and page.verticalScrollBar().maximum():
                    page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
                    QTest.qWait(100)
                    window.grab().save(str(output/f"{theme}-{name}-bottom.png"))
                    page.verticalScrollBar().setValue(0)
        app.navigate(0, animate=False)
        window.resize(840, 600)
        QTest.qWait(200)
        window.grab().save(str(output/"dark-compact.png"))
        app.notice_frame.hide()
        window.resize(min(1140, available.width()-50), min(920, available.height()-60))
        app.preferences["reduce_motion"] = False
        app.system_motion_off = False
        app.navigate(0, animate=False)
        QTest.qWait(100)
        frames = []
        app.navigate(3)
        for index in range(7):
            QTest.qWait(25)
            path = output/f"motion-{index}.png"
            window.grab().save(str(path))
            frames.append(path)
        from PIL import Image
        images = [Image.open(p).convert("RGB") for p in frames]
        images[0].save(output/"page-transition.gif",save_all=True,append_images=images[1:],duration=[25]*6+[900],loop=0)
        for frame in images:
            frame.close()
        print(f"Native platform={qt.platformName()}; DPR={window.devicePixelRatioF()}; screenshots={output}")
        app.force_close = True
        window.close()
        qt.processEvents()
    print("Qt UI smoke passed: isolated data, config persistence, toggle, both themes, four pages, compact window")


if __name__ == "__main__":
    main()
