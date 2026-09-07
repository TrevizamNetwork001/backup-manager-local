from __future__ import annotations

import re
from dataclasses import dataclass


class IntelbrasOLTError(ValueError):
    pass


@dataclass(frozen=True)
class IntelbrasFTPConfig:
    host: str
    username: str
    password: str
    filename: str


SAFE_VALUE_RE = re.compile(r"[A-Za-z0-9._@%+!/-]{1,180}")
HOST_RE = re.compile(r"[A-Za-z0-9.:-]{3,253}")


def _values(config: IntelbrasFTPConfig) -> tuple[str, str, str, str]:
    host = config.host.strip()
    username = config.username.strip()
    password = config.password
    filename = config.filename.strip()
    if not HOST_RE.fullmatch(host):
        raise IntelbrasOLTError("Host FTP inválido.")
    if any(not SAFE_VALUE_RE.fullmatch(value) for value in (username, password, filename)):
        raise IntelbrasOLTError("Parâmetro FTP inválido para a OLT Intelbras.")
    if not filename.lower().endswith(".cfg"):
        raise IntelbrasOLTError("O arquivo Intelbras deve usar a extensão .cfg.")
    return host, username, password, filename


def gpon_backup_dialog(config: IntelbrasFTPConfig) -> tuple[tuple[str, str | None], ...]:
    """Return command and prompt responses for Intelbras 8820i/G08/G16."""
    host, username, password, filename = _values(config)
    return (
        (f"backup network ftp {host} filename {filename}", None),
        (username, r"User\s*:?[ ]*$"),
        (password, r"Password\s*:?[ ]*$"),
    )


def epon_backup_command(config: IntelbrasFTPConfig) -> str:
    """Return the single-line command used by Intelbras 4840 E."""
    host, username, password, filename = _values(config)
    return f"upload configuration ftp inet {host} {username} {password} {filename}"


def redacted_gpon_dialog(config: IntelbrasFTPConfig) -> tuple[tuple[str, str | None], ...]:
    dialog = gpon_backup_dialog(config)
    return (dialog[0], (config.username, dialog[1][1]), ("********", dialog[2][1]))


def redacted_epon_command(config: IntelbrasFTPConfig) -> str:
    return epon_backup_command(config).replace(config.password, "********")
