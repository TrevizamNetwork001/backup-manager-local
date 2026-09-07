from __future__ import annotations

import io
import json
import re
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from .security import SecretKeyError, decrypt_secret, encrypt_secret
from .storage import (
    atomic_move,
    backup_relative_path,
    ensure_directories,
    load_config,
    resolve_inside,
    safe_filename,
    write_bytes_to_temporary,
)

try:
    import paramiko
except Exception:  # pragma: no cover - depends on host package state
    paramiko = None


GENERIC_VENDOR_DRIVERS = {
    "zte_olt_ssh_ftp": "ZTE — OLT (SSH + FTP)",
    "intelbras_gpon_ssh_ftp": "Intelbras — OLT GPON 8820i/G08/G16 (SSH + FTP interativo)",
    "intelbras_epon_ssh_ftp": "Intelbras — OLT EPON 4840 E (SSH + FTP)",
    "vsol_olt_ssh_ftp": "VSOL — OLT (SSH + FTP)",
    "parks_olt_100_200_ssh_ftp": "Parks — OLT 100xx/200xx (SSH + FTP)",
    "parks_olt_300_400_ssh_ftp": "Parks — OLT 300xx/400xx (SSH + FTP)",
    "cdata_olt_ssh_ftp": "C-DATA — OLT GPON (SSH + FTP)",
    "fiberhome_olt_telnet_ftp": "FiberHome — OLT (Telnet + FTP)",
    "vsol_olt_telnet_cli": "VSOL V1600GT — OLT (backup direto via Telnet)",
}
LEGACY_DRIVER_LABELS = {
    "zte_ssh": "Legado ZTE — selecione o tipo/modelo",
    "intelbras_ssh": "Legado Intelbras — selecione o tipo/modelo",
    "vsol_ssh": "Legado VSOL — selecione o tipo/modelo",
    "parks_ssh": "Legado Parks — selecione a família",
    "cdata_ssh": "Legado C-DATA — selecione o tipo/modelo",
    "fiberhome_ssh": "Legado FiberHome — selecione o tipo/modelo",
    "datacom_ssh": "Legado Datacom — selecione o tipo/modelo",
}
SSH_DRIVERS = ("generic_ssh", "mikrotik_routeros", "huawei_vrp", "huawei_router", "huawei_olt_ssh_ftp",
               "datacom_dmos_ssh",
               "cisco_ios", *GENERIC_VENDOR_DRIVERS)
SSH_DRIVER_LABELS = {
    "generic_ssh": "Generico SSH",
    "mikrotik_routeros": "MikroTik RouterOS",
    "huawei_vrp": "Huawei — Switch",
    "huawei_router": "Huawei — Roteador",
    "huawei_olt_ssh_ftp": "Huawei — OLT (SSH + FTP)",
    "datacom_dmos_ssh": "Datacom — OLT DM461X (DmOS via SSH)",
    "cisco_ios": "Cisco IOS",
    **GENERIC_VENDOR_DRIVERS,
    **LEGACY_DRIVER_LABELS,
}
SSH_DRIVER_GROUPS = (
    ("OLTs", (
        "huawei_olt_ssh_ftp", "zte_olt_ssh_ftp", "fiberhome_olt_telnet_ftp",
        "intelbras_gpon_ssh_ftp", "intelbras_epon_ssh_ftp", "vsol_olt_ssh_ftp", "vsol_olt_telnet_cli",
        "parks_olt_100_200_ssh_ftp", "parks_olt_300_400_ssh_ftp",
        "cdata_olt_ssh_ftp", "datacom_dmos_ssh",
    )),
    ("Roteadores", ("mikrotik_routeros", "huawei_router")),
    ("Switches", ("huawei_vrp", "cisco_ios")),
    ("Genérico", ("generic_ssh",)),
)
FTP_PUSH_OLT_DRIVERS = frozenset(SSH_DRIVER_GROUPS[0][1]) - {"datacom_dmos_ssh", "vsol_olt_telnet_cli"}


def driver_group(driver: str) -> str:
    singular = {"OLTs": "OLT", "Roteadores": "Roteador", "Switches": "Switch", "Genérico": "Genérico"}
    for group, drivers in SSH_DRIVER_GROUPS:
        if driver in drivers:
            return singular[group]
    return "Genérico"


def driver_label(driver: str) -> str:
    return SSH_DRIVER_LABELS.get(driver or "", "Nenhum")


def is_ftp_push_olt_driver(driver: str) -> bool:
    return driver in FTP_PUSH_OLT_DRIVERS
ERROR_MESSAGES = {
    "SSH_CREDENTIAL_MISSING": "Credencial SSH nao configurada.",
    "SSH_DRIVER_MISSING": "Driver SSH nao configurado.",
    "SSH_CUSTOM_COMMAND_MISSING": "Comando SSH customizado inválido ou ausente.",
    "SSH_OPERATION_UNSUPPORTED": "Operação SSH indisponível para o driver configurado.",
    "SSH_CONNECTION_FAILED": "Nao foi possivel conectar ao equipamento pela porta SSH configurada.",
    "SSH_AUTH_FAILED": "Falha de autenticacao. Verifique usuario e senha.",
    "SSH_EMPTY_OUTPUT": "O comando retornou saida vazia.",
    "SSH_INVALID_OUTPUT": "O comando retornou saida invalida.",
    "SSH_TIMEOUT": "Tempo limite ao tentar conectar ao equipamento.",
    "SSH_CONNECTION_REFUSED": "A conexao SSH foi recusada pelo equipamento.",
    "TELNET_CONNECTION_FAILED": "Nao foi possivel conectar ao terminal Telnet do equipamento.",
    "SSH_SECRET_UNAVAILABLE": "Chave local de credenciais indisponivel.",
    "ROUTEROS_SYNTAX_ERROR": "O RouterOS recusou um comando da instalação.",
    "ROUTEROS_VERSION_UNSUPPORTED": "A versão detectada do RouterOS não é compatível.",
    "ROUTEROS_SCRIPT_FAILED": "O RouterOS não confirmou a instalação dos scripts.",
    "ROUTEROS_SCHEDULER_FAILED": "O RouterOS não confirmou o agendamento.",
    "FTP_FILE_NOT_RECEIVED": "O arquivo de teste não foi recebido e validado pelo Backup Manager.",
}
MIN_OUTPUT_BYTES = 20
CUSTOM_COMMAND_RE = re.compile(r"^[^\r\n\x00]{1,200}$")
BAD_OUTPUT_MARKERS = (
    "Authentication failed",
    "Permission denied",
    "Invalid input",
    "Unknown command",
    "Error:",
)


class SSHBackupError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or ERROR_MESSAGES.get(code, code))


@dataclass(frozen=True)
class SSHCommandResult:
    output: str
    bytes_received: int
    commands: tuple[str, ...]


class BackupDriver:
    vendor_key = "generic_ssh"

    def __init__(self, equipment) -> None:
        self.equipment = equipment

    def test_commands(self) -> tuple[str, ...]:
        return ("echo backup-manager-test",)

    def validate_backup_readiness(self) -> None:
        return None

    def backup_commands(self) -> tuple[str, ...]:
        self.validate_backup_readiness()
        command = (self.equipment["ssh_custom_command"] or "").strip()
        return (command,)

    def normalize_output(self, output: str) -> str:
        return output.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"

    def validate_output(self, output: str) -> None:
        data = output.strip()
        if not data:
            raise SSHBackupError("SSH_EMPTY_OUTPUT")
        if len(data.encode("utf-8")) < MIN_OUTPUT_BYTES:
            raise SSHBackupError("SSH_INVALID_OUTPUT")
        lowered = data.lower()
        for marker in BAD_OUTPUT_MARKERS:
            if marker.lower() in lowered:
                raise SSHBackupError("SSH_INVALID_OUTPUT")

    def extension(self) -> str:
        return ".cfg"

    def suggested_filename(self, equipment, timestamp: datetime) -> str:
        slug = slugify(equipment["hostname"])
        stamp = timestamp.strftime("%Y%m%d%H%M%S")
        return safe_filename(f"{slug}_{stamp}_{self.vendor_key}{self.extension()}")


class GenericSSHDriver(BackupDriver):
    def validate_backup_readiness(self) -> None:
        command = (self.equipment["ssh_custom_command"] or "").strip()
        if not validate_custom_command(command):
            raise SSHBackupError("SSH_CUSTOM_COMMAND_MISSING")


class MikroTikRouterOSDriver(BackupDriver):
    vendor_key = "mikrotik_routeros"

    def test_commands(self) -> tuple[str, ...]:
        return ("/system identity print",)

    def backup_commands(self) -> tuple[str, ...]:
        return ("/export",)

    def extension(self) -> str:
        return ".rsc"

    def validate_output(self, output: str) -> None:
        super().validate_output(output)
        if "#" not in output and "/interface" not in output and "/ip" not in output:
            return


class HuaweiVRPDriver(BackupDriver):
    vendor_key = "huawei_vrp"

    def test_commands(self) -> tuple[str, ...]:
        return ("screen-length 0 temporary", "display version")

    def backup_commands(self) -> tuple[str, ...]:
        return ("screen-length 0 temporary", "display current-configuration")


class HuaweiRouterDriver(HuaweiVRPDriver):
    vendor_key = "huawei_router"

    def backup_commands(self) -> tuple[str, ...]:
        return ("display current-configuration | no-more",)


class CiscoIOSDriver(BackupDriver):
    vendor_key = "cisco_ios"

    def test_commands(self) -> tuple[str, ...]:
        return ("terminal length 0", "show version")

    def backup_commands(self) -> tuple[str, ...]:
        return ("terminal length 0", "show running-config")


class DatacomDmOSDriver(BackupDriver):
    vendor_key = "datacom_dmos_ssh"

    def test_commands(self) -> tuple[str, ...]:
        return ("show version",)

    def backup_commands(self) -> tuple[str, ...]:
        return ("show running-config",)


class VSOLTelnetDriver(BackupDriver):
    vendor_key = "vsol_olt_telnet_cli"


DRIVER_CLASSES = {
    "generic_ssh": GenericSSHDriver,
    "mikrotik_routeros": MikroTikRouterOSDriver,
    "huawei_vrp": HuaweiVRPDriver,
    "huawei_router": HuaweiRouterDriver,
    "huawei_olt_ssh_ftp": BackupDriver,
    "datacom_dmos_ssh": DatacomDmOSDriver,
    "cisco_ios": CiscoIOSDriver,
    **{key: BackupDriver for key in GENERIC_VENDOR_DRIVERS},
    **{key: BackupDriver for key in LEGACY_DRIVER_LABELS},
    "vsol_olt_telnet_cli": VSOLTelnetDriver,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def dt_to_db(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip().lower()).strip("._-")
    return slug or "equipment"


def validate_custom_command(command: str) -> bool:
    if not command or not CUSTOM_COMMAND_RE.match(command):
        return False
    forbidden = (";", "&&", "||", "`", "$(", "<", ">")
    return not any(item in command for item in forbidden)


def get_driver(equipment):
    key = equipment["ssh_backup_driver"] or ""
    if key not in DRIVER_CLASSES:
        raise SSHBackupError("SSH_DRIVER_MISSING")
    return DRIVER_CLASSES[key](equipment)


def active_credential(conn, equipment_id: int):
    return conn.execute(
        """
        SELECT * FROM device_credentials
        WHERE equipment_id = ? AND credential_type = 'ssh' AND is_active = 1 AND deleted_at IS NULL
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (equipment_id,),
    ).fetchone()


def create_credential(conn, equipment_id: int, user_id: int | None, name: str, username: str, password: str,
                      port: int = 22, notes: str = ""):
    if not name.strip() or not username.strip() or not password:
        raise ValueError("Nome, usuario e senha sao obrigatorios.")
    encrypted = encrypt_secret(password)
    cred_uuid = str(uuid.uuid4())
    conn.execute(
        """
        UPDATE device_credentials
        SET is_active = 0, updated_at = CURRENT_TIMESTAMP
        WHERE equipment_id = ? AND credential_type = 'ssh' AND deleted_at IS NULL
        """,
        (equipment_id,),
    )
    conn.execute(
        """
        INSERT INTO device_credentials(uuid, equipment_id, credential_type, name, username,
                                       password_encrypted, port, auth_method, created_by_user_id, notes)
        VALUES (?, ?, 'ssh', ?, ?, ?, ?, 'password', ?, ?)
        """,
        (cred_uuid, equipment_id, name.strip()[:120], username.strip()[:120], encrypted, port, user_id, notes.strip()[:500]),
    )
    audit_event(conn, user_id, "credential.created", "device_credential", cred_uuid, {"equipment_id": equipment_id, "type": "ssh"})
    return conn.execute("SELECT * FROM device_credentials WHERE uuid = ?", (cred_uuid,)).fetchone()


def update_credential(conn, credential_id: int, user_id: int | None, name: str, username: str, password: str,
                      port: int, notes: str = ""):
    row = conn.execute("SELECT * FROM device_credentials WHERE id = ? AND deleted_at IS NULL", (credential_id,)).fetchone()
    if not row:
        raise ValueError("Credencial nao encontrada.")
    password_sql = ""
    params: list[object] = [name.strip()[:120], username.strip()[:120], port, notes.strip()[:500]]
    if password:
        password_sql = ", password_encrypted = ?"
        params.append(encrypt_secret(password))
    params.append(credential_id)
    conn.execute(
        f"""
        UPDATE device_credentials
        SET name = ?, username = ?, port = ?, notes = ?, updated_at = CURRENT_TIMESTAMP{password_sql}
        WHERE id = ? AND deleted_at IS NULL
        """,
        params,
    )
    audit_event(conn, user_id, "credential.updated", "device_credential", row["uuid"], {"equipment_id": row["equipment_id"], "type": "ssh"})
    return conn.execute("SELECT * FROM device_credentials WHERE id = ?", (credential_id,)).fetchone()


def audit_event(conn, user_id: int | None, action: str, entity: str, entity_id: object = "", details: dict | None = None) -> None:
    conn.execute(
        """
        INSERT INTO audit_log(user_id, action, entity, entity_id, details, ip_address)
        VALUES (?, ?, ?, ?, ?, '')
        """,
        (user_id, action, entity, str(entity_id), json.dumps(details or {}, separators=(",", ":"))),
    )


def _client_factory():
    if paramiko is None:
        raise SSHBackupError("SSH_CONNECTION_FAILED", "Biblioteca Paramiko nao instalada.")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    return client


def _paramiko_exception(name: str):
    if paramiko is None:
        return ()
    ssh_exception = getattr(paramiko, "ssh_exception", None)
    candidates = [getattr(paramiko, name, None)]
    if ssh_exception is not None:
        candidates.append(getattr(ssh_exception, name, None))
    return tuple(candidate for candidate in candidates if isinstance(candidate, type))


def normalize_ssh_exception(exc: BaseException) -> SSHBackupError:
    if isinstance(exc, SSHBackupError):
        return exc
    if isinstance(exc, socket.timeout):
        return SSHBackupError("SSH_TIMEOUT")
    if isinstance(exc, ConnectionRefusedError):
        return SSHBackupError("SSH_CONNECTION_REFUSED")
    auth_errors = _paramiko_exception("AuthenticationException")
    if auth_errors and isinstance(exc, auth_errors):
        return SSHBackupError("SSH_AUTH_FAILED")
    no_valid_errors = _paramiko_exception("NoValidConnectionsError")
    if no_valid_errors and isinstance(exc, no_valid_errors):
        errors = getattr(exc, "errors", {}) or {}
        if any(isinstance(inner, ConnectionRefusedError) for inner in errors.values()):
            return SSHBackupError("SSH_CONNECTION_REFUSED")
        if any(isinstance(inner, socket.timeout) for inner in errors.values()):
            return SSHBackupError("SSH_TIMEOUT")
        return SSHBackupError("SSH_CONNECTION_FAILED")
    return SSHBackupError("SSH_CONNECTION_FAILED")


def _connect_client(equipment, credential, connect_timeout: int, client_factory=None):
    try:
        password = decrypt_secret(credential["password_encrypted"])
    except SecretKeyError as exc:
        raise SSHBackupError("SSH_SECRET_UNAVAILABLE") from exc
    client = None
    try:
        client = (client_factory or _client_factory)()
        client.connect(
            hostname=equipment["ip_address"],
            port=int(credential["port"] or 22),
            username=credential["username"],
            password=password,
            look_for_keys=False,
            allow_agent=False,
            timeout=connect_timeout,
            banner_timeout=connect_timeout,
            auth_timeout=connect_timeout,
        )
    except Exception as exc:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        raise normalize_ssh_exception(exc) from exc
    finally:
        password = ""
    return client


def connect_device(equipment, credential, connect_timeout: int, client_factory=None):
    """Public connection entry point for vendor modules; credential decryption stays centralized."""
    return _connect_client(equipment, credential, connect_timeout, client_factory)


def run_commands(client, commands: tuple[str, ...], timeout: int) -> SSHCommandResult:
    chunks: list[str] = []
    for command in commands:
        try:
            _, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise normalize_ssh_exception(exc) from exc
        chunks.append(out)
        if err.strip():
            chunks.append(err)
    output = "\n".join(chunk for chunk in chunks if chunk)
    return SSHCommandResult(output=output, bytes_received=len(output.encode("utf-8")), commands=commands)


HUAWEI_SHELL_MAX_BYTES = 8 * 1024 * 1024
HUAWEI_PROMPT_RE = re.compile(r"(?m)^(?:<[^>\r\n]+>|\[[^\]\r\n]+\])\s*$")


def run_huawei_shell(client, commands: tuple[str, ...], timeout: int) -> SSHCommandResult:
    """Run read-only Huawei CLI commands through the interactive shell required by VRP."""
    channel = None
    chunks: list[str] = []

    def receive_until_prompt() -> str:
        deadline = time.monotonic() + timeout
        received = bytearray()
        while time.monotonic() < deadline:
            try:
                if channel.recv_ready():
                    data = channel.recv(65535)
                    if not data:
                        raise SSHBackupError("SSH_CONNECTION_FAILED", "O equipamento encerrou o shell SSH interativo.")
                    received.extend(data)
                    if len(received) > HUAWEI_SHELL_MAX_BYTES:
                        raise SSHBackupError("SSH_INVALID_OUTPUT", "A saída SSH excedeu o limite seguro.")
                    text = received.decode("utf-8", errors="replace")
                    if HUAWEI_PROMPT_RE.search(text):
                        return text
                else:
                    time.sleep(0.05)
            except SSHBackupError:
                raise
            except Exception as exc:
                raise normalize_ssh_exception(exc) from exc
        raise SSHBackupError("SSH_TIMEOUT", "Tempo limite aguardando o prompt do equipamento Huawei.")

    try:
        channel = client.invoke_shell(width=160, height=48)
        receive_until_prompt()
        for command in commands:
            channel.send(command + "\n")
            chunks.append(receive_until_prompt())
    except SSHBackupError:
        raise
    except Exception as exc:
        raise normalize_ssh_exception(exc) from exc
    finally:
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
    output = "\n".join(chunks)
    return SSHCommandResult(output=output, bytes_received=len(output.encode("utf-8")), commands=commands)


ROUTEROS_ERROR_MARKERS = (
    "syntax error", "expected closing", "failure", "invalid", "no such item", "missing",
    "bad command", "input does not match", "expected end of command",
)


def parse_routeros_output(output: str, error: str = "", *, step: str, secret: bool = False,
                          content_output: bool = False) -> str:
    """Reject RouterOS errors even when the same output also contains a positive log line."""
    combined = "\n".join(value for value in (output, error) if value).strip()
    semantic_output = "\n".join(line for line in combined.splitlines() if not line.lstrip().casefold().startswith("flags:"))
    semantic_output = re.sub(r'source="(?:\\.|[^"\\])*"', 'source="<redacted>"', semantic_output,
                             flags=re.IGNORECASE | re.DOTALL)
    lowered = semantic_output.casefold()
    ftp_auth_markers = ("invalid user name or password", "invalid username or password",
                        "login incorrect", "authentication failed", "not logged in")
    if step == "Comando FTP" and any(item in lowered for item in ftp_auth_markers):
        raise SSHBackupError("FTP_AUTH_FAILED", "Autenticação FTP recusada. Verifique o usuário e a senha FTP configurados.")
    marker = None if content_output else next((item for item in ROUTEROS_ERROR_MARKERS if item in lowered), None)
    if error.strip() or marker:
        location = re.search(r"(?:line|linha)\s+\d+(?:\s+(?:column|coluna)\s+\d+)?", combined, re.IGNORECASE)
        protected_reason = "comando com dados protegidos recusado pelo RouterOS"
        if marker:
            protected_reason += f" ({marker}{'; ' + location.group(0) if location else ''})"
            marker_line = next((line.strip() for line in semantic_output.splitlines() if marker in line.casefold()), "")
            if (marker_line and len(marker_line) <= 200
                    and not any(word in marker_line.casefold() for word in ("password", "bmuser", "source=", "ftp-user"))):
                protected_reason += ": " + marker_line
        reason = (protected_reason if secret else
                  re.sub(r"[\r\n]+", " ", combined)[:240] or "comando recusado pelo RouterOS")
        raise SSHBackupError("ROUTEROS_SYNTAX_ERROR", f"{step}: {reason}")
    return output.strip()


def _routeros_command(client, command: str, timeout: int, *, step: str, secret: bool = False,
                      content_output: bool = False) -> str:
    """Execute one logical RouterOS operation and never include its command in an exception."""
    try:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        output = stdout.read().decode("utf-8", errors="replace")
        error = stderr.read().decode("utf-8", errors="replace")
    except Exception as exc:
        normalized = normalize_ssh_exception(exc)
        raise SSHBackupError(normalized.code, f"{step}: {normalized}") from exc
    return parse_routeros_output(output, error, step=step, secret=secret, content_output=content_output)


def _routeros_major(version_output: str) -> int:
    match = re.search(r"(?:^|\s)((\d+)\.(\d+)(?:\.\d+)?)(?=\D|$)", version_output)
    if not match:
        raise SSHBackupError("ROUTEROS_VERSION_UNSUPPORTED", "Detecção do RouterOS: versão não reconhecida; nenhuma alteração foi realizada.")
    major, minor = int(match.group(2)), int(match.group(3))
    if major == 6 and minor >= 43:
        return 6
    if major == 7:
        return 7
    minimum = "6.43" if major == 6 else "7.0"
    raise SSHBackupError(
        "ROUTEROS_VERSION_UNSUPPORTED",
        f"Detecção do RouterOS: versão {match.group(1)} não compatível; mínimo suportado {minimum}. Nenhuma alteração foi realizada.",
    )


def _routeros_preflight(client, config, command_timeout: int) -> dict[str, object]:
    from .mikrotik_ftp_scripts import SCRIPT_VERSION, config_fingerprint
    script_count = int(_routeros_command(client, '/system script print count-only where name="backup-manager"', command_timeout, step="Verificação dos objetos").strip() or 0)
    scheduler_count = int(_routeros_command(client, '/system scheduler print count-only where name="backup-manager"', command_timeout, step="Verificação dos objetos").strip() or 0)
    script_detail = _routeros_command(client, '/system script print detail where name="backup-manager"', command_timeout, step="Verificação do script", secret=True, content_output=True) if script_count else ""
    scheduler_detail = _routeros_command(client, '/system scheduler print detail where name="backup-manager"', command_timeout, step="Verificação do scheduler") if scheduler_count else ""
    expected_disabled = config.schedule_mode != "scheduled"
    disabled = "disabled=yes" in scheduler_detail or bool(re.search(r'(?m)^\s*\d+\s+X\b', scheduler_detail))
    script_valid = (script_count == 1
                    and f"backup-manager-version={SCRIPT_VERSION}" in script_detail
                    and f"backup-manager-config={config_fingerprint(config)}" in script_detail)
    scheduler_valid = (scheduler_count == 1 and "/system script run backup-manager" in scheduler_detail
                       and "interval=1d" in scheduler_detail and "sensitive" in scheduler_detail
                       and re.search(r"policy=[^\r\n]*\bpolicy\b", scheduler_detail)
                       and disabled == expected_disabled)
    if script_count == 0 and scheduler_count == 0: state = "not_installed"
    elif script_count > 1 or scheduler_count > 1: state = "duplicated"
    elif script_count == 0 or scheduler_count == 0: state = "partial"
    elif script_valid and scheduler_valid: state = "installed_valid"
    else: state = "divergent"
    return {"state": state, "script_count": script_count, "scheduler_count": scheduler_count,
            "script_valid": script_valid, "scheduler_valid": scheduler_valid,
            "script_detail": script_detail, "scheduler_detail": scheduler_detail}


def install_routeros_script(equipment, credential, config, connect_timeout: int,
                            command_timeout: int, client_factory=None, *, repair: bool = False) -> dict[str, object]:
    """Preflight and install only the managed script/scheduler objects."""
    from .mikrotik_ftp_scripts import generate_install_script, validate_config

    if equipment["ssh_backup_driver"] != "mikrotik_routeros":
        raise SSHBackupError("SSH_OPERATION_UNSUPPORTED", "A instalação automática está disponível somente para MikroTik RouterOS.")
    if not credential:
        raise SSHBackupError("SSH_CREDENTIAL_MISSING")
    host = str(equipment.get("ip_address") or "").strip()
    username = str(credential.get("username") or "").strip()
    port = credential.get("port") or equipment.get("ssh_port")
    if not host or not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", host):
        raise SSHBackupError("SSH_CONNECTION_FAILED", "Validação SSH: host/IP inválido.")
    if not username or any(char in username for char in "\r\n\x00"):
        raise SSHBackupError("SSH_CREDENTIAL_MISSING", "Validação SSH: usuário ausente ou inválido.")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise SSHBackupError("SSH_CONNECTION_FAILED", "Validação SSH: porta inválida.")
    validate_config(config)
    client = None
    version_text = ""
    current_stage = "connection"
    try:
        client = _connect_client(equipment, credential, connect_timeout, client_factory)
        current_stage = "routeros"
        version_text = _routeros_command(client, ":put [/system resource get version]", command_timeout, step="Detecção do RouterOS")
        detected = _routeros_major(version_text)
        effective = config.__class__(**{**config.__dict__, "routeros_version": detected})
        before = _routeros_preflight(client, effective, command_timeout)
        if before["state"] == "installed_valid":
            return {"routeros_major": detected, "routeros_version": version_text, "preflight": before,
                    "preflight_before": before, "changed": False, "script_installed": True, "scheduler_installed": True}
        if before["state"] != "not_installed" and not repair:
            return {"routeros_major": detected, "routeros_version": version_text, "preflight": before,
                    "preflight_before": before, "changed": False, "needs_repair": True, "script_installed": before["script_valid"],
                    "scheduler_installed": before["scheduler_valid"]}
        remote_files: list[str] = []
        try:
            sftp = client.open_sftp()
            current_stage = "script"
            install_name = f"backup-manager-install-{uuid.uuid4().hex}.rsc"
            remote_files.extend((install_name, f"disk/{install_name}"))
            sftp.putfo(io.BytesIO(generate_install_script(effective, include_secret=True).encode("utf-8")), f"/{install_name}")
            import_name = install_name
            transferred = "0"
            for attempt in range(3):
                transferred = _routeros_command(client, f'/file print count-only where name="{install_name}"', command_timeout, step="Validação da transferência")
                if transferred.strip() == "1":
                    import_name = install_name
                    break
                disk_name = f"disk/{install_name}"
                transferred = _routeros_command(client, f'/file print count-only where name="{disk_name}"', command_timeout, step="Validação da transferência")
                if transferred.strip() == "1":
                    import_name = disk_name
                    break
                if attempt < 2:
                    _routeros_command(client, ":delay 1s", command_timeout, step="Aguardando registro do arquivo")
            if transferred.strip() != "1":
                raise SSHBackupError("ROUTEROS_SCRIPT_FAILED", "Validação da transferência: arquivo .rsc não encontrado no MikroTik.")
            _routeros_command(client, f'/import file-name="{import_name}" verbose=no', command_timeout,
                              step="Importação dos objetos", secret=True)

            current_stage = "scheduler"
            after = _routeros_preflight(client, effective, command_timeout)
            if after["state"] != "installed_valid":
                raise SSHBackupError(
                    "ROUTEROS_SCRIPT_FAILED",
                    "Validação dos objetos: instalação divergente após a importação "
                    f"(estado={after['state']}, scripts={after['script_count']}, schedulers={after['scheduler_count']}, "
                    f"script_válido={'sim' if after['script_valid'] else 'não'}, scheduler_válido={'sim' if after['scheduler_valid'] else 'não'}).",
                )
        finally:
            for filename in remote_files:
                try:
                    _routeros_command(client, f'/file remove [find where name="{filename}"]', command_timeout, step="Remoção do arquivo temporário")
                except SSHBackupError:
                    pass
        return {"routeros_major": detected, "routeros_version": version_text, "preflight": after,
                "preflight_before": before, "changed": True, "script_installed": True, "scheduler_installed": True}
    except SSHBackupError as exc:
        exc.install_stage = current_stage
        exc.routeros_version = version_text
        raise
    except Exception as exc:
        normalized = normalize_ssh_exception(exc)
        normalized.install_stage = current_stage
        normalized.routeros_version = version_text
        raise normalized from exc
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def run_routeros_ftp_test(equipment, credential, config, connect_timeout: int,
                          command_timeout: int, client_factory=None) -> dict[str, object]:
    """Execute only the small, tokenless FTP probe; never touch managed objects."""
    from .mikrotik_ftp_scripts import generate_test_import, validate_config
    validate_config(config)
    client = _connect_client(equipment, credential, connect_timeout, client_factory)
    name = f"backup-manager-ftp-test-{uuid.uuid4().hex}.rsc"
    try:
        version_text = _routeros_command(client, ":put [/system resource get version]", command_timeout, step="Detecção do RouterOS")
        detected = _routeros_major(version_text)
        effective = config.__class__(**{**config.__dict__, "routeros_version": detected})
        preflight = _routeros_preflight(client, effective, command_timeout)
        if preflight["state"] != "installed_valid":
            raise SSHBackupError("ROUTEROS_SCRIPT_FAILED", "A instalação está incompleta ou desatualizada.")
        sftp = client.open_sftp()
        sftp.putfo(io.BytesIO(generate_test_import(effective).encode("utf-8")), f"/{name}")
        _routeros_command(client, f'/import file-name="{name}" verbose=no', command_timeout,
                          step="Comando FTP", secret=True)
        return {"routeros_major": detected, "routeros_version": version_text, "command_executed": True}
    finally:
        for temporary_name in (name, f"disk/{name}"):
            try:
                _routeros_command(client, f'/file remove [find where name="{temporary_name}"]', command_timeout, step="Limpeza do teste")
            except SSHBackupError:
                pass
        try:
            client.close()
        except Exception:
            pass


def run_routeros_managed_backup(equipment, credential, config, connect_timeout: int,
                                command_timeout: int, client_factory=None) -> dict[str, object]:
    """Run the installed managed script and preserve RouterOS' sanitized error."""
    client = _connect_client(equipment, credential, connect_timeout, client_factory)
    version_text = ""
    try:
        version_text = _routeros_command(client, ":put [/system resource get version]", command_timeout,
                                         step="Detecção do RouterOS")
        detected = _routeros_major(version_text)
        effective = config.__class__(**{**config.__dict__, "routeros_version": detected})
        preflight = _routeros_preflight(client, effective, command_timeout)
        if preflight["state"] != "installed_valid":
            raise SSHBackupError("ROUTEROS_SCRIPT_FAILED", "Execução: o script instalado está ausente ou desatualizado.")
        _routeros_command(client, '/system script run "backup-manager"', command_timeout,
                          step="Execução completa do backup", secret=True)
        expected_base = _routeros_command(client, ':put $backupManagerFtpLastBase', command_timeout,
                                          step="Identificação da execução").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", expected_base):
            raise SSHBackupError("ROUTEROS_SCRIPT_FAILED", "O RouterOS não informou o nome-base da execução atual.")
        return {"routeros_major": detected, "routeros_version": version_text,
                "executed": True, "expected_base": expected_base, "preflight": preflight}
    except SSHBackupError as exc:
        exc.routeros_version = version_text
        raise
    finally:
        try:
            client.close()
        except Exception:
            pass


def remove_routeros_backup_manager(equipment, credential, connect_timeout: int,
                                   command_timeout: int, client_factory=None) -> dict[str, object]:
    """Remove only Backup Manager's managed RouterOS objects and verify their absence."""
    if equipment["ssh_backup_driver"] != "mikrotik_routeros":
        raise SSHBackupError("SSH_OPERATION_UNSUPPORTED", "A remoção automática está disponível somente para MikroTik RouterOS.")
    if not credential:
        raise SSHBackupError("SSH_CREDENTIAL_MISSING")
    client = _connect_client(equipment, credential, connect_timeout, client_factory)
    try:
        version_text = _routeros_command(client, ":put [/system resource get version]", command_timeout,
                                         step="Detecção do RouterOS")
        detected = _routeros_major(version_text)
        _routeros_command(client, '/system scheduler remove [find where name="backup-manager"]',
                          command_timeout, step="Remoção do scheduler")
        _routeros_command(client, '/system script remove [find where name="backup-manager"]',
                          command_timeout, step="Remoção do script")
        scheduler_count = _routeros_command(
            client, '/system scheduler print count-only where name="backup-manager"',
            command_timeout, step="Validação da remoção"
        ).strip()
        script_count = _routeros_command(
            client, '/system script print count-only where name="backup-manager"',
            command_timeout, step="Validação da remoção"
        ).strip()
        if scheduler_count != "0" or script_count != "0":
            raise SSHBackupError("ROUTEROS_SCRIPT_FAILED", "Validação da remoção: ainda existem objetos gerenciados no MikroTik.")
        return {"routeros_major": detected, "routeros_version": version_text, "removed": True}
    finally:
        try:
            client.close()
        except Exception:
            pass


def load_equipment(conn, equipment_id: int):
    return conn.execute("SELECT * FROM equipment WHERE id = ? AND is_active = 1", (equipment_id,)).fetchone()


def test_connection(conn, equipment_id: int, user_id: int | None = None, credential_id: int | None = None, client_factory=None) -> None:
    equipment = load_equipment(conn, equipment_id)
    if not equipment:
        raise SSHBackupError("SSH_CONNECTION_FAILED", "Equipamento inativo ou ausente.")
    credential = conn.execute("SELECT * FROM device_credentials WHERE id = ? AND deleted_at IS NULL", (credential_id,)).fetchone() if credential_id else active_credential(conn, equipment_id)
    if not credential:
        raise SSHBackupError("SSH_CREDENTIAL_MISSING")
    driver = get_driver(equipment)
    audit_event(conn, user_id, "credential.test_started", "device_credential", credential["uuid"], {"equipment_id": equipment_id, "driver": driver.vendor_key})
    connect_timeout = int(setting(conn, "ssh_connect_timeout_seconds", "15"))
    command_timeout = int(setting(conn, "ssh_command_timeout_seconds", "60"))
    client = None
    try:
        if driver.vendor_key == "vsol_olt_telnet_cli":
            from .vsol_olt import collect_running_config
            try:
                collect_running_config(
                    host=equipment["ip_address"], port=int(credential["port"] or 23),
                    username=credential["username"], password=decrypt_secret(credential["password_encrypted"]),
                    timeout=command_timeout,
                )
            except Exception as exc:
                detail = str(exc).strip() or "Não foi possível abrir o terminal Telnet da VSOL."
                raise SSHBackupError("TELNET_CONNECTION_FAILED", detail) from exc
        else:
            client = _connect_client(equipment, credential, connect_timeout, client_factory)
            result = (run_huawei_shell(client, driver.test_commands(), command_timeout)
                      if isinstance(driver, HuaweiVRPDriver) else
                      run_commands(client, driver.test_commands(), command_timeout))
            if result.bytes_received <= 0:
                raise SSHBackupError("SSH_EMPTY_OUTPUT")
        driver.validate_backup_readiness()
        conn.execute(
            """
            UPDATE device_credentials
            SET last_test_status = 'success', last_test_at = CURRENT_TIMESTAMP,
                last_test_error = '', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (credential["id"],),
        )
        audit_event(conn, user_id, "credential.test_success", "device_credential", credential["uuid"], {"equipment_id": equipment_id, "driver": driver.vendor_key})
    except SSHBackupError as exc:
        conn.execute(
            """
            UPDATE device_credentials
            SET last_test_status = 'failed', last_test_at = CURRENT_TIMESTAMP,
                last_test_error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (ERROR_MESSAGES.get(exc.code, str(exc)), credential["id"]),
        )
        audit_event(conn, user_id, "credential.test_failed", "device_credential", credential["uuid"], {"equipment_id": equipment_id, "error_code": exc.code})
        raise
    finally:
        if client is not None:
            client.close()


def probe_connection(equipment, credential, connect_timeout: int, command_timeout: int, client_factory=None) -> str:
    driver = get_driver(equipment)
    if driver.vendor_key == "vsol_olt_telnet_cli":
        from .vsol_olt import collect_running_config
        try:
            collect_running_config(
                host=equipment["ip_address"], port=int(credential["port"] or 23),
                username=credential["username"], password=decrypt_secret(credential["password_encrypted"]),
                timeout=command_timeout,
            )
            driver.validate_backup_readiness()
            return driver.vendor_key
        except Exception as exc:
            detail = str(exc).strip() or "Não foi possível abrir o terminal Telnet da VSOL."
            raise SSHBackupError("TELNET_CONNECTION_FAILED", detail) from exc
    client = None
    try:
        client = _connect_client(equipment, credential, connect_timeout, client_factory)
        result = (run_huawei_shell(client, driver.test_commands(), command_timeout)
                  if isinstance(driver, HuaweiVRPDriver) else
                  run_commands(client, driver.test_commands(), command_timeout))
        if result.bytes_received <= 0:
            raise SSHBackupError("SSH_EMPTY_OUTPUT")
        driver.validate_backup_readiness()
        return driver.vendor_key
    finally:
        if client is not None:
            client.close()


def perform_backup(conn, equipment_id: int, run=None, user_id: int | None = None, backup_reason: str = "manual", client_factory=None):
    equipment = load_equipment(conn, equipment_id)
    if not equipment:
        raise SSHBackupError("SSH_CONNECTION_FAILED", "Equipamento inativo ou ausente.")
    if is_ftp_push_olt_driver(equipment["ssh_backup_driver"] or ""):
        raise SSHBackupError(
            "SSH_OPERATION_UNSUPPORTED",
            "Esta OLT envia o arquivo ao servidor FTP. Configure e execute o fluxo FTP específico do equipamento.",
        )
    credential = active_credential(conn, equipment_id)
    if not credential:
        raise SSHBackupError("SSH_CREDENTIAL_MISSING")
    driver = get_driver(equipment)
    driver.validate_backup_readiness()
    audit_event(conn, user_id, "ssh.backup_started", "equipment", equipment_id, {"driver": driver.vendor_key, "run_uuid": run["uuid"] if run else ""})
    connect_timeout = int(setting(conn, "ssh_connect_timeout_seconds", "15"))
    command_timeout = int(setting(conn, "ssh_command_timeout_seconds", "60"))
    client = None
    try:
        if driver.vendor_key == "vsol_olt_telnet_cli":
            from .vsol_olt import collect_running_config
            try:
                output = collect_running_config(
                    host=equipment["ip_address"], port=int(credential["port"] or 23),
                    username=credential["username"], password=decrypt_secret(credential["password_encrypted"]),
                    timeout=command_timeout,
                )
            except Exception as exc:
                detail = str(exc).strip() or "Não foi possível coletar a configuração via Telnet."
                raise SSHBackupError("TELNET_CONNECTION_FAILED", detail) from exc
        else:
            client = _connect_client(equipment, credential, connect_timeout, client_factory)
            raw = (run_huawei_shell(client, driver.backup_commands(), command_timeout)
                   if isinstance(driver, HuaweiVRPDriver) else
                   run_commands(client, driver.backup_commands(), command_timeout))
            output = driver.normalize_output(raw.output)
        driver.validate_output(output)
        created = save_ssh_backup(conn, equipment, output, driver, user_id, backup_reason)
        audit_event(conn, user_id, "backup.created_from_ssh", "backup", created["uuid"], {"equipment_id": equipment_id, "driver": driver.vendor_key, "size": created["file_size"]})
        audit_event(conn, user_id, "ssh.backup_success", "equipment", equipment_id, {"driver": driver.vendor_key, "backup_uuid": created["uuid"]})
        safe_log = "\n".join(
            (
                "Iniciando backup SSH.",
                f"Driver: {driver.vendor_key}.",
                "Conexao estabelecida.",
                "Comando de backup executado.",
                f"Saida recebida: {created['file_size']} bytes.",
                "Backup salvo com sucesso.",
            )
        )
        return created, safe_log
    except SSHBackupError as exc:
        audit_event(conn, user_id, "ssh.backup_failed", "equipment", equipment_id, {"driver": getattr(driver, "vendor_key", ""), "error_code": exc.code})
        raise
    finally:
        if client is not None:
            client.close()


def save_ssh_backup(conn, equipment, output: str, driver: BackupDriver, user_id: int | None, backup_reason: str):
    config = load_config(conn)
    ensure_directories(config)
    backup_uuid = str(uuid.uuid4())
    received_at = utc_now()
    original = driver.suggested_filename(equipment, received_at)
    content = output.encode("utf-8")
    temp_path, size, digest = write_bytes_to_temporary(content, config.temporary_directory, config.maximum_upload_size, backup_uuid)
    relative_path = backup_relative_path(equipment["id"], backup_uuid, received_at)
    target = resolve_inside(config.backup_directory, relative_path)
    if target.exists():
        temp_path.unlink(missing_ok=True)
        raise ValueError("Tentativa de sobrescrita bloqueada.")
    conn.execute(
        """
        INSERT INTO backups(uuid, equipment_id, original_filename, stored_filename, relative_path,
                            file_size, sha256, source_method, backup_reason, backup_status,
                            received_at, created_by_user_id, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'ssh', ?, 'validating', ?, ?, ?)
        """,
        (
            backup_uuid,
            equipment["id"],
            original,
            target.name,
            relative_path,
            size,
            digest,
            backup_reason,
            dt_to_db(received_at),
            user_id,
            f"driver={driver.vendor_key}",
        ),
    )
    atomic_move(temp_path, target)
    conn.execute("UPDATE backups SET backup_status = 'available' WHERE uuid = ?", (backup_uuid,))
    created = conn.execute("SELECT * FROM backups WHERE uuid = ?", (backup_uuid,)).fetchone()
    from .cloud_sync import enqueue_new_backup
    enqueue_new_backup(conn, created["id"])
    from .telegram_backup import enqueue_new_backup as enqueue_new_telegram_backup
    enqueue_new_telegram_backup(conn, created["id"])
    return created


def setting(conn, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default
