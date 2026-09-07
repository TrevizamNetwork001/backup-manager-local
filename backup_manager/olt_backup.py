from __future__ import annotations

import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone

from .cdata_olt import CDataFTPConfig, backup_command as cdata_command
from .fiberhome_olt import FiberHomeFTPConfig, backup_commands as fiberhome_commands
from .huawei_olt import HuaweiFTPConfig, backup_commands as huawei_commands
from .intelbras_olt import IntelbrasFTPConfig, epon_backup_command, gpon_backup_dialog
from .parks_olt import ParksFTPConfig, backup_100xx_200xx_command, backup_300xx_400xx_command
from .vsol_olt import VSOLFTPConfig, manual_backup_commands as vsol_commands
from .zte_olt import ZTEFTPConfig, automatic_backup_commands as zte_automatic, manual_test_commands as zte_manual


class OLTBackupError(ValueError):
    pass


@dataclass(frozen=True)
class OLTBackupRequest:
    driver: str
    hostname: str
    ftp_host: str
    ftp_port: int
    ftp_path: str
    ftp_username: str
    ftp_password: str
    action: str = "manual"


@dataclass(frozen=True)
class OLTBackupPlan:
    transport: str
    commands: tuple[str, ...]
    expected_files: tuple[str, ...]
    interactive: bool = False
    prompt_patterns: tuple[tuple[int, str], ...] = ()


def _base_name(hostname: str, now: datetime) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", hostname.strip()).strip("_") or "olt"
    return f"{slug}_{now.astimezone(timezone.utc).strftime('%Y%m%d%H%M%S')}"


def build_plan(request: OLTBackupRequest, *, now: datetime | None = None) -> OLTBackupPlan:
    now = now or datetime.now(timezone.utc)
    base = _base_name(request.hostname, now)
    common = (request.ftp_host, request.ftp_username, request.ftp_password)
    driver = request.driver
    if driver == "fiberhome_olt_telnet_ftp":
        names = (base + ".txt", base + ".db")
        config = FiberHomeFTPConfig(common[0], request.ftp_port, common[1], common[2], *names)
        return OLTBackupPlan("telnet", fiberhome_commands(config), names)
    if driver == "huawei_olt_ssh_ftp":
        names = (base + ".cfg", base + ".dat")
        return OLTBackupPlan("ssh", huawei_commands(HuaweiFTPConfig(*common, *names)), names,
                             interactive=False,
                             prompt_patterns=((6, r"User\s+Name.*:"), (7, r"User\s+Password.*:")))
    if driver == "zte_olt_ssh_ftp":
        config = ZTEFTPConfig(common[0], request.ftp_path, common[1], common[2])
        if request.action == "install":
            return OLTBackupPlan("ssh", zte_automatic(config), ())
        name = base + ".dat"
        return OLTBackupPlan("ssh", zte_manual(config, name), (name,))
    if driver in {"intelbras_gpon_ssh_ftp", "intelbras_epon_ssh_ftp"}:
        name = base + ".cfg"
        config = IntelbrasFTPConfig(*common, name)
        if driver == "intelbras_gpon_ssh_ftp":
            dialog = gpon_backup_dialog(config)
            return OLTBackupPlan("ssh", tuple(value for value, _ in dialog), (name,), interactive=True)
        return OLTBackupPlan("ssh", (epon_backup_command(config),), (name,))
    if driver in {"vsol_olt_ssh_ftp", "vsol_olt_telnet_cli"}:
        name = base + ".config"
        config = VSOLFTPConfig(common[0], request.ftp_port, common[1], common[2], name)
        transport = "telnet" if driver == "vsol_olt_telnet_cli" else "ssh"
        return OLTBackupPlan(transport, vsol_commands(config), (name,))
    if driver in {"parks_olt_100_200_ssh_ftp", "parks_olt_300_400_ssh_ftp"}:
        name = base + ".bin"
        config = ParksFTPConfig(*common, name)
        command = (backup_100xx_200xx_command(config) if driver == "parks_olt_100_200_ssh_ftp"
                   else backup_300xx_400xx_command(config))
        return OLTBackupPlan("ssh", (command,), (name,))
    if driver == "cdata_olt_ssh_ftp":
        name = base
        config = CDataFTPConfig(*common, name, "gz")
        return OLTBackupPlan("ssh", (cdata_command(config),), (name + ".gz",))
    raise OLTBackupError("Método de OLT não suportado pelo módulo operacional.")


def redact_plan(plan: OLTBackupPlan, password: str) -> OLTBackupPlan:
    encoded = urllib.parse.quote(password, safe="")
    return OLTBackupPlan(plan.transport, tuple(value.replace(password, "********").replace(encoded, "********") for value in plan.commands),
                         plan.expected_files, plan.interactive)


def run_ssh_plan(client, plan: OLTBackupPlan, *, timeout: int = 60, quiet_seconds: float = 0.25) -> dict[str, object]:
    """Execute an OLT plan in one persistent shell and return secret-free metadata."""
    if plan.transport != "ssh":
        raise OLTBackupError("O plano não utiliza SSH.")
    channel = client.invoke_shell()
    channel.settimeout(timeout)

    def receive() -> str:
        chunks: list[bytes] = []
        deadline = time.monotonic() + timeout
        quiet_at: float | None = None
        while time.monotonic() < deadline:
            if channel.recv_ready():
                chunk = channel.recv(65535)
                if not chunk:
                    break
                chunks.append(chunk)
                quiet_at = time.monotonic() + quiet_seconds
            elif quiet_at is not None and time.monotonic() >= quiet_at:
                break
            else:
                time.sleep(0.02)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def check_response(response: str, index: int) -> None:
        lowered = response.casefold()
        if any(marker in lowered for marker in ("invalid input", "unknown command", "command error", "authentication failed")):
            raise OLTBackupError(f"A OLT recusou a etapa {index + 1} do roteiro de backup.")
        if re.search(r"backing up files is fail(?:s|ed|ure)?\b", response, re.IGNORECASE):
            raise OLTBackupError(f"A OLT informou falha no backup FTP na etapa {index + 1}.")

    try:
        receive()
        last_response = ""
        for index, command in enumerate(plan.commands):
            if plan.interactive and index == 1 and not re.search(r"User\s*:?", last_response, re.IGNORECASE):
                raise OLTBackupError("A OLT Intelbras não apresentou o prompt de usuário FTP.")
            if plan.interactive and index == 2 and not re.search(r"Password\s*:?", last_response, re.IGNORECASE):
                raise OLTBackupError("A OLT Intelbras não apresentou o prompt de senha FTP.")
            channel.send(command + "\n")
            last_response = receive()
            if re.search(r"are you sure to continue\?\s*\(y/n\)", last_response, re.IGNORECASE):
                channel.send("y\n")
                last_response += receive()
            check_response(last_response, index)
            if command.startswith("backup configuration ftp ") or command.startswith("backup data ftp "):
                deadline = time.monotonic() + timeout
                while not re.search(r"backing up files is successful", last_response, re.IGNORECASE):
                    if time.monotonic() >= deadline:
                        raise OLTBackupError("A Huawei não confirmou a conclusão do backup FTP.")
                    time.sleep(0.2)
                    last_response += receive()
                    check_response(last_response, index)
        return {"ok": True, "commands_executed": len(plan.commands),
                "expected_files": plan.expected_files, "interactive": plan.interactive}
    finally:
        channel.close()
