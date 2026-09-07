from __future__ import annotations

import fcntl
import os
from pathlib import Path

from .db import SchemaCompatibilityError, connect, require_schema_current
from .telegram_backup import process_test_files, run_once


def main() -> None:
    try:
        require_schema_current()
    except SchemaCompatibilityError as exc:
        print(exc.code)
        raise SystemExit(3)
    lock_path = Path(os.environ.get("BACKUP_MANAGER_TELEGRAM_LOCK", "/tmp/backup-manager-telegram-backup.lock"))
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("telegram-backup worker: outra execução está ativa")
            return
        with connect() as conn:
            tests = process_test_files(conn)
            result = run_once(conn)
    print("telegram-backup " + " ".join(f"{key}={value}" for key, value in {**tests, **result}.items()))


if __name__ == "__main__":
    main()
