from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass


class FiberHomeError(RuntimeError):
    pass


@dataclass(frozen=True)
class FiberHomeFTPConfig:
    host: str
    port: int
    username: str
    password: str
    config_filename: str
    system_filename: str


def backup_commands(config: FiberHomeFTPConfig) -> tuple[str, ...]:
    if not re.fullmatch(r"[A-Za-z0-9.:-]{3,253}", config.host):
        raise FiberHomeError("Host FTP inválido.")
    if not 1 <= config.port <= 65535:
        raise FiberHomeError("Porta FTP inválida.")
    safe_values = (config.username, config.password, config.config_filename, config.system_filename)
    if any(not value or not re.fullmatch(r"[A-Za-z0-9._@%+!/-]{1,180}", value) for value in safe_values):
        raise FiberHomeError("Parâmetro FTP inválido para a OLT FiberHome.")
    return (
        "enable",
        f"ping {config.host}",
        f"upload ftp config {config.host} {config.port} {config.username} {config.password} {config.config_filename}",
        f"upload ftp system {config.host} {config.port} {config.username} {config.password} {config.system_filename}",
    )


def run_telnet_backup(*, olt_host: str, olt_port: int, olt_username: str, olt_password: str,
                      ftp: FiberHomeFTPConfig, timeout: int = 60, socket_factory=socket.create_connection) -> dict[str, object]:
    """Authenticate, trigger both exports and return only sanitized operation metadata."""
    connection = socket_factory((olt_host, olt_port), timeout=timeout)
    connection.settimeout(timeout)

    def receive() -> str:
        chunks: list[bytes] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = connection.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            chunks.append(chunk)
            text = b"".join(chunks).decode("iso-8859-1", errors="replace")
            if re.search(r"(?:Login|Username|Password|User|#|>)\s*:?[ ]*$", text, re.IGNORECASE):
                break
        return b"".join(chunks).decode("iso-8859-1", errors="replace")

    def send(value: str) -> str:
        connection.sendall((value + "\n").encode("iso-8859-1"))
        return receive()

    try:
        prompt = receive()
        if not re.search(r"(?:Login|Username)", prompt, re.IGNORECASE):
            raise FiberHomeError("A OLT não apresentou o prompt de login esperado.")
        prompt = send(olt_username)
        if "password" not in prompt.lower():
            raise FiberHomeError("A OLT não apresentou o prompt de senha esperado.")
        prompt = send(olt_password)
        if not re.search(r"(?:User|#|>)", prompt, re.IGNORECASE):
            raise FiberHomeError("A autenticação Telnet da OLT foi recusada.")
        responses = [send(command) for command in backup_commands(ftp)]
        if not all("success" in response.lower() for response in responses[-2:]):
            raise FiberHomeError("A OLT não confirmou o envio dos dois arquivos por FTP.")
        return {"ok": True, "config_filename": ftp.config_filename,
                "system_filename": ftp.system_filename, "artifacts": 2}
    finally:
        try:
            connection.close()
        except OSError:
            pass
