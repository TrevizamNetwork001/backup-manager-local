from __future__ import annotations

from .fiberhome_olt import FiberHomeFTPConfig, run_telnet_backup
from .olt_backup import OLTBackupError, OLTBackupRequest, build_plan, run_ssh_plan
from .olt_operations import create_operation, fail_operation
from .security import decrypt_secret
from .ssh import active_credential, connect_device, is_ftp_push_olt_driver
from .vsol_olt import VSOLOLTError, VSOLFTPConfig, run_telnet_backup as run_vsol_telnet_backup


def _setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row and row[0] else default


def trigger_olt_backup(conn, equipment_id: int, *, action: str = "manual", job_run_id: int | None = None):
    equipment = conn.execute("SELECT * FROM equipment WHERE id=? AND is_active=1", (equipment_id,)).fetchone()
    account = conn.execute("""SELECT * FROM ftp_accounts WHERE equipment_id=? AND is_active=1 AND deleted_at IS NULL
        ORDER BY updated_at DESC,id DESC LIMIT 1""", (equipment_id,)).fetchone()
    credential = active_credential(conn, equipment_id)
    if not equipment or not is_ftp_push_olt_driver(equipment["ssh_backup_driver"] or ""):
        raise OLTBackupError("Equipamento OLT ausente, inativo ou incompatível.")
    if not account or not credential:
        raise OLTBackupError("Conta FTP ou credencial de acesso ausente.")
    reference = account["password_hash_or_secret_reference"] or ""
    if not reference.startswith("fernet:"):
        raise OLTBackupError("A senha FTP precisa ser redefinida para permitir automação.")
    ftp_password = decrypt_secret(reference.removeprefix("fernet:"))
    ftp_host = _setting(conn, "ftp_public_ip") or _setting(conn, "ftp_public_ipv6")
    ftp_port = int(account["control_port"] or _setting(conn, "ftp_control_port", "21"))
    connect_timeout = int(_setting(conn, "ssh_connect_timeout_seconds", "15"))
    command_timeout = int(_setting(conn, "ssh_command_timeout_seconds", "60"))
    plan = build_plan(OLTBackupRequest(equipment["ssh_backup_driver"], equipment["hostname"], ftp_host,
                                      ftp_port, "/", account["username"], ftp_password, action))
    operation = (create_operation(conn, equipment_id=equipment_id, expected_files=plan.expected_files,
                                  timeout_seconds=max(60, command_timeout * 3), job_run_id=job_run_id)
                 if plan.expected_files else None)
    client = None
    device_password = ""
    try:
        if plan.transport == "telnet":
            device_password = decrypt_secret(credential["password_encrypted"])
            if equipment["ssh_backup_driver"] == "vsol_olt_telnet_cli":
                ftp = VSOLFTPConfig(ftp_host, ftp_port, account["username"], ftp_password, plan.expected_files[0])
                result = run_vsol_telnet_backup(
                    olt_host=equipment["ip_address"], olt_port=int(credential["port"] or 23),
                    olt_username=credential["username"], olt_password=device_password,
                    ftp=ftp, timeout=command_timeout,
                )
            else:
                ftp = FiberHomeFTPConfig(ftp_host, ftp_port, account["username"], ftp_password, *plan.expected_files)
                result = run_telnet_backup(olt_host=equipment["ip_address"], olt_port=int(credential["port"] or 23),
                                           olt_username=credential["username"], olt_password=device_password,
                                           ftp=ftp, timeout=command_timeout)
        else:
            client = connect_device(equipment, credential, connect_timeout)
            plan_timeout = max(command_timeout, 300) if equipment["ssh_backup_driver"] == "huawei_olt_ssh_ftp" else command_timeout
            result = run_ssh_plan(client, plan, timeout=plan_timeout)
        return plan, operation, result
    except Exception as exc:
        if operation:
            fail_operation(conn, operation["id"], "olt_trigger_failed", "A OLT não concluiu o acionamento remoto.")
        if isinstance(exc, (OLTBackupError, VSOLOLTError)):
            if isinstance(exc, VSOLOLTError):
                raise OLTBackupError(str(exc)) from exc
            raise
        raise OLTBackupError("A OLT não concluiu o acionamento remoto.") from exc
    finally:
        ftp_password = device_password = ""
        if client is not None:
            client.close()
