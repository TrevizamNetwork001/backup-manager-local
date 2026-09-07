from __future__ import annotations

import re
from dataclasses import dataclass


class HuaweiOLTError(ValueError):
    pass


@dataclass(frozen=True)
class HuaweiFTPConfig:
    host: str
    username: str
    password: str
    config_filename: str
    data_filename: str


SAFE_VALUE_RE = re.compile(r"[A-Za-z0-9._@%+!/-]{1,180}")
HOST_RE = re.compile(r"[A-Za-z0-9.:-]{3,253}")


def backup_commands(config: HuaweiFTPConfig) -> tuple[str, ...]:
    host = config.host.strip()
    values = (config.config_filename, config.data_filename)
    if not HOST_RE.fullmatch(host):
        raise HuaweiOLTError("Host FTP inválido.")
    if any(not SAFE_VALUE_RE.fullmatch(value) for value in values):
        raise HuaweiOLTError("Parâmetro FTP inválido para a OLT Huawei.")
    config_filename, data_filename = values
    if not config_filename.lower().endswith(".cfg") or not data_filename.lower().endswith(".dat"):
        raise HuaweiOLTError("Os arquivos Huawei devem usar as extensões .cfg e .dat.")
    return (
        "enable",
        "config",
        "undo interactive",
        "undo smart",
        "scroll",
        f"backup configuration ftp {host} {config_filename}",
        f"backup data ftp {host} {data_filename}",
    )


def redact_commands(commands: tuple[str, ...], password: str) -> tuple[str, ...]:
    return tuple(command.replace(password, "********") for command in commands)
