from __future__ import annotations

import re
from dataclasses import dataclass


class CDataOLTError(ValueError):
    pass


@dataclass(frozen=True)
class CDataFTPConfig:
    host: str
    username: str
    password: str
    filename: str
    file_format: str = "gz"


HOST_RE = re.compile(r"[A-Za-z0-9.:-]{3,253}")
VALUE_RE = re.compile(r"[A-Za-z0-9._@%+!/-]{1,180}")


def backup_command(config: CDataFTPConfig) -> str:
    host = config.host.strip()
    username = config.username.strip()
    filename = config.filename.strip()
    file_format = config.file_format.strip().lower()
    if not HOST_RE.fullmatch(host):
        raise CDataOLTError("Host FTP inválido.")
    if any(not VALUE_RE.fullmatch(value) for value in (username, config.password, filename)):
        raise CDataOLTError("Parâmetro FTP inválido para a OLT C-DATA.")
    if file_format not in {"gz", "txt"}:
        raise CDataOLTError("O formato C-DATA deve ser gz ou txt.")
    return f"backup save-config format {file_format} ftp {host} {username} {config.password} {filename}"


def redact_command(command: str, password: str) -> str:
    return command.replace(password, "********")
