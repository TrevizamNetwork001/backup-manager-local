from __future__ import annotations

import json
from urllib.parse import parse_qs


def handle_equipment_ssh_test_logic(
    *,
    connect,
    equipment_id: int,
    user,
    environ: dict,
    get_setting,
    probe_connection,
    record_ssh_test_failure,
    audit,
    queue_flash,
    redirect,
    ERROR_MESSAGES,
    SSHBackupError,
):
    ip = environ.get("REMOTE_ADDR", "")
    onboarding = (parse_qs(environ.get("QUERY_STRING", "")).get("onboarding") or [""])[-1]
    if onboarding not in {"ftp", "ssh"}:
        onboarding = ""
    destination = f"/equipment/{equipment_id}" + (f"?onboarding={onboarding}" if onboarding else "")
    with connect() as conn:
        equipment = conn.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
        credential = conn.execute(
            """
            SELECT * FROM device_credentials
            WHERE equipment_id = ? AND credential_type = 'ssh' AND is_active = 1 AND deleted_at IS NULL
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (equipment_id,),
        ).fetchone()
        connect_timeout = int(get_setting(conn, "ssh_connect_timeout_seconds", "15"))
        command_timeout = int(get_setting(conn, "ssh_command_timeout_seconds", "60"))
        equipment_data = dict(equipment) if equipment else None
        credential_data = dict(credential) if credential else None

    credential_id = credential_data["id"] if credential_data else None
    credential_uuid = credential_data["uuid"] if credential_data else None
    driver_key = ""
    access_protocol = "Telnet" if equipment_data and equipment_data.get("ssh_backup_driver") == "vsol_olt_telnet_cli" else "SSH"
    error = ""
    error_code = ""
    try:
        if equipment_data is None:
            raise SSHBackupError("SSH_EQUIPMENT_NOT_FOUND", "O equipamento não foi encontrado.")
        if not equipment_data["is_active"]:
            raise SSHBackupError(
                "SSH_EQUIPMENT_INACTIVE",
                f"O equipamento está inativo. Ative-o em Editar equipamento antes de testar a conexão {access_protocol}.",
            )
        if credential_data is None:
            raise SSHBackupError("SSH_CREDENTIAL_MISSING")
        driver_key = probe_connection(equipment_data, credential_data, connect_timeout, command_timeout)
    except SSHBackupError as exc:
        error = str(exc) if exc.code == "TELNET_CONNECTION_FAILED" else ERROR_MESSAGES.get(exc.code, str(exc))
        error_code = exc.code

    conn = connect()
    try:
        if error:
            record_ssh_test_failure(conn, equipment_id, credential_id, user["id"], error_code, error, ip)
            queue_flash(conn, environ, "error", error)
        elif credential_id is not None:
            conn.execute(
                """
                UPDATE device_credentials
                SET last_test_status = 'success', last_test_at = CURRENT_TIMESTAMP,
                    last_test_error = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (credential_id,),
            )
            audit(
                conn,
                user["id"],
                "credential.test_success",
                "device_credential",
                credential_uuid,
                json.dumps({"equipment_id": equipment_id, "driver": driver_key}, separators=(",", ":")),
                ip,
            )
            queue_flash(conn, environ, "success", f"Conexão {access_protocol} testada com sucesso.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return redirect(destination)
