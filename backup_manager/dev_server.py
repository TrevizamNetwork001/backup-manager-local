from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .presentation import ROOT_DIR


WATCH_ROOTS = (ROOT_DIR / "backup_manager", ROOT_DIR / "templates")


def watched_state() -> dict[Path, int]:
    state: dict[Path, int] = {}
    for root in WATCH_ROOTS:
        pattern = "*.py" if root.name == "backup_manager" else "*.html"
        for path in root.rglob(pattern):
            try:
                state[path] = path.stat().st_mtime_ns
            except FileNotFoundError:
                pass
    state[ROOT_DIR / "run.py"] = (ROOT_DIR / "run.py").stat().st_mtime_ns
    return state


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main() -> None:
    env = os.environ.copy()
    env["BACKUP_MANAGER_ENV"] = "development"
    print("Modo desenvolvimento: autoreload de Python/templates ativo; produção permanece inalterada.", flush=True)
    previous = watched_state()
    process = subprocess.Popen([sys.executable, str(ROOT_DIR / "run.py")], cwd=ROOT_DIR, env=env)
    try:
        while True:
            time.sleep(0.75)
            current = watched_state()
            if current != previous:
                print("Alteração detectada; reiniciando servidor de desenvolvimento...", flush=True)
                stop(process)
                process = subprocess.Popen([sys.executable, str(ROOT_DIR / "run.py")], cwd=ROOT_DIR, env=env)
                previous = current
            elif process.poll() is not None:
                raise SystemExit(process.returncode or 1)
    except KeyboardInterrupt:
        pass
    finally:
        stop(process)


if __name__ == "__main__":
    main()
