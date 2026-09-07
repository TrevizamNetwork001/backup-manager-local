#!/usr/bin/python3
"""Backup periódico e recuperação controlada do SQLite."""
import argparse, os, sqlite3, subprocess, uuid
from datetime import datetime, timezone
from pathlib import Path

DB=Path(os.environ.get("BACKUP_MANAGER_DB","/opt/backup-manager-local/data/backup_manager.sqlite3"))
BACKUPS=DB.parent/"recovery-backups"
SERVICES=("backup-manager-local.service","backup-manager-worker.timer","backup-manager-ftp-importer.timer","backup-manager-lifecycle.timer","backup-manager-cloud-sync.timer","backup-manager-telegram-backup.timer")

def valid(path):
    try:
        c=sqlite3.connect(f"file:{path}?mode=ro",uri=True)
        ok=c.execute("PRAGMA integrity_check").fetchone()[0]=="ok" and c.execute("PRAGMA foreign_key_check").fetchone() is None
        c.close(); return ok
    except (sqlite3.Error,OSError): return False

def backup():
    BACKUPS.mkdir(parents=True,exist_ok=True,mode=0o750)
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target=BACKUPS/f"backup_manager.sqlite3.auto-{stamp}-{uuid.uuid4().hex}.bak"
    src=sqlite3.connect(f"file:{DB}?mode=ro",uri=True); dst=sqlite3.connect(target)
    try: src.backup(dst)
    finally: dst.close(); src.close()
    if not valid(target): target.unlink(missing_ok=True); raise SystemExit("BACKUP_INVALID")
    target.chmod(0o640)
    keep=sorted(BACKUPS.glob("*.auto-*.bak"),reverse=True)
    for old in keep[14:]: old.unlink()
    print(f"BACKUP_OK {target}")

def recover():
    if valid(DB): backup(); print("DATABASE_OK"); return
    candidates=sorted(BACKUPS.glob("*.bak"),key=lambda p:p.stat().st_mtime,reverse=True)
    selected=next((p for p in candidates if valid(p)),None)
    if not selected: raise SystemExit("RECOVERY_NO_VALID_BACKUP")
    manage_systemd=os.environ.get("BACKUP_MANAGER_GUARD_NO_SYSTEMD")!="1"
    if manage_systemd:
        for service in SERVICES: subprocess.run(["systemctl","stop",service],check=False)
    evidence=DB.with_name(f"{DB.name}.corrupt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    os.replace(DB,evidence)
    src=sqlite3.connect(f"file:{selected}?mode=ro",uri=True); dst=sqlite3.connect(DB)
    try: src.backup(dst)
    finally: dst.close(); src.close()
    DB.chmod(0o640)
    if not valid(DB): os.replace(evidence,DB); raise SystemExit("RECOVERY_RESTORE_INVALID")
    if manage_systemd:
        for service in reversed(SERVICES): subprocess.run(["systemctl","start",service],check=False)
    print(f"RECOVERY_OK source={selected} evidence={evidence}")

if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--recover",action="store_true"); a=p.parse_args()
    recover() if a.recover else (backup() if valid(DB) else (_ for _ in ()).throw(SystemExit("DATABASE_CORRUPT")))
