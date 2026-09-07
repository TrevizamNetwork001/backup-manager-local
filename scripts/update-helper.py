#!/usr/bin/env python3
"""Helper root-only com ações e caminhos estritamente allowlisted."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backup_manager.db import DB_PATH, connect
from backup_manager.updater import (DEFAULT_BACKUP_ROOT, DEFAULT_PUBLIC_KEY, DEFAULT_UPDATE_ROOT,
    UpdateError, apply_staged, create_backup, record_validation, restore_backup, root_required,
    run_migrations, safe_extract, systemctl, validate_package)


def paths(operation: str) -> tuple[Path, Path]:
    incoming = Path(os.environ.get("BACKUP_MANAGER_UPDATE_ROOT", str(DEFAULT_UPDATE_ROOT))) / "incoming" / f"{operation}.bmu"
    staging = Path(os.environ.get("BACKUP_MANAGER_UPDATE_ROOT", str(DEFAULT_UPDATE_ROOT))) / "staging" / operation
    return incoming, staging


def operation_row(conn, operation: str):
    row = conn.execute("SELECT * FROM update_operations WHERE uuid=?", (operation,)).fetchone()
    if not row: raise UpdateError("OPERATION_NOT_FOUND")
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["validate", "stage", "apply", "rollback", "status"])
    parser.add_argument("--operation", default="")
    args = parser.parse_args()
    root_required()
    try:
        if not args.operation:
            if args.action != "apply": raise UpdateError("OPERATION_REQUIRED")
            with connect() as conn:
                pending = conn.execute("SELECT uuid FROM update_operations WHERE status='ready' ORDER BY id LIMIT 1").fetchone()
            if not pending: raise UpdateError("OPERATION_NOT_FOUND")
            args.operation = pending["uuid"]
        incoming, staging = paths(args.operation)
        with connect() as conn:
            row = operation_row(conn, args.operation)
            if args.action == "status": print(row["status"]); return 0
            if args.action == "validate":
                result = validate_package(incoming, os.environ.get("BACKUP_MANAGER_UPDATE_PUBLIC_KEY", str(DEFAULT_PUBLIC_KEY)))
                record_validation(conn, args.operation, result); print("VALID"); return 0
            manifest = json.loads(row["manifest_json_sanitized"])
            if args.action == "stage":
                if row["status"] != "ready": raise UpdateError("OPERATION_STATE")
                validate_package(incoming, os.environ.get("BACKUP_MANAGER_UPDATE_PUBLIC_KEY", str(DEFAULT_PUBLIC_KEY)))
                safe_extract(incoming, staging); print("STAGED"); return 0
            backup_root = Path(os.environ.get("BACKUP_MANAGER_UPDATE_BACKUP_ROOT", str(DEFAULT_BACKUP_ROOT)))
            app_root = Path(os.environ.get("BACKUP_MANAGER_HOME", str(ROOT)))
            if args.action == "rollback":
                if row["status"] not in {"failed", "success"} or not row["backup_path"]: raise UpdateError("ROLLBACK_NOT_ELIGIBLE")
                conn.execute("UPDATE update_operations SET status='rolling_back',updated_at=CURRENT_TIMESTAMP WHERE uuid=?", (args.operation,))
                systemctl("stop", manifest["services_to_restart"])
                restore_backup(DB_PATH, app_root, Path(row["backup_path"]))
                systemctl("start", manifest["services_to_restart"])
                conn.execute("UPDATE update_operations SET status='rolled_back',rollback_status='success',finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE uuid=?", (args.operation,))
                print("ROLLED_BACK"); return 0
            if row["status"] != "ready": raise UpdateError("OPERATION_STATE")
            verified = validate_package(incoming, os.environ.get("BACKUP_MANAGER_UPDATE_PUBLIC_KEY", str(DEFAULT_PUBLIC_KEY)))
            if verified.package_sha256 != row["package_sha256"]: raise UpdateError("PACKAGE_CHANGED")
            if staging.exists(): raise UpdateError("STAGING_EXISTS")
            safe_extract(incoming, staging)
            conn.execute("UPDATE update_operations SET status='applying',started_at=CURRENT_TIMESTAMP,staging_path=?,updated_at=CURRENT_TIMESTAMP WHERE uuid=?", (str(staging), args.operation))
            backup = create_backup(DB_PATH, app_root, backup_root, args.operation, [x["path"] for x in manifest["files"]])
            conn.execute("UPDATE update_operations SET backup_path=?,updated_at=CURRENT_TIMESTAMP WHERE uuid=?", (str(backup), args.operation))
            try:
                systemctl("stop", manifest["services_to_restart"])
                apply_staged(staging, app_root, manifest)
                run_migrations(DB_PATH, staging, manifest["migrations"])
                systemctl("start", manifest["services_to_restart"])
            except Exception:
                restore_backup(DB_PATH, app_root, backup)
                systemctl("start", manifest["services_to_restart"])
                raise
            conn.execute("UPDATE update_operations SET status='success',finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE uuid=?", (args.operation,))
            print("SUCCESS"); return 0
    except UpdateError as exc:
        try:
            with connect() as conn:
                conn.execute("UPDATE update_operations SET status='failed',error_code=?,error_message='Falha segura na atualização.',finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE uuid=?", (exc.code, args.operation))
        except Exception: pass
        print(f"INVALID {exc.code}"); return 1
    except Exception:
        try:
            with connect() as conn:
                conn.execute("UPDATE update_operations SET status='failed',error_code='APPLY_FAILED',error_message='Falha segura na atualização.',finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE uuid=?", (args.operation,))
        except Exception: pass
        print("INVALID APPLY_FAILED"); return 1


if __name__ == "__main__": raise SystemExit(main())
