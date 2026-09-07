from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path


REMOTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
PROVIDERS = {"drive": "Google Drive", "s3": "S3 compatível", "b2": "Backblaze B2", "sftp": "SFTP", "onedrive": "Microsoft OneDrive", "dropbox": "Dropbox"}
AUTOMATIC_ANSWERS = {
    "drive": {
        # The panel is dedicated to backup uploads. Limit OAuth access to files
        # created by rclone and keep server-only technical choices out of the UI.
        "scope": "drive.file",
        "service_account_file": "",
        "config_fs_advanced": "false",
        "config_is_local": "false",
    },
}
_WIZARDS: dict[str, dict] = {}
CONFIG_PATH = Path(os.environ.get("BACKUP_MANAGER_RCLONE_CONFIG", "/etc/backup-manager-local/rclone.conf"))


class RcloneError(RuntimeError):
    def __init__(self, code: str, safe_message: str, *, transient: bool = True):
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.transient = transient


@dataclass(frozen=True)
class RcloneUpload:
    remote_path: str


def binary() -> str:
    executable = shutil.which("rclone")
    if not executable:
        raise RcloneError("RCLONE_NOT_INSTALLED", "rclone não está instalado no servidor.", transient=False)
    return executable


def normalize_remote(value: str) -> str:
    remote = (value or "").strip().removesuffix(":")
    if not REMOTE_RE.fullmatch(remote):
        raise ValueError("Remote rclone inválido.")
    return remote


def normalize_path(value: str) -> str:
    parts = []
    for part in (value or "").strip().strip("/").split("/"):
        if not part:
            continue
        if part in {".", ".."} or any(char in part for char in "\r\n\0:"):
            raise ValueError("Pasta remota inválida.")
        parts.append(part)
    return "/".join(parts)


def _folder_slug(value: str, fallback: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9_-]+", "-", ascii_name).strip("-_") or fallback


def organized_path(base_path: str, group: str, equipment: str, received_at: str, *, system_name: str = "") -> str:
    """Build the remote folder, using the installation name for new layouts."""
    root = _folder_slug(system_name, "BackupManager") if system_name else normalize_path(base_path)
    equipment_slug = _folder_slug(equipment, "Equipamento")
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", received_at or "")
    if not match:
        raise ValueError("Data do backup inválida para organização remota.")
    date_folder = f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
    if system_name:
        return normalize_path("/".join((root, equipment_slug, date_folder)))
    group_slug = _folder_slug(group, "SEM-GRUPO")
    return normalize_path("/".join(part for part in (root, group_slug, equipment_slug, date_folder) if part))


def destination(remote: str, base_path: str, filename: str = "") -> str:
    safe_remote = normalize_remote(remote)
    safe_path = normalize_path(base_path)
    safe_name = Path(filename).name if filename else ""
    if safe_name in {"", ".", ".."} and filename:
        raise ValueError("Nome remoto inválido.")
    joined = "/".join(part for part in (safe_path, safe_name) if part)
    return f"{safe_remote}:{joined}"


def _run(arguments: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    command = [binary(), "--config", str(CONFIG_PATH), *arguments]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, timeout=timeout, check=False,
                                env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"})
    except subprocess.TimeoutExpired as exc:
        raise RcloneError("RCLONE_TIMEOUT", "O destino rclone não respondeu no tempo esperado.") from exc
    except OSError as exc:
        raise RcloneError("RCLONE_EXEC_FAILED", "Não foi possível executar o rclone.") from exc
    if result.returncode:
        raise RcloneError("RCLONE_COMMAND_FAILED", "O rclone recusou a operação; verifique o remote e as credenciais.")
    return result


def _config_protocol(arguments: list[str]) -> dict:
    result = _run(["config", *arguments, "--non-interactive", "--all"], timeout=90)
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RcloneError("RCLONE_CONFIG_PROTOCOL", "O rclone retornou uma resposta de configuração inválida.", transient=False) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("State", ""), str):
        raise RcloneError("RCLONE_CONFIG_PROTOCOL", "O rclone não iniciou o assistente de configuração.", transient=False)
    return payload


def begin_remote(remote: str, provider: str) -> dict:
    name = normalize_remote(remote)
    backend = (provider or "").strip().lower()
    if backend not in PROVIDERS:
        raise ValueError("Provedor rclone não permitido pelo assistente.")
    if name in list_remotes():
        raise ValueError("Já existe um remote com esse nome.")
    payload = _config_protocol(["create", name, backend])
    option = payload.get("Option") or {}
    _WIZARDS[name] = {"provider": backend, "answers": {}, "option": str(option.get("Name", ""))}
    return payload


def _continue_remote_once(name: str, state: str, result: str) -> dict:
    safe_state = (state or "").strip()
    if not safe_state or len(safe_state) > 500 or any(char in safe_state for char in "\r\n\0"):
        raise ValueError("Estado do assistente inválido.")
    if len(result or "") > 8192 or "\0" in (result or ""):
        raise ValueError("Resposta do assistente excede o limite permitido.")
    if name not in list_remotes():
        raise ValueError("Remote do assistente não encontrado.")
    wizard = _WIZARDS.get(name)
    if not wizard or not wizard.get("option"):
        raise ValueError("A sessão visual expirou. Exclua o remote incompleto e reinicie a configuração.")
    wizard["answers"][wizard["option"]] = result or ""
    defaults = [value for item in wizard["answers"].items() for value in item]
    payload = _config_protocol(["update", name, *defaults, "--continue", "--state", safe_state, "--result", result or ""])
    option = payload.get("Option") or {}
    wizard["option"] = str(option.get("Name", ""))
    if not payload.get("State"):
        _WIZARDS.pop(name, None)
    return payload


def _advance_automatic_options(name: str, payload: dict) -> dict:
    for _ in range(10):
        wizard = _WIZARDS.get(name) or {}
        option = payload.get("Option") or {}
        option_name = str(option.get("Name", ""))
        automatic = AUTOMATIC_ANSWERS.get(str(wizard.get("provider", "")), {})
        if not payload.get("State") or option_name not in automatic:
            return payload
        payload = _continue_remote_once(name, str(payload["State"]), automatic[option_name])
    raise RcloneError("RCLONE_CONFIG_PROTOCOL", "O assistente excedeu o limite de etapas automáticas.", transient=False)


def continue_remote(remote: str, state: str, result: str) -> dict:
    name = normalize_remote(remote)
    return _advance_automatic_options(name, _continue_remote_once(name, state, result))


def cancel_remote(remote: str) -> bool:
    """Remove only a remote that belongs to an active visual wizard."""
    name = normalize_remote(remote)
    if name not in _WIZARDS:
        return False
    if name in list_remotes():
        _run(["config", "delete", name], timeout=20)
    _WIZARDS.pop(name, None)
    return True


def create_drive_remote(remote: str, client_id: str, client_secret: str, token: str, *, replace: bool = False) -> None:
    name = normalize_remote(remote)
    if name in list_remotes():
        if not replace:
            raise ValueError("Já existe um remote com esse nome.")
        _run(["config", "delete", name], timeout=20)
    _run(["config", "create", name, "drive", "client_id", client_id, "client_secret", client_secret,
          "scope", "drive.file", "token", token, "--non-interactive"], timeout=45)


def list_remotes() -> list[str]:
    result = _run(["listremotes"], timeout=20)
    return sorted(line.strip().removesuffix(":") for line in result.stdout.splitlines() if line.strip())


def test_remote(remote: str, base_path: str) -> None:
    name = normalize_remote(remote)
    if name not in list_remotes():
        raise RcloneError("RCLONE_REMOTE_NOT_FOUND", "Remote rclone não encontrado na configuração.", transient=False)
    target = destination(name, base_path)
    _run(["mkdir", target], timeout=45)
    _run(["lsjson", target, "--max-depth", "1", "--stat"], timeout=45)


def upload(source: Path, remote: str, base_path: str, filename: str, *, bandwidth_kbps: int | None = None) -> RcloneUpload:
    target = destination(remote, base_path, filename)
    arguments = ["copyto", str(source), target, "--immutable", "--transfers", "1", "--checkers", "2",
                 "--retries", "1", "--low-level-retries", "2", "--stats", "0"]
    if bandwidth_kbps:
        arguments.extend(["--bwlimit", f"{int(bandwidth_kbps)}k"])
    _run(arguments, timeout=int(os.environ.get("BACKUP_MANAGER_RCLONE_TIMEOUT", "3600")))
    return RcloneUpload(target)
