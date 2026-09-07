from __future__ import annotations

import re
from dataclasses import dataclass


class ZTEOLTError(ValueError):
    pass


@dataclass(frozen=True)
class ZTEFTPConfig:
    host: str
    path: str
    username: str
    password: str


SAFE_VALUE_RE = re.compile(r"[A-Za-z0-9._@%+!/-]{1,180}")
HOST_RE = re.compile(r"[A-Za-z0-9.:-]{3,253}")


def _values(config: ZTEFTPConfig) -> tuple[str, str, str, str]:
    host = config.host.strip()
    path = "/" + config.path.strip().strip("/") if config.path.strip().strip("/") else "/"
    username = config.username.strip()
    password = config.password
    if not HOST_RE.fullmatch(host):
        raise ZTEOLTError("Host FTP inválido.")
    if any(not SAFE_VALUE_RE.fullmatch(value) for value in (path, username, password)):
        raise ZTEOLTError("Parâmetro FTP inválido para a OLT ZTE.")
    return host, path, username, password


def manual_test_commands(config: ZTEFTPConfig, filename: str = "backup_teste.dat") -> tuple[str, ...]:
    host, path, username, password = _values(config)
    if not SAFE_VALUE_RE.fullmatch(filename):
        raise ZTEOLTError("Nome do arquivo de teste inválido.")
    return (
        "enable",
        "write",
        f"upload cfg startrun.dat destfilename {filename} ftp ipaddress {host} "
        f"path {path} user {username} password {password}",
    )


def automatic_backup_commands(config: ZTEFTPConfig) -> tuple[str, ...]:
    host, path, username, password = _values(config)
    return (
        "enable",
        "write",
        "configure terminal",
        f"file-server auto-backup all server-index 1 ftp ipaddress {host} "
        f"path {path} user {username} password {password}",
        "auto-backup condition cfg-changed hold-off-time 1 max-hold-off-time 2",
        "exit",
        "write",
        "show file-server auto-backup",
    )


def redact_commands(commands: tuple[str, ...], password: str) -> tuple[str, ...]:
    return tuple(command.replace(password, "********") for command in commands)
