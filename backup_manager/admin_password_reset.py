from __future__ import annotations

import getpass
import hashlib
import json
import os
import secrets
import sqlite3
import string
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .db import TEMP_ADMIN_PASSWORD, check_schema_version
from .security import hash_password, verify_password

CONFIRMATION = "RESETAR_SENHA_ADMIN"
SUSPICIOUS_MESSAGE = (
    "Banco suspeito ou recém-criado. Reset de senha cancelado para evitar operar no banco incorreto."
)
OPERATIONAL_TABLES = ("equipment", "backup_jobs", "backups", "ftp_accounts", "telegram_destinations")
REQUIRED_TABLES = {"users", "roles", "sessions", "audit_log", "schema_migrations", *OPERATIONAL_TABLES}


class AdminPasswordResetError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class DatabaseSummary:
    path: Path
    size: int
    schema_version: int
    users: int
    equipment: int
    jobs: int
    backups: int
    ftp_accounts: int
    telegram_destinations: int

    @property
    def operational_counts(self) -> dict[str, int]:
        return {
            "equipment": self.equipment,
            "backup_jobs": self.jobs,
            "backups": self.backups,
            "ftp_accounts": self.ftp_accounts,
            "telegram_destinations": self.telegram_destinations,
        }


@dataclass(frozen=True)
class ResetResult:
    summary: DatabaseSummary
    username: str
    user_id: int
    backup_path: Path | None
    sessions_invalidated: int
    dry_run: bool
    generated_password: str = ""


def _uri(path: Path, mode: str) -> str:
    return f"file:{quote(str(path))}?mode={mode}"


def _open_existing(path: Path, *, writable: bool = False) -> sqlite3.Connection:
    mode = "rw" if writable else "ro"
    try:
        conn = sqlite3.connect(_uri(path, mode), uri=True, timeout=15)
    except sqlite3.Error as exc:
        raise AdminPasswordResetError("DATABASE_OPEN_FAILED", "Não foi possível abrir o banco existente.") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    if not writable:
        conn.execute("PRAGMA query_only=ON")
    return conn


def _existing_file(path: Path) -> Path:
    target = path.expanduser().resolve(strict=False)
    if not target.exists():
        raise AdminPasswordResetError("DATABASE_MISSING", "Arquivo do banco não existe.")
    if target.is_dir() or not target.is_file():
        raise AdminPasswordResetError("DATABASE_NOT_FILE", "Caminho do banco não aponta para arquivo regular.")
    if target.stat().st_size == 0:
        raise AdminPasswordResetError("DATABASE_EMPTY", "Arquivo do banco está vazio.")
    return target


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _schema_version(conn: sqlite3.Connection) -> int:
    versions = []
    for row in conn.execute("SELECT version FROM schema_migrations"):
        prefix = str(row[0]).split("_", 1)[0]
        if prefix.isdigit():
            versions.append(int(prefix))
    return max(versions, default=0)


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def inspect_database(path: Path) -> tuple[DatabaseSummary, sqlite3.Connection]:
    target = _existing_file(path)
    conn = _open_existing(target)
    try:
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.Error as exc:
            raise AdminPasswordResetError("INTEGRITY_FAILED", "Falha no integrity_check do banco.") from exc
        if integrity != "ok":
            raise AdminPasswordResetError("INTEGRITY_FAILED", "Falha no integrity_check do banco.")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise AdminPasswordResetError("FOREIGN_KEY_FAILED", "Banco possui violações de integridade referencial.")
        status = check_schema_version(db_path=target)
        if not status.ready:
            raise AdminPasswordResetError(status.code, "Schema incompatível com esta versão do código.")
        tables = _tables(conn)
        missing = REQUIRED_TABLES - tables
        if missing:
            raise AdminPasswordResetError("SCHEMA_INVALID", "Banco não contém as tabelas obrigatórias.")
        summary = DatabaseSummary(
            target, target.stat().st_size, _schema_version(conn), _count(conn, "users"),
            _count(conn, "equipment"), _count(conn, "backup_jobs"), _count(conn, "backups"),
            _count(conn, "ftp_accounts"), _count(conn, "telegram_destinations"),
        )
        migration_rows = conn.execute(
            "SELECT COUNT(*),COUNT(DISTINCT applied_at) FROM schema_migrations"
        ).fetchone()
        operational_total = sum(summary.operational_counts.values())
        seed_only = summary.users <= 1 and operational_total == 0
        age_seconds = max(0.0, datetime.now().timestamp() - target.stat().st_ctime)
        newly_created_minimum = age_seconds < 15 * 60 and operational_total <= 2
        migrations_concentrated = migration_rows[0] > 1 and migration_rows[1] <= 1
        if seed_only or newly_created_minimum or migrations_concentrated:
            raise AdminPasswordResetError("DATABASE_SUSPICIOUS", SUSPICIOUS_MESSAGE)
        return summary, conn
    except Exception:
        conn.close()
        raise


def _select_admin(conn: sqlite3.Connection, *, username: str = "", user_id: int = 0):
    rows = conn.execute(
        """SELECT users.id,users.username,users.password_hash
           FROM users JOIN roles ON roles.id=users.role_id
           WHERE roles.can_admin=1 AND users.is_active=1 ORDER BY users.id"""
    ).fetchall()
    if username:
        selected = [row for row in rows if row["username"] == username]
    elif user_id:
        selected = [row for row in rows if row["id"] == user_id]
    elif len(rows) == 1:
        selected = rows
    elif len(rows) > 1:
        raise AdminPasswordResetError(
            "ADMIN_SELECTION_REQUIRED", "Existem vários administradores; informe --username ou --user-id."
        )
    else:
        selected = []
    if not selected:
        raise AdminPasswordResetError("ADMIN_NOT_FOUND", "Usuário administrativo selecionado não existe ou está inativo.")
    return selected[0]


def validate_password_policy(password: str) -> None:
    if not 12 <= len(password) <= 128:
        raise AdminPasswordResetError("PASSWORD_POLICY", "A senha deve possuir entre 12 e 128 caracteres.")
    checks = (
        any(char.islower() for char in password), any(char.isupper() for char in password),
        any(char.isdigit() for char in password), any(not char.isalnum() for char in password),
    )
    if not all(checks):
        raise AdminPasswordResetError(
            "PASSWORD_POLICY", "A senha deve conter maiúscula, minúscula, número e caractere especial."
        )


def generate_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "-_.!@#%"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        try:
            validate_password_policy(value)
            return value
        except AdminPasswordResetError:
            continue


def prompt_password() -> str:
    first = getpass.getpass("Nova senha administrativa: ")
    second = getpass.getpass("Confirme a nova senha: ")
    if first != second:
        raise AdminPasswordResetError("PASSWORD_MISMATCH", "As senhas informadas não coincidem.")
    validate_password_policy(first)
    return first


def _backup_database(source: sqlite3.Connection, path: Path) -> Path:
    root = path.parent / "password-reset-backups"
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    if root.is_symlink() or path.parent.resolve() not in root.resolve().parents:
        raise AdminPasswordResetError("BACKUP_PATH_INVALID", "Diretório de backup inseguro.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = root / f"{path.name}.pre-password-reset-{stamp}-{uuid.uuid4().hex}.bak"
    try:
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
        info = path.stat()
        os.chown(target, info.st_uid, info.st_gid)
        os.chmod(target, 0o600)
        checked = _open_existing(target)
        try:
            if checked.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise AdminPasswordResetError("BACKUP_INTEGRITY_FAILED", "Backup do banco não está íntegro.")
            if checked.execute("PRAGMA foreign_key_check").fetchone():
                raise AdminPasswordResetError("BACKUP_FOREIGN_KEY_FAILED", "Backup possui violações de FK.")
        finally:
            checked.close()
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _restore_validated_backup(backup_path: Path, target_path: Path) -> None:
    source = _open_existing(backup_path)
    target = _open_existing(target_path, writable=True)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def reset_admin_password(
    path: Path, *, confirmation: str = "", username: str = "", user_id: int = 0,
    generate: bool = False, password_reader=prompt_password,
) -> ResetResult:
    if username and user_id:
        raise AdminPasswordResetError("ADMIN_SELECTOR_CONFLICT", "Use somente --username ou --user-id.")
    summary, diagnostic = inspect_database(path)
    try:
        admin = _select_admin(diagnostic, username=username, user_id=user_id)
        selected_id, selected_username = int(admin["id"]), str(admin["username"])
    finally:
        diagnostic.close()
    if confirmation != CONFIRMATION:
        return ResetResult(summary, selected_username, selected_id, None, 0, True)
    password = generate_password() if generate else password_reader()
    validate_password_policy(password)
    conn = _open_existing(summary.path, writable=True)
    backup_path: Path | None = None
    generated = password if generate else ""
    before = summary.operational_counts
    old_hash = ""
    sessions = 0
    try:
        selected = _select_admin(conn, username=selected_username, user_id=selected_id)
        old_hash = selected["password_hash"]
        backup_path = _backup_database(conn, summary.path)
        new_hash = hash_password(password)
        fingerprint = hashlib.sha256(str(summary.path).encode("utf-8")).hexdigest()[:16]
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE users SET password_hash=?,must_change_password=1 WHERE id=? AND password_hash=?",
            (new_hash, selected_id, old_hash),
        )
        if cursor.rowcount != 1:
            raise AdminPasswordResetError("ADMIN_CHANGED", "Administrador foi alterado durante a operação.")
        sessions = conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (selected_id,)).fetchone()[0]
        conn.execute("DELETE FROM sessions WHERE user_id=?", (selected_id,))
        details = json.dumps({
            "origin": "cli", "result": "success", "user_id": selected_id,
            "username": selected_username, "database_fingerprint": fingerprint,
        }, separators=(",", ":"), sort_keys=True)
        conn.execute(
            """INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address)
               VALUES(?, 'auth.admin_password_reset', 'user', ?, ?, 'local-cli')""",
            (selected_id, str(selected_id), details),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    try:
        checked = _open_existing(summary.path)
        try:
            if checked.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise AdminPasswordResetError("POST_INTEGRITY_FAILED", "Falha de integridade após o reset.")
            if checked.execute("PRAGMA foreign_key_check").fetchone():
                raise AdminPasswordResetError("POST_FOREIGN_KEY_FAILED", "Violação de FK após o reset.")
            current = checked.execute("SELECT password_hash FROM users WHERE id=?", (selected_id,)).fetchone()
            if not current or current[0] == old_hash:
                raise AdminPasswordResetError("POST_USER_INVALID", "Reset não foi confirmado no usuário.")
            after = {table: _count(checked, table) for table in OPERATIONAL_TABLES}
            if after != before:
                raise AdminPasswordResetError("OPERATIONAL_COUNTS_CHANGED", "Contagens operacionais foram alteradas.")
        finally:
            checked.close()
    except Exception:
        if backup_path is not None:
            _restore_validated_backup(backup_path, summary.path)
        raise
    return ResetResult(summary, selected_username, selected_id, backup_path, sessions, False, generated)


def temp_password_message(path: Path) -> tuple[bool, str]:
    warning = (
        "Este comando não redefine a senha. Ele apenas mostra a senha inicial de instalações novas. "
        "Use reset-admin-password para recuperar o acesso."
    )
    try:
        target = _existing_file(path)
        conn = _open_existing(target)
        try:
            if "settings" not in _tables(conn):
                return False, warning
            setup = conn.execute("SELECT value FROM settings WHERE key='setup_complete'").fetchone()
            if setup and setup[0] == "1":
                return False, warning
            if "users" not in _tables(conn):
                return False, warning
            admin = conn.execute(
                "SELECT password_hash,must_change_password FROM users WHERE username='admin'"
            ).fetchone()
            if not admin or not admin[1] or not verify_password(TEMP_ADMIN_PASSWORD, admin[0]):
                return False, warning
        finally:
            conn.close()
    except AdminPasswordResetError:
        return False, warning
    return True, warning
