"""Reproduce Qt/background-thread login, with no model or manual input."""
import sys
import threading
import runpy
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assistant.desktop.session import WindowSession


def main():
    app = QApplication(sys.argv[:1])
    result = []
    def worker():
        import pythoncom
        pythoncom.CoInitialize()
        session = WindowSession(print)
        try:
            original = sys.argv
            sys.argv = [str(Path(__file__).with_name('Verify-GameLogin.py')), '--run']
            try:
                code = runpy.run_path(sys.argv[0])['main']()
            finally:
                sys.argv = original
            if code == 0:
                session.remember_started_game()
                session.finish()
            result.append(code)
        except Exception as exc:
            print(str(exc), flush=True)
            result.append(1)
        finally:
            pythoncom.CoUninitialize()
    thread = threading.Thread(target=worker, daemon=False)
    timer = QTimer()
    timer.timeout.connect(lambda: app.exit(result[0]) if result else None)
    timer.start(200)
    thread.start()
    code = app.exec()
    thread.join()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
