from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from assistant.config import Config, data_directory, load_config, now, save_config
from assistant.platform import InstanceLock, discover_wechat


def main():
    parser = argparse.ArgumentParser(description="永劫无间 Windows 自动签到助手")
    parser.add_argument("--data-dir", type=Path, default=data_directory())
    parser.add_argument("--minimized", action="store_true")
    parser.add_argument("--scheduled-start", action="store_true", help="由 Windows 计划任务启动；暂停配置时直接退出")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run-now", action="store_true", help="启动界面后用日常后台线程立即执行一次")
    mode.add_argument("--once", action="store_true", help="立即执行一次真实自动签到")
    parser.add_argument("--demo", choices=("success", "already_signed", "manual_required", "network_error", "activity_missing"))
    mode.add_argument("--smoke", action="store_true", help="临时模拟数据创建 Qt 界面，两秒后关闭")
    mode.add_argument("--preview", action="store_true", help="打开隔离的模拟界面，不操作微信")
    parser.add_argument("--scenario", default="partial", choices=("partial", "success", "already_signed", "manual_required", "network_error", "activity_missing"))
    args = parser.parse_args()
    if args.demo:
        import tempfile
        from assistant.adapters import MockAdapter
        from assistant.engine import Engine
        from assistant.storage import Store
        with tempfile.TemporaryDirectory(prefix="naraka-demo-") as folder:
            result = Engine(Store(Path(folder)/"demo.db")).run(Config(), MockAdapter(args.demo), "simulation")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if sys.platform != "win32":
        raise SystemExit("真实签到和界面运行需要 Windows。模拟与核心测试可跨平台运行。")
    if args.smoke or args.preview:
        import tempfile
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from assistant.ui import App, Window
        from assistant.preview import PreviewAdapter
        with tempfile.TemporaryDirectory(prefix="naraka-ui-preview-") as folder:
            qt = QApplication(sys.argv[:1])
            qt.setStyle("Fusion")
            window = Window()
            ui = App(window, Path(folder), Config(), preview=True,
                     adapter_factory=lambda cancel: PreviewAdapter(args.scenario, cancel))
            ui.engine.run(ui.config, PreviewAdapter(args.scenario), "simulation")
            ui.refresh()
            if args.smoke:
                QTimer.singleShot(2000, window.close)
            return qt.exec()
    args.data_dir = args.data_dir.resolve()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    if args.scheduled_start and not load_config(args.data_dir/"config.json").enabled:
        return 0
    lock = InstanceLock(args.data_dir/"instance.lock")
    if not lock.acquire():
        if args.once:
            print("已有助手实例运行；本次未执行。")
            return 3
        if not args.minimized:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, "助手已经运行，请查看任务栏。", "永劫无间签到助手", 0)
        return 0
    try:
        config = load_config(args.data_dir/"config.json")
        if not config.wechat_exe:
            config.wechat_exe = discover_wechat()
        if not (args.data_dir/"config.json").exists():
            save_config(args.data_dir/"config.json", config)
        if args.once:
            from assistant.desktop.automation import DesktopAdapter
            from assistant.engine import Engine
            from assistant.storage import Store
            import time
            time.sleep(3)
            store = Store(args.data_dir/"history.sqlite3")
            store.recover(now().isoformat())
            adapter = DesktopAdapter(args.data_dir/"evidence"/now().strftime("%Y%m%d-%H%M%S-%f"), manual=True)
            result = Engine(store).run(config, adapter)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] in ("success", "already_signed") else 2
        from PySide6.QtWidgets import QApplication
        from assistant.ui import App, Window
        qt = QApplication(sys.argv[:1])
        qt.setStyle("Fusion")
        root = Window()
        ui = App(root, args.data_dir, config, args.minimized)
        if args.run_now:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: ui.run("manual"))
        return qt.exec()
    except Exception as exc:
        (args.data_dir/"startup-error.log").write_text(f"{now().isoformat()}\n{type(exc).__name__}: {exc}\n", encoding="utf-8")
        if args.once:
            raise
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(exc), "助手启动失败（原数据已保留）", 16)
        return 1
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
