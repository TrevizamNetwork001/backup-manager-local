from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_PATH = Path(os.environ.get("BACKUP_MANAGER_SERVICE_SNAPSHOT", "/opt/backup-manager-local/data/service-status.json"))
UNITS = {
    "backup-manager-local": "backup-manager-local.service",
    "worker": "backup-manager-worker.service",
    "scheduler": "backup-manager-worker.timer",
    "ftp-importer": "backup-manager-ftp-importer.timer",
    "pure-ftpd": "pure-ftpd.service",
}


def unit_state(unit: str) -> dict[str, str]:
    result = subprocess.run(
        ["/usr/bin/systemctl", "show", unit, "--no-pager",
         "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus,StateChangeTimestamp"],
        capture_output=True, text=True, timeout=8, check=False,
    )
    values = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return {
        "unit": unit,
        "load": values.get("LoadState", "unknown"),
        "active": values.get("ActiveState", "unknown"),
        "sub": values.get("SubState", "unknown"),
        "result": values.get("Result", "unknown"),
        "exit_status": values.get("ExecMainStatus", ""),
        "changed_at": values.get("StateChangeTimestamp", ""),
    }


def create_snapshot() -> dict:
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "services": {name: unit_state(unit) for name, unit in UNITS.items()},
    }
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".service-status-", dir=SNAPSHOT_PATH.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, SNAPSHOT_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return payload


def main() -> None:
    payload = create_snapshot()
    print(f"snapshot_at={payload['generated_at']} services={len(payload['services'])}")


if __name__ == "__main__":
    main()
