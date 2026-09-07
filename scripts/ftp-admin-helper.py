#!/usr/bin/python3
"""Root-only allowlisted bridge between Backup Manager and pure-pw."""
from __future__ import annotations

import argparse
import os
import re
import pwd
import sqlite3
import subprocess
import sys
from pathlib import Path

DB = Path(os.environ.get("BACKUP_MANAGER_DB", "/opt/backup-manager-local/data/backup_manager.sqlite3"))
PURE_PW = "/usr/bin/pure-pw"
PUREDB = "/etc/pure-ftpd/pureftpd.pdb"
PASSWD = "/etc/pure-ftpd/pureftpd.passwd"
UUID_RE = re.compile(r"^[0-9a-f-]{36}$")
USER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")


def fail(message: str, code: int = 2) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def run(args: list[str], password: str = "") -> None:
    result = subprocess.run(args, input=password, text=True, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, timeout=15, check=False)
    if result.returncode:
        fail("pure_pw_failed", 1)


def account(account_id: int):
    if not DB.is_file():
        fail("database_missing")
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT * FROM ftp_accounts WHERE id=?", (account_id,)).fetchone()
    connection.close()
    if not row or not UUID_RE.fullmatch(row["uuid"]) or not USER_RE.fullmatch(row["username"]):
        fail("account_invalid")
    expected = f"ftp-incoming/accounts/{row['uuid']}/incoming"
    if row["home_relative_path"] != expected:
        fail("home_invalid")
    home = Path("/var/lib/backup-manager-local") / expected
    if not home.is_dir() or home.is_symlink():
        fail("home_missing")
    identity = pwd.getpwnam("backupftp")
    os.chown(home, identity.pw_uid, identity.pw_gid)
    home.chmod(0o750)
    upload_subdirectory = row["upload_subdirectory"] if "upload_subdirectory" in row.keys() else ""
    if upload_subdirectory:
        destination = home / upload_subdirectory
        destination.mkdir(mode=0o750, exist_ok=True)
        os.chown(destination, identity.pw_uid, identity.pw_gid)
        destination.chmod(0o750)
    for sibling in (home.parent / "processing", home.parent / "rejected"):
        if sibling.is_dir() and not sibling.is_symlink():
            os.chown(sibling, 0, identity.pw_gid)
            sibling.chmod(0o750)
    return row, home


def rebuild() -> None:
    run([PURE_PW, "mkdb", PUREDB, "-f", PASSWD])


def main() -> None:
    if os.geteuid() != 0:
        fail("root_required")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("action", choices=["create-or-update-account", "disable-account", "check-account"])
    parser.add_argument("--account-id", type=int, required=True)
    args = parser.parse_args()
    if args.account_id < 1:
        fail("account_id_invalid")
    row, home = account(args.account_id)
    exists = subprocess.run([PURE_PW, "show", row["username"], "-f", PASSWD], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=10, check=False).returncode == 0
    if args.action == "check-account":
        if not exists:
            fail("puredb_account_missing", 1)
        print("account_ok")
        return
    if args.action == "disable-account" or not row["is_active"] or row["deleted_at"]:
        if exists:
            run([PURE_PW, "userdel", row["username"], "-f", PASSWD])
            rebuild()
        print("account_disabled")
        return
    password = sys.stdin.readline(512).rstrip("\r\n")
    confirmation = sys.stdin.readline(512).rstrip("\r\n")
    if len(password) < 8 or password != confirmation:
        fail("password_invalid")
    if exists:
        run([PURE_PW, "passwd", row["username"], "-f", PASSWD], password + "\n" + password + "\n")
        run([PURE_PW, "usermod", row["username"], "-u", "backupftp", "-g", "backupftp", "-d", str(home), "-f", PASSWD])
    else:
        run([PURE_PW, "useradd", row["username"], "-u", "backupftp", "-g", "backupftp", "-d", str(home), "-f", PASSWD],
            password + "\n" + password + "\n")
    rebuild()
    print("account_synced")


if __name__ == "__main__":
    main()
