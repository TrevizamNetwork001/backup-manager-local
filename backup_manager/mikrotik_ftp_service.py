from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone

from .mikrotik_ftp_credentials import create_isolated_credential
from .mikrotik_ftp_scripts import ScriptConfig
from .ftp import generate_password
from .ssh import ERROR_MESSAGES, SSHBackupError, install_routeros_script
from .security import SecretKeyError, decrypt_secret, encrypt_secret, hash_password
from .equipment_compatibility import require_mikrotik_equipment, require_mikrotik_integration

TEST_TTL_MINUTES = 15
FINAL_TEST_STATES = {"validated", "failed", "expired", "cancelled"}
ACTIVE_TEST_STATES = {"pending", "running", "waiting_upload", "validating"}
UPLOAD_TIMEOUT_MESSAGE = "O MikroTik concluiu a tentativa de envio, mas o servidor não recebeu o arquivo dentro do prazo."
MIKROTIK_DRIVER_KEYS = {"mikrotik_routeros", "mikrotik", "routeros"}
MIKROTIK_VENDOR_KEYS = {"mikrotik", "mikrotikls", "mikrotikls sia"}


def is_mikrotik_equipment(equipment, vendor_name: str | None = None) -> bool:
    """Recognize RouterOS devices through explicit legacy keys or a known vendor."""
    driver = str(equipment["ssh_backup_driver"] or "").strip().lower()
    vendor = str(vendor_name or "").strip().lower()
    return driver in MIKROTIK_DRIVER_KEYS or vendor in MIKROTIK_VENDOR_KEYS


def validate_ftp_directory(value: str) -> str:
    directory = (value or "/").strip()
    if (not directory.startswith("/") or len(directory) > 160 or ".." in directory.split("/")
            or not re.fullmatch(r"/[A-Za-z0-9._/-]*", directory)):
        raise ValueError("Diretório remoto FTP inválido.")
    return directory


def sanitize_error(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9À-ÿ .,;:_/-]", "_", value or "")
    return clean[:240]


def validate_time(value: str) -> str:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value or ""):
        raise ValueError("Horário inválido.")
    return value


def create_integration(conn, *, equipment_id: int, routeros_version: int, backup_format: str,
                       schedule_mode: str, schedule_time: str, ftp_host: str, ftp_port: int,
                       user_id: int | None, retention_days: int | None = None,
                       ftp_directory: str = "/", credential_action: str = "isolated"):
    equipment = require_mikrotik_equipment(conn, equipment_id)
    existing_integration = conn.execute(
        """SELECT i.id,a.is_active AS account_is_active,a.deleted_at AS account_deleted_at
           FROM mikrotik_ftp_integrations i
           LEFT JOIN ftp_accounts a ON a.id=i.ftp_account_id
           WHERE i.equipment_id=? AND i.is_active=1 ORDER BY i.id DESC LIMIT 1""",
        (equipment_id,),
    ).fetchone()
    if existing_integration:
        if existing_integration["account_is_active"] and not existing_integration["account_deleted_at"]:
            raise ValueError("Este equipamento já possui integração FTP Push.")
        # Mantém testes, uploads e auditoria da integração órfã, mas libera a
        # reconciliação com a conta FTP ativa atual.
        conn.execute(
            "UPDATE mikrotik_ftp_integrations SET is_active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (existing_integration["id"],),
        )
    account = conn.execute(
        "SELECT * FROM ftp_accounts WHERE equipment_id=? AND is_active=1 AND deleted_at IS NULL", (equipment_id,)
    ).fetchone()
    if credential_action not in {"reuse", "rotate", "isolated"}:
        raise ValueError("Ação de credencial FTP inválida.")
    if account and credential_action == "isolated":
        raise ValueError("O modelo atual permite apenas uma conta FTP por equipamento. Reutilize ou rotacione a conta existente.")
    if not account and credential_action in {"reuse", "rotate"}:
        raise ValueError("Este equipamento não possui conta FTP para reutilizar.")
    if account and credential_action == "reuse" and not account["is_active"]:
        raise ValueError("A conta FTP existente está inativa. Rotacione a senha para reativá-la e continuar.")
    if routeros_version not in {6, 7} or backup_format not in {"backup", "rsc", "both"}:
        raise ValueError("Versão ou formato inválido.")
    if schedule_mode not in {"manual", "scheduled"}:
        raise ValueError("Modo de execução inválido.")
    schedule_time = validate_time(schedule_time)
    if not ftp_host.strip() or not 1 <= int(ftp_port) <= 65535:
        raise ValueError("Servidor FTP inválido.")
    ftp_directory = validate_ftp_directory(ftp_directory)
    password = None
    encrypted = ""
    if account:
        reference = account["password_hash_or_secret_reference"] or ""
        if credential_action == "reuse" and reference.startswith("fernet:"):
            try:
                password = decrypt_secret(reference.removeprefix("fernet:"))
                encrypted = encrypt_secret(password)
            except SecretKeyError:
                password = None
        elif credential_action == "rotate":
            password = generate_password(28)
            encrypted = encrypt_secret(password)
            conn.execute(
                """UPDATE ftp_accounts SET password_hash_or_secret_reference=?,is_active=1,
                   sync_status='pending',updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (hash_password(password), account["id"]),
            )
    else:
        account, password, encrypted = create_isolated_credential(
            conn, equipment_id=equipment_id, equipment_name=equipment["name"] or equipment["hostname"], user_id=user_id
        )
    cursor = conn.execute(
        """INSERT INTO mikrotik_ftp_integrations
           (uuid,equipment_id,ftp_account_id,routeros_major_version,backup_format,schedule_mode,
            schedule_frequency,schedule_time,ftp_host,ftp_port,ftp_directory,encrypted_ftp_secret,retention_days)
           VALUES(?,?,?,?,?,?,'daily',?,?,?,?,?,?)""",
        (str(uuid.uuid4()), equipment_id, account["id"], routeros_version, backup_format, schedule_mode,
         schedule_time, ftp_host.strip(), int(ftp_port), ftp_directory, encrypted, retention_days),
    )
    integration = conn.execute("SELECT * FROM mikrotik_ftp_integrations WHERE id=?", (cursor.lastrowid,)).fetchone()
    return integration, account, password


def integration_config(conn, integration, *, include_secret: bool) -> ScriptConfig:
    account = conn.execute("SELECT username FROM ftp_accounts WHERE id=?", (integration["ftp_account_id"],)).fetchone()
    equipment = conn.execute("SELECT name,hostname FROM equipment WHERE id=?", (integration["equipment_id"],)).fetchone()
    password = decrypt_secret(integration["encrypted_ftp_secret"]) if include_secret else ""
    return ScriptConfig(
        device_id=integration["equipment_id"], device_name=equipment["name"] or equipment["hostname"],
        routeros_version=integration["routeros_major_version"], backup_format=integration["backup_format"],
        schedule_mode=integration["schedule_mode"], schedule_frequency=integration["schedule_frequency"],
        schedule_time=integration["schedule_time"], ftp_host=integration["ftp_host"], ftp_port=integration["ftp_port"],
        ftp_username=account["username"], ftp_password=password, ftp_directory=integration["ftp_directory"],
        execution_namespace=hashlib.sha256(integration["uuid"].encode("ascii")).hexdigest()[:12],
    )


def create_test(conn, integration_id: int, *, now: datetime | None = None):
    current = now or datetime.now(timezone.utc)
    integration = conn.execute("SELECT * FROM mikrotik_ftp_integrations WHERE id=? AND is_active=1", (integration_id,)).fetchone()
    if not integration:
        raise ValueError("Integração FTP Push inativa ou ausente.")
    active = conn.execute("""SELECT 1 FROM mikrotik_ftp_tests WHERE integration_id=?
        AND status IN ('pending','running','waiting_upload','validating') LIMIT 1""", (integration_id,)).fetchone()
    if active:
        raise ValueError("Já existe um teste FTP em andamento para este equipamento.")
    test_uuid = str(uuid.uuid4())
    expires = current + timedelta(minutes=TEST_TTL_MINUTES)
    cursor = conn.execute(
        """INSERT INTO mikrotik_ftp_tests(uuid,integration_id,token_hash,status,expires_at,started_at,expected_filename,last_check_at)
           VALUES(?,?,NULL,'waiting_upload',?,?,?,?)""",
        (test_uuid, integration_id, expires.strftime("%Y-%m-%d %H:%M:%S"), current.strftime("%Y-%m-%d %H:%M:%S"),
         f"backup-manager-test-{integration['equipment_id']}.rsc", current.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.execute("UPDATE mikrotik_ftp_integrations SET last_test_status='waiting_upload',last_test_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 (current.strftime("%Y-%m-%d %H:%M:%S"), integration_id))
    return conn.execute("SELECT * FROM mikrotik_ftp_tests WHERE id=?", (cursor.lastrowid,)).fetchone()


def expire_test(conn, test_id: int, *, now: datetime | None = None):
    current = now or datetime.now(timezone.utc)
    test = conn.execute("SELECT * FROM mikrotik_ftp_tests WHERE id=?", (test_id,)).fetchone()
    if not test:
        return None
    expires = datetime.fromisoformat(test["expires_at"].replace(" ", "T") + "+00:00")
    stamp = current.strftime("%Y-%m-%d %H:%M:%S")
    if test["status"] in ACTIVE_TEST_STATES and current >= expires:
        conn.execute("""UPDATE mikrotik_ftp_tests SET status='expired',error_code='upload_timeout',
                     error_message=?,completed_at=?,last_check_at=?,updated_at=?
                     WHERE id=? AND status IN ('pending','running','waiting_upload','validating')""",
                     (UPLOAD_TIMEOUT_MESSAGE, stamp, stamp, stamp, test_id))
        conn.execute("UPDATE mikrotik_ftp_integrations SET last_test_status='expired',updated_at=CURRENT_TIMESTAMP WHERE id=?", (test["integration_id"],))
        test = conn.execute("SELECT * FROM mikrotik_ftp_tests WHERE id=?", (test_id,)).fetchone()
    elif test["status"] in ACTIVE_TEST_STATES:
        conn.execute("UPDATE mikrotik_ftp_tests SET last_check_at=?,updated_at=? WHERE id=?", (stamp, stamp, test_id))
        test = conn.execute("SELECT * FROM mikrotik_ftp_tests WHERE id=?", (test_id,)).fetchone()
    return test


def active_test(conn, integration_id: int, now: datetime):
    row = conn.execute("""SELECT * FROM mikrotik_ftp_tests WHERE integration_id=?
        AND status IN ('pending','running','waiting_upload','validating') ORDER BY id DESC LIMIT 1""",
        (integration_id,)).fetchone()
    if not row:
        return None, False
    expires = datetime.fromisoformat(row["expires_at"].replace(" ", "T") + "+00:00")
    return row, now < expires


def equipment_snapshot(conn, equipment_id: int, *, artifacts_v2: bool = False) -> dict[str, object]:
    ftp_account = conn.execute(
        """SELECT * FROM ftp_accounts WHERE equipment_id=? AND deleted_at IS NULL
           ORDER BY is_active DESC,id DESC LIMIT 1""",
        (equipment_id,),
    ).fetchone()
    ftp_count = conn.execute(
        "SELECT COUNT(*) FROM backups WHERE equipment_id=? AND source_method='ftp' AND backup_status='available'",
        (equipment_id,),
    ).fetchone()[0]
    mikrotik_ftp = conn.execute(
        """SELECT i.*,a.username,a.sync_status,a.is_active AS account_is_active,
            a.deleted_at AS account_deleted_at FROM mikrotik_ftp_integrations i
            JOIN ftp_accounts a ON a.id=i.ftp_account_id
            WHERE i.equipment_id=? AND i.is_active=1 AND a.is_active=1 AND a.deleted_at IS NULL
            ORDER BY i.id DESC LIMIT 1""",
        (equipment_id,),
    ).fetchone()
    mikrotik_last_test = conn.execute(
        "SELECT * FROM mikrotik_ftp_tests WHERE integration_id=? ORDER BY id DESC LIMIT 1",
        (mikrotik_ftp["id"],),
    ).fetchone() if mikrotik_ftp else None
    mikrotik_last_upload = conn.execute(
        "SELECT * FROM mikrotik_ftp_uploads WHERE integration_id=? AND status='success' AND is_test=0 ORDER BY id DESC LIMIT 1",
        (mikrotik_ftp["id"],),
    ).fetchone() if mikrotik_ftp else None
    mikrotik_operation = conn.execute(
        "SELECT * FROM backup_operations WHERE provider_key='mikrotik_ftp' AND equipment_id=? ORDER BY id DESC LIMIT 1",
        (equipment_id,),
    ).fetchone() if mikrotik_ftp and artifacts_v2 else None
    mikrotik_artifacts = conn.execute(
        "SELECT * FROM backup_operation_artifacts WHERE operation_id=? ORDER BY artifact_type",
        (mikrotik_operation["id"],),
    ).fetchall() if mikrotik_operation else []
    mikrotik_install_event = conn.execute(
        """SELECT * FROM audit_log WHERE entity='mikrotik_ftp_integration' AND entity_id=?
            AND action IN ('mikrotik_ftp.ssh_installed','mikrotik_ftp.installation_checked','mikrotik_ftp.installation_repaired',
                           'mikrotik_ftp.ssh_install_failed','mikrotik_ftp.credential_rotated')
            ORDER BY id DESC LIMIT 1""",
        (mikrotik_ftp["uuid"],),
    ).fetchone() if mikrotik_ftp else None
    return {
        "ftp_account": ftp_account,
        "ftp_count": ftp_count,
        "mikrotik_ftp": mikrotik_ftp,
        "mikrotik_last_test": mikrotik_last_test,
        "mikrotik_last_upload": mikrotik_last_upload,
        "mikrotik_operation": mikrotik_operation,
        "mikrotik_artifacts": mikrotik_artifacts,
        "mikrotik_install_event": mikrotik_install_event,
    }


def _timeline(steps: list[str], failed_stage: int | None = None) -> list[dict[str, str]]:
    timeline: list[dict[str, str]] = []
    for index, step in enumerate(steps):
        if failed_stage is not None and index > failed_stage:
            status = "pending"
        elif failed_stage is not None and index == failed_stage:
            status = "failed"
        else:
            status = "done"
        timeline.append({"step": step, "status": status})
    return timeline


def install_integration_via_ssh(conn, integration_id: int, *, repair: bool = False,
                                installer=install_routeros_script) -> dict[str, object]:
    try:
        integration, equipment = require_mikrotik_integration(conn, integration_id)
    except ValueError as exc:
        message = str(exc)
        code = "integration_not_found" if message == "Integração FTP Push inativa ou ausente." else "equipment_inconsistent"
        return {"found": False, "equipment_id": None, "payload": {"ok": False, "status": "failed", "message": message, "error_code": code, "operation_id": None, "timeline": []}}
    credential = conn.execute("""SELECT * FROM device_credentials WHERE equipment_id=? AND credential_type='ssh'
        AND is_active=1 AND deleted_at IS NULL ORDER BY updated_at DESC,id DESC LIMIT 1""", (integration["equipment_id"],)).fetchone()
    connect_timeout = int(conn.execute("SELECT value FROM settings WHERE key='ssh_connect_timeout_seconds'").fetchone()[0] or 15)
    command_timeout = int(conn.execute("SELECT value FROM settings WHERE key='ssh_command_timeout_seconds'").fetchone()[0] or 60)
    try:
        if not equipment:
            raise SSHBackupError("SSH_CONNECTION_FAILED", "Equipamento inativo ou ausente.")
        if not credential:
            raise SSHBackupError("SSH_CREDENTIAL_MISSING")
        config = integration_config(conn, integration, include_secret=True)
        if not config.ftp_password:
            raise ValueError("A senha FTP não está disponível. Rotacione a credencial antes da instalação via SSH.")
        result = installer(dict(equipment), dict(credential), config, connect_timeout, command_timeout, repair=repair)
        detected = int(result["routeros_major"])
        state = result["preflight"]["state"]
        conn.execute("UPDATE mikrotik_ftp_integrations SET routeros_major_version=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (detected, integration_id))
        changed = bool(result.get("changed"))
        needs_repair = bool(result.get("needs_repair")) or state == "needs_repair"
        if needs_repair:
            status, installation_state = "needs_repair", "needs_repair"
            message_text = "A instalação está incompleta ou desatualizada. Use Reparar instalação."
        elif repair and changed:
            status, installation_state = "repaired", "installed"
            message_text = "Instalação reparada e validada com sucesso."
        elif not changed:
            status, installation_state = "installed_valid", "installed"
            message_text = ("Backup Manager já estava instalado e atualizado. Nenhuma reparação foi necessária."
                            if repair else "Backup Manager já está instalado e atualizado neste MikroTik. Nenhuma alteração foi necessária.")
        else:
            status, installation_state = "installed", "installed"
            message_text = "Instalação concluída. Agora teste o envio FTP para validar a integração."
        previous = result.get("preflight_before") or {}
        corrections: list[str] = []
        if repair and changed:
            if not previous.get("script_valid", False):
                corrections.append("Correção aplicada: script atualizado")
            if not previous.get("scheduler_valid", False):
                corrections.append("Correção aplicada: scheduler atualizado")
            if previous.get("state") == "duplicated":
                corrections.append("Correção aplicada: objetos duplicados removidos")
            if not corrections:
                corrections.append("Correção aplicada: script e scheduler atualizados")
        steps = (["Conexão SSH", "RouterOS detectado", "Verificando divergências", *corrections,
                  "Validando objetos", "Instalação reparada"]
                 if repair and changed else
                 ["Conexão SSH", "RouterOS detectado", "Verificação dos objetos",
                  "Script já instalado e válido" if not changed else "Script atualizado",
                  "Scheduler já instalado e válido" if not changed else "Scheduler atualizado",
                  "Alterações: nenhuma" if not changed else "Alterações aplicadas",
                  "Resultado: instalação já concluída" if not changed else "Instalação concluída"])
        timeline = _timeline(steps)
        details = {"installation_state": installation_state, "status": status, "message": message_text,
                   "preflight": state, "preflight_before": previous, "changed": changed,
                   "corrections": corrections, "routeros": detected, "timeline": timeline}
        payload = {"ok": not needs_repair, "status": status, "message": message_text, "operation_id": None, "timeline": timeline}
        return {"found": True, "equipment_id": integration["equipment_id"], "integration_uuid": integration["uuid"],
                "routeros_major_version": detected, "payload": payload, "details": details, "error_code": None}
    except (ValueError, SecretKeyError, SSHBackupError) as exc:
        raw_message = str(exc)
        if isinstance(exc, SSHBackupError) and (not raw_message or raw_message == exc.code):
            raw_message = ERROR_MESSAGES.get(exc.code, "Falha segura ao executar a operação no MikroTik.")
        if isinstance(exc, SSHBackupError) and equipment and credential:
            host = str(equipment["ip_address"] or "equipamento")
            port = int(credential["port"] or 22)
            username = str(credential["username"] or "usuário configurado")
            if exc.code == "SSH_CONNECTION_REFUSED":
                raw_message = (f"Conexão SSH recusada em {host}:{port}. Verifique a porta configurada, "
                               "se o serviço SSH está ativo no MikroTik e se o firewall permite o acesso.")
            elif exc.code == "SSH_TIMEOUT":
                raw_message = (f"O equipamento {host}:{port} não respondeu a tempo. Verifique endereço, "
                               "porta SSH, rota de rede e regras de firewall.")
            elif exc.code == "SSH_AUTH_FAILED":
                raw_message = (f"Autenticação SSH recusada para o usuário {username} em {host}:{port}. "
                               "Verifique o usuário e a senha SSH configurados.")
        message_text = sanitize_error(raw_message) or "Falha segura ao executar a operação no MikroTik."
        error_code = exc.code if isinstance(exc, SSHBackupError) else "validation_failed"
        failed_stage = {"connect": 0, "preflight": 1, "script": 2, "scheduler": 2, "verify": 3}.get(str(getattr(exc, "install_stage", "connect")), 0)
        timeline = _timeline(["Conexão SSH", "RouterOS detectado", "Verificação dos objetos", "Operação concluída"], failed_stage)
        details = {"installation_state": "unknown", "status": "failed", "message": message_text, "error_code": error_code, "timeline": timeline}
        payload = {"ok": False, "status": "failed", "message": message_text, "error_code": error_code, "operation_id": None, "timeline": timeline}
        return {"found": True, "equipment_id": integration["equipment_id"], "integration_uuid": integration["uuid"],
                "routeros_major_version": integration["routeros_major_version"], "payload": payload, "details": details, "error_code": error_code}
