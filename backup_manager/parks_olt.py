from __future__ import annotations

import re
from dataclasses import dataclass


class ParksOLTError(ValueError):
    pass


@dataclass(frozen=True)
class ParksFTPConfig:
    host: str
    username: str
    password: str
    filename: str


HOST_RE = re.compile(r"[A-Za-z0-9.:-]{3,253}")
VALUE_RE = re.compile(r"[A-Za-z0-9._%+!-]{1,180}")


def _values(config: ParksFTPConfig) -> tuple[str, str, str, str]:
    values = (config.host.strip(), config.username.strip(), config.password, config.filename.strip())
    host, username, password, filename = values
    if not HOST_RE.fullmatch(host) or any(not VALUE_RE.fullmatch(value) for value in values[1:]):
        raise ParksOLTError("Parâmetro FTP inválido para a OLT Parks.")
    if not filename.lower().endswith(".bin"):
        raise ParksOLTError("O arquivo Parks deve usar a extensão .bin.")
    return values


def backup_100xx_200xx_command(config: ParksFTPConfig) -> str:
    host, username, password, filename = _values(config)
    return f"copy startup-config ftp://{host}/{filename} {username} {password}"


def backup_300xx_400xx_command(config: ParksFTPConfig) -> str:
    host, username, password, filename = _values(config)
    return f"copy startup-config ftp://{username}:{password}@{host}/{filename}"


def redact_command(command: str, password: str) -> str:
    return command.replace(password, "********")
