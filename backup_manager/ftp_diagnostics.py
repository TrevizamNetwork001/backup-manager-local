from __future__ import annotations

import shutil
import socket
import subprocess
from collections.abc import Callable

from .ftp import run_helper
from .storage import load_config


def ftp_statistics(conn) -> dict:
    return {
        "accounts_total": conn.execute(
            "SELECT COUNT(*) FROM ftp_accounts WHERE deleted_at IS NULL"
        ).fetchone()[0],
        "accounts_active": conn.execute(
            "SELECT COUNT(*) FROM ftp_accounts WHERE is_active=1 AND deleted_at IS NULL"
        ).fetchone()[0],
        "uploads_detected": conn.execute("SELECT COUNT(*) FROM ftp_received_files").fetchone()[0],
        "imported": conn.execute(
            "SELECT COUNT(*) FROM ftp_received_files WHERE status='imported'"
        ).fetchone()[0],
        "rejected": conn.execute(
            "SELECT COUNT(*) FROM ftp_received_files WHERE status='rejected'"
        ).fetchone()[0],
        "failed": conn.execute(
            "SELECT COUNT(*) FROM ftp_received_files WHERE status='failed'"
        ).fetchone()[0],
        "last_upload": conn.execute("SELECT MAX(detected_at) FROM ftp_received_files").fetchone()[0] or "-",
    }


def check_account_directories(conn, *, fix: bool = False) -> list[dict]:
    config = load_config(conn)
    rows = conn.execute(
        "SELECT uuid,username FROM ftp_accounts WHERE deleted_at IS NULL"
    ).fetchall()
    results = []
    for row in rows:
        root = config.storage_root / "ftp-incoming" / "accounts" / row["uuid"]
        for name in ("incoming", "processing", "rejected"):
            path = root / name
            status = "ok"
            if not path.is_dir():
                status = "missing"
                if fix:
                    path.mkdir(parents=True, exist_ok=True)
                    path.chmod(0o750)
            elif path.stat().st_mode & 0o002:
                status = "bad_mode"
                if fix:
                    path.chmod(0o750)
            results.append({"username": row["username"], "directory": name,
                            "path": str(path), "status": status})
    return results


def reconcile_accounts(conn, *, pure_users: set[str] | None = None) -> dict:
    config = load_config(conn)
    rows = conn.execute(
        "SELECT uuid,username,home_relative_path,is_active FROM ftp_accounts WHERE deleted_at IS NULL"
    ).fetchall()
    warning = ""
    if pure_users is None:
        try:
            output = subprocess.run(
                ["pure-pw", "list"], capture_output=True, text=True, timeout=10, check=False
            ).stdout
            pure_users = {line.split("\t", 1)[0].strip() for line in output.splitlines() if line.strip()}
        except (OSError, subprocess.TimeoutExpired):
            pure_users = set()
            warning = "pure-pw indisponivel"
    accounts = []
    for row in rows:
        issues = []
        root = config.storage_root / "ftp-incoming" / "accounts" / row["uuid"]
        if row["username"] not in pure_users:
            issues.append("ausente no PureDB")
        if not root.is_dir():
            issues.append("diretorio ausente")
        expected = f"ftp-incoming/accounts/{row['uuid']}/incoming"
        if row["home_relative_path"] != expected:
            issues.append("home divergente")
        accounts.append({"username": row["username"], "issues": issues})
    return {"warning": warning, "accounts": accounts,
            "problems": sum(bool(item["issues"]) for item in accounts)}


def check_ftp_config(conn, *, executable_lookup: Callable[[str], str | None] = shutil.which,
                     port_probe: Callable[[int], bool] | None = None) -> dict:
    config = load_config(conn)
    enabled = conn.execute("SELECT value FROM settings WHERE key='ftp_enabled'").fetchone()[0] == "1"
    port = int(conn.execute("SELECT value FROM settings WHERE key='ftp_control_port'").fetchone()[0])
    problems = []
    incoming = config.storage_root / "ftp-incoming" / "accounts"
    if enabled and not incoming.is_dir():
        problems.append("ftp-incoming ausente")
    if enabled and not executable_lookup("pure-pw"):
        problems.append("pure-pw ausente")
    if enabled:
        if port_probe is None:
            sock = socket.socket()
            sock.settimeout(.25)
            try:
                listening = sock.connect_ex(("127.0.0.1", port)) == 0
            finally:
                sock.close()
        else:
            listening = port_probe(port)
        if not listening:
            problems.append("porta de controle nao esta ouvindo")
    conn.execute(
        """INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address)
           VALUES(NULL,'ftp.config_checked','ftp','','problems=' || ?, '')""",
        (len(problems),),
    )
    return {"enabled": enabled, "port": port, "problems": problems}


def sync_account(conn, account_id: int, *, helper=run_helper) -> dict:
    ok, detail = helper("check-account", account_id)
    conn.execute(
        """UPDATE ftp_accounts SET sync_status=?,sync_error=?,updated_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        ("synced" if ok else "failed", "" if ok else detail, account_id),
    )
    return {"account_id": account_id, "ok": ok, "detail": detail}


def sync_all_accounts(conn, *, helper=run_helper) -> dict:
    ids = [row[0] for row in conn.execute(
        "SELECT id FROM ftp_accounts WHERE deleted_at IS NULL"
    )]
    results = [sync_account(conn, account_id, helper=helper) for account_id in ids]
    return {"accounts": len(results), "failures": sum(not item["ok"] for item in results),
            "results": results}
