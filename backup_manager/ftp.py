from __future__ import annotations

import ipaddress
import os
import re
import secrets
import string
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from .security import hash_password
from .storage import load_config, resolve_inside

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")
RESERVED_USERNAMES = {"root", "admin", "ftp", "anonymous", "backupftp"}
PERMISSION_MODES = {"upload_only", "upload_and_list", "read_write"}


class FTPAccountError(ValueError):
    pass


@dataclass(frozen=True)
class FTPPaths:
    root: Path
    incoming: Path
    processing: Path
    rejected: Path


def validate_username(value: str) -> str:
    username = value.strip()
    if not USERNAME_RE.fullmatch(username) or username in RESERVED_USERNAMES:
        raise FTPAccountError("Usuario FTP invalido; use 3 a 32 caracteres minusculos, numeros, hifen ou underscore.")
    return username


def validate_source_network(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        return str(ipaddress.ip_network(value, strict=False) if "/" in value else ipaddress.ip_address(value))
    except ValueError as exc:
        raise FTPAccountError("IP ou CIDR permitido invalido.") from exc


def validate_password(password: str, legacy: bool = False) -> None:
    minimum = 6 if legacy else 8
    maximum = 12 if legacy else 32
    if len(password) < minimum or len(password) > maximum:
        raise FTPAccountError(f"A senha deve ter entre {minimum} e {maximum} caracteres.")


def validate_upload_subdirectory(value: str) -> str:
    value = value.strip().strip("/")
    if not value:
        return ""
    if len(value) > 64 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise FTPAccountError("Use somente letras, números, _ ou - no nome da pasta.")
    return value


def generate_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "-_!@#%"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        try:
            validate_password(value)
            return value
        except FTPAccountError:
            pass


def account_paths(conn, account_uuid: str) -> FTPPaths:
    if not re.fullmatch(r"[0-9a-f-]{36}", account_uuid):
        raise FTPAccountError("UUID da conta invalido.")
    root = load_config(conn).storage_root / "ftp-incoming" / "accounts" / account_uuid
    return FTPPaths(root, root / "incoming", root / "processing", root / "rejected")


def ensure_account_directories(conn, account_uuid: str) -> FTPPaths:
    paths = account_paths(conn, account_uuid)
    for path in (paths.root, paths.incoming, paths.processing, paths.rejected):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o750)
    return paths


def create_account(conn, *, equipment_id: int | None, name: str, username: str, password: str,
                   account_type: str = "backup",
                   permission_mode: str = "upload_only", allowed_source: str = "",
                   quota_bytes: int | None = None, max_files: int | None = None,
                   is_active: bool = True, notes: str = "", created_by_user_id: int | None = None,
                   upload_subdirectory: str = ""):
    username = validate_username(username)
    if not name.strip():
        raise FTPAccountError("Nome da conta e obrigatorio.")
    validate_password(password)
    upload_subdirectory = validate_upload_subdirectory(upload_subdirectory)
    source = validate_source_network(allowed_source)
    if permission_mode not in PERMISSION_MODES or permission_mode == "read_write":
        raise FTPAccountError("Modo de permissao indisponivel nesta fase.")
    if account_type not in {"backup", "file_server"}:
        raise FTPAccountError("Tipo de conta FTP invalido.")
    if account_type == "backup":
        equipment = conn.execute("SELECT id FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
        if not equipment:
            raise FTPAccountError("Equipamento invalido.")
    elif equipment_id is not None:
        raise FTPAccountError("Conta de arquivos nao deve ser vinculada a equipamento.")
    account_uuid = str(uuid.uuid4())
    relative = str(Path("ftp-incoming") / "accounts" / account_uuid / "incoming")
    try:
        cursor = conn.execute(
            """INSERT INTO ftp_accounts
               (uuid, equipment_id, account_type, name, username, password_hash_or_secret_reference,
                home_relative_path, permission_mode, allowed_source_ip, allowed_source_cidr,
                control_port, is_active, quota_bytes, max_files, created_by_user_id, notes, upload_subdirectory)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       COALESCE((SELECT CAST(value AS INTEGER) FROM settings WHERE key='ftp_control_port'),21),
                       ?, ?, ?, ?, ?, ?)""",
            (account_uuid, equipment_id, account_type, name.strip()[:120], username, hash_password(password), relative,
             permission_mode, source if source and "/" not in source else None,
             source if "/" in source else None, int(is_active), quota_bytes, max_files,
             created_by_user_id, notes.strip()[:1000], upload_subdirectory),
        )
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise FTPAccountError("Usuario ou equipamento ja possui conta FTP ativa.") from exc
        raise
    ensure_account_directories(conn, account_uuid)
    return conn.execute("SELECT * FROM ftp_accounts WHERE id = ?", (cursor.lastrowid,)).fetchone()


def run_helper(action: str, account_id: int, password: str | None = None, timeout: int = 20) -> tuple[bool, str]:
    if action not in {"create-or-update-account", "disable-account", "check-account"} or account_id < 1:
        raise FTPAccountError("Operacao administrativa invalida.")
    helper = os.environ.get("BACKUP_MANAGER_FTP_HELPER", "/usr/local/sbin/backup-manager-ftp-admin")
    command = [helper, action, "--account-id", str(account_id)]
    if os.geteuid() != 0:
        command = ["sudo", "-n", *command]
    payload = ((password + "\n" + password + "\n") if password is not None else "")
    try:
        result = subprocess.run(command, input=payload, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False, "helper_indisponivel"
    safe = (result.stdout or result.stderr or "").strip().splitlines()[-1:] or ["sem_detalhes"]
    return result.returncode == 0, re.sub(r"[^A-Za-z0-9_.:-]", "_", safe[0])[:120]


def safe_account_home(conn, row) -> Path:
    config = load_config(conn)
    expected = Path("ftp-incoming") / "accounts" / row["uuid"] / "incoming"
    if Path(row["home_relative_path"]) != expected:
        raise FTPAccountError("Diretorio da conta inconsistente.")
    return resolve_inside(config.storage_root, str(expected))
