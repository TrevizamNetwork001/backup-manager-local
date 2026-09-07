from __future__ import annotations

import re
import socket
import time
import urllib.parse
from dataclasses import dataclass


class VSOLOLTError(ValueError):
    pass


@dataclass(frozen=True)
class VSOLFTPConfig:
    host: str
    port: int
    username: str
    password: str
    filename: str


SAFE_VALUE_RE = re.compile(r"[A-Za-z0-9._@%+!/-]{1,180}")
HOST_RE = re.compile(r"[A-Za-z0-9.:-]{3,253}")
TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


def _values(config: VSOLFTPConfig) -> tuple[str, int, str, str, str]:
    host = config.host.strip()
    username = config.username.strip()
    password = config.password
    filename = config.filename.strip()
    if not HOST_RE.fullmatch(host):
        raise VSOLOLTError("Host FTP inválido.")
    if not 1 <= config.port <= 65535:
        raise VSOLOLTError("Porta FTP inválida.")
    if (not username or not password or len(username) > 180 or len(password) > 180 or
            any(ord(char) < 33 or ord(char) == 127 for char in username + password) or
            not SAFE_VALUE_RE.fullmatch(filename)):
        raise VSOLOLTError("Parâmetro FTP inválido para a OLT VSOL.")
    if not filename.lower().endswith(".config"):
        raise VSOLOLTError("O arquivo VSOL deve usar a extensão .config.")
    return host, config.port, username, password, filename


def manual_backup_commands(config: VSOLFTPConfig) -> tuple[str, ...]:
    host, port, username, password, filename = _values(config)
    authority = host if port == 21 else f"{host}:{port}"
    username = urllib.parse.quote(username, safe="")
    password = urllib.parse.quote(password, safe="")
    return (
        "write",
        f"copy startup-config ftp://{username}:{password}@{authority}/{filename}",
    )


def daily_schedule_commands(config: VSOLFTPConfig, at: str = "03:00",
                            task_name: str = "backup_diario") -> tuple[str, ...]:
    host, port, username, password, filename = _values(config)
    if not TIME_RE.fullmatch(at) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", task_name):
        raise VSOLOLTError("Horário ou nome da tarefa VSOL inválido.")
    authority = host if port == 21 else f"{host}:{port}"
    username = urllib.parse.quote(username, safe="")
    password = urllib.parse.quote(password, safe="")
    copy_command = f"copy startup-config ftp://{username}:{password}@{authority}/{filename}"
    return (
        f"cron task {task_name}",
        f"schedule daily {at}",
        f'action "{copy_command}"',
        "exit",
        "write",
    )


def redact_commands(commands: tuple[str, ...], password: str) -> tuple[str, ...]:
    encoded = urllib.parse.quote(password, safe="")
    return tuple(command.replace(password, "********").replace(encoded, "********") for command in commands)


def _safe_cli_detail(response: str) -> str:
    text = re.sub(r"ftp://\S+", "ftp://[credenciais removidas]", response, flags=re.I)
    lines = []
    for line in text.replace("\r", "").split("\n"):
        value = line.strip()
        if not value or value in {"#", ">"} or re.fullmatch(r"\S+[>#]", value):
            continue
        if value.casefold() in {"write", "copy startup-config"}:
            continue
        lines.append(value)
    return " ".join(lines)[-240:]


def run_telnet_backup(*, olt_host: str, olt_port: int, olt_username: str, olt_password: str,
                      ftp: VSOLFTPConfig, timeout: int = 60,
                      socket_factory=socket.create_connection) -> dict[str, object]:
    """Authenticate on a V1600GT and trigger its startup-config FTP export."""
    connection = socket_factory((olt_host, olt_port), timeout=timeout)
    connection.settimeout(0.5)

    def receive() -> str:
        chunks: list[bytes] = []
        deadline = time.monotonic() + timeout
        quiet_at: float | None = None
        while time.monotonic() < deadline:
            try:
                chunk = connection.recv(65535)
            except socket.timeout:
                if quiet_at is not None and time.monotonic() >= quiet_at:
                    break
                continue
            if not chunk:
                break
            chunks.append(chunk)
            text = b"".join(chunks).decode("iso-8859-1", errors="replace")
            quiet_at = time.monotonic() + 0.35
            if re.search(r"(?:login|user\s*name|password)\s*:?\s*$|[>#]\s*$", text, re.I):
                break
        return b"".join(chunks).decode("iso-8859-1", errors="replace")

    def send(value: str) -> str:
        connection.sendall((value + "\r\n").encode("iso-8859-1"))
        return receive()

    try:
        response = receive()
        if not re.search(r"(?:login|user\s*name)", response, re.I):
            raise VSOLOLTError("A OLT não apresentou o login Telnet.")
        response = send(olt_username)
        if "password" not in response.casefold():
            raise VSOLOLTError("A OLT não solicitou a senha Telnet.")
        response = send(olt_password)
        if not re.search(r"[>#]\s*$", response):
            raise VSOLOLTError("A autenticação Telnet da OLT foi recusada.")
        if not re.search(r"#\s*$", response):
            response = send("enable")
            if "password" in response.casefold():
                response = send(olt_password)
            if not re.search(r"#\s*$", response):
                raise VSOLOLTError("A OLT não liberou o modo privilegiado.")
        for step, command in zip(("salvar a configuração atual", "enviar a startup-config por FTP"),
                                 manual_backup_commands(ftp)):
            response = send(command)
            lowered = response.casefold()
            if any(marker in lowered for marker in
                   ("invalid input", "unknown command", "command error", "authentication failed", "failed")):
                detail = _safe_cli_detail(response)
                suffix = f" Resposta: {detail}" if detail else ""
                raise VSOLOLTError(f"A OLT recusou a etapa de {step}.{suffix}")
        return {"ok": True, "filename": ftp.filename, "artifacts": 1}
    finally:
        try:
            connection.close()
        except OSError:
            pass


def collect_running_config(*, host: str, port: int, username: str, password: str,
                           timeout: int = 60, socket_factory=socket.create_connection) -> str:
    """Collect a VSOL running configuration through its native Telnet CLI."""
    connection = socket_factory((host, port), timeout=timeout)
    connection.settimeout(0.5)

    def clean(data: bytes) -> str:
        # Remove the common three-byte Telnet negotiation sequences and ANSI controls.
        data = re.sub(rb"\xff[\xfb-\xfe].", b"", data)
        text = data.decode("iso-8859-1", errors="replace")
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        return text.replace("\x08", "")

    def receive(*, prompt: bool = True) -> str:
        chunks: list[bytes] = []
        deadline = time.monotonic() + timeout
        quiet_deadline: float | None = None
        handled_pagers = 0
        while time.monotonic() < deadline:
            try:
                chunk = connection.recv(65535)
            except socket.timeout:
                if quiet_deadline is not None and time.monotonic() >= quiet_deadline:
                    break
                continue
            if not chunk:
                break
            chunks.append(chunk)
            text = clean(b"".join(chunks))
            pager_count = text.count("--More--") + text.count("---- More ----")
            if pager_count > handled_pagers:
                connection.sendall(b" " * (pager_count - handled_pagers))
                handled_pagers = pager_count
                quiet_deadline = None
                continue
            quiet_deadline = time.monotonic() + 0.35
            if prompt and re.search(r"(?:login|user\s*name|password)\s*:?\s*$|[>#]\s*$", text, re.I):
                break
        return clean(b"".join(chunks))

    def send(value: str) -> str:
        connection.sendall((value + "\r\n").encode("iso-8859-1"))
        return receive()

    try:
        response = receive()
        if not re.search(r"(?:login|user\s*name)", response, re.I):
            raise VSOLOLTError("A OLT não apresentou o login Telnet.")
        response = send(username)
        if "password" not in response.casefold():
            raise VSOLOLTError("A OLT não solicitou a senha Telnet.")
        response = send(password)
        if not re.search(r">\s*$|#\s*$", response):
            raise VSOLOLTError("A autenticação Telnet da OLT foi recusada.")
        if not re.search(r"#\s*$", response):
            response = send("enable")
            if "password" in response.casefold():
                response = send(password)
            if not re.search(r"#\s*$", response):
                raise VSOLOLTError("A OLT não liberou o modo privilegiado.")
        output = send("show running-config")
        lowered = output.casefold()
        if any(marker in lowered for marker in ("unknown command", "invalid input", "command error")):
            raise VSOLOLTError("A OLT recusou o comando de leitura da configuração.")
        # Remove command echo, prompts and pager labels without exposing credentials.
        lines = [line.rstrip() for line in output.replace("\r", "").split("\n")]
        lines = [line.replace("--More--", "").replace("---- More ----", "") for line in lines]
        lines = [line for line in lines if line.strip() != "show running-config" and not re.fullmatch(r"\S+[>#]", line.strip())]
        result = "\n".join(lines).strip() + "\n"
        if len(result.encode("utf-8")) < 100:
            raise VSOLOLTError("A configuração retornada pela OLT está incompleta.")
        try:
            connection.sendall(b"exit\r\n")
        except OSError:
            pass
        return result
    finally:
        try:
            connection.close()
        except OSError:
            pass
