from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from .artifacts import artifacts_v2_enabled, ensure_mikrotik_operation, reduce_persisted_operation, safe_validation_metadata
from .artifact_validators import RouterOSBinaryBackupValidator, RouterOSExportValidator
from .mikrotik_ftp_service import ACTIVE_TEST_STATES, active_test, expire_test, sanitize_error

IDENTITY = r"[a-z0-9_](?:[a-z0-9._-]{0,46}[a-z0-9_])?"
RUN_ID = r"x[a-f0-9]{12}-\d{14}-\d{1,10}"
REAL_NAME_RE = re.compile(rf"^backup\.({IDENTITY})\.(\d{{4}}-\d{{2}}-\d{{2}})\.(\d{{6}})(?:\.({RUN_ID}))?\.(backup|rsc)$")
SHORT_NAME_RE = re.compile(rf"^({IDENTITY})\.(\d{{4}}-\d{{2}}-\d{{2}})-(\d{{2}})-(\d{{2}})-(\d{{2}})\.(\d{{1,10}})\.(backup|rsc)$")
# v5 sends a RouterOS export; .txt remains accepted for in-flight legacy tests.
TEST_NAME_RE = re.compile(r"^backup-manager-test-(\d+)\.(?:rsc|txt)$")
MANAGED_PREFIX_RE = re.compile(r"^(?:backup\.|backup-manager-test-)")
LOOSE_BACKUP_RE = re.compile(rf"^backup\.({IDENTITY})\.([^.]+)\.([^.]+)\.(backup|rsc)$")


class UploadValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = sanitize_error(message)


class RetryableUploadValidationError(UploadValidationError):
    """Correlation/pairing may become valid in a later importer pass."""


class UnmanagedUpload(ValueError):
    pass


@dataclass(frozen=True)
class UploadMatch:
    upload_id: int
    integration_id: int
    test_id: int | None
    is_test: bool
    file_type: str
    operation_id: int | None = None
    artifact_id: int | None = None
    artifacts_v2: bool = False
    artifact_state: str = ""
    artifact_sha256: str | None = None
    artifact_backup_id: int | None = None


def begin_validation(conn, *, account_id: int, received_file_id: int, filename: str, size: int, now: datetime,
                     mtime_ns: int | None = None) -> UploadMatch | None:
    integration = conn.execute(
        "SELECT * FROM mikrotik_ftp_integrations WHERE ftp_account_id=? AND is_active=1", (account_id,)
    ).fetchone()
    if not integration:
        return None
    test_match = TEST_NAME_RE.fullmatch(filename)
    real_match = REAL_NAME_RE.fullmatch(filename)
    short_match = SHORT_NAME_RE.fullmatch(filename)
    if not test_match and not real_match and not short_match:
        if not MANAGED_PREFIX_RE.match(filename):
            raise UnmanagedUpload(filename)
        loose = LOOSE_BACKUP_RE.fullmatch(filename)
        if loose:
            try: datetime.strptime(loose.group(2), "%Y-%m-%d")
            except ValueError: raise UploadValidationError("date_invalid", "Data do backup inválida.") from None
            try: datetime.strptime(loose.group(3), "%H%M%S")
            except ValueError: raise UploadValidationError("time_invalid", "Hora do backup inválida.") from None
        raise UploadValidationError("filename_invalid", "Nome de arquivo gerenciado inválido.")
    file_type = "rsc" if test_match else (short_match.group(7) if short_match else real_match.group(5))
    if short_match:
        backup_date = short_match.group(2)
        backup_time = "".join(short_match.group(index) for index in (3, 4, 5))
        run_id = f"short-{backup_date.replace('-', '')}{backup_time}-{short_match.group(6)}"
    elif real_match:
        backup_date = real_match.group(2)
        backup_time = real_match.group(3)
        run_id = real_match.group(4) or f"legacy-{backup_date.replace('-', '')}{backup_time}"
    else:
        backup_date = backup_time = run_id = ""
    test_id = None
    is_test = bool(test_match)
    if test_match:
        if int(test_match.group(1)) != integration["equipment_id"]:
            raise UploadValidationError("account_mismatch", "Arquivo de teste pertence a outro equipamento.")
        test, valid = active_test(conn, integration["id"], now)
        if not test:
            latest = conn.execute("""SELECT * FROM mikrotik_ftp_tests WHERE integration_id=? AND expected_filename=?
                ORDER BY id DESC LIMIT 1""", (integration["id"], filename)).fetchone()
            if latest and latest["status"] in ACTIVE_TEST_STATES:
                expire_test(conn, latest["id"], now=now)
                raise UploadValidationError("late_test_file", "Arquivo de teste recebido depois do prazo; removido com segurança.")
            if latest and latest["status"] == "expired":
                raise UploadValidationError("late_test_file", "Arquivo de teste recebido depois do prazo; removido com segurança.")
            raise UnmanagedUpload(filename)
        if not valid:
            expire_test(conn, test["id"], now=now)
            raise UploadValidationError("late_test_file", "Arquivo de teste recebido depois do prazo; removido com segurança.")
        started = datetime.fromisoformat(test["started_at"].replace(" ", "T") + "+00:00").timestamp()
        if mtime_ns is not None and (mtime_ns / 1_000_000_000) < started:
            raise UnmanagedUpload(filename)
        test_id = test["id"]
    else:
        try:
            datetime.strptime(backup_date, "%Y-%m-%d")
        except ValueError:
            raise UploadValidationError("date_invalid", "Data do backup inválida.") from None
        try:
            datetime.strptime(backup_time, "%H%M%S")
        except ValueError:
            raise UploadValidationError("time_invalid", "Hora do backup inválida.") from None
    v2 = bool(not test_match and artifacts_v2_enabled(conn))
    if not test_match and integration["backup_format"] != "both" and integration["backup_format"] != file_type:
        raise UploadValidationError("extension_not_allowed", "Extensão não habilitada para esta integração.")
    elif not test_match and integration["backup_format"] == "both" and not v2:
        counterpart = filename.rsplit(".", 1)[0] + (".rsc" if file_type == "backup" else ".backup")
        incoming = conn.execute("SELECT incoming_relative_path FROM ftp_received_files WHERE ftp_account_id=? AND original_filename=? AND status IN ('waiting_stable','processing','imported') ORDER BY id DESC LIMIT 1", (account_id, counterpart)).fetchone()
        if not incoming:
            raise RetryableUploadValidationError("waiting_pair", "Aguardando o outro arquivo do par backup/export.")
    if size <= 0:
        raise UploadValidationError("size_invalid", "Arquivo vazio.")
    if not is_test and not v2 and conn.execute("SELECT 1 FROM mikrotik_ftp_uploads WHERE integration_id=? AND original_filename=?", (integration["id"], filename)).fetchone():
        raise UploadValidationError("duplicate", "Upload repetido.")
    operation = artifact = existing_upload = None
    if v2:
        correlation = f"i{integration['id']}:{run_id}"
        types = ("backup", "rsc") if integration["backup_format"] == "both" else (integration["backup_format"],)
        operation = ensure_mikrotik_operation(
            conn, provider_key="mikrotik_ftp", equipment_id=integration["equipment_id"],
            correlation_key=correlation, artifact_types=types, deadline=now + timedelta(seconds=300), now=now,
        )
        artifact = conn.execute(
            "SELECT * FROM backup_operation_artifacts WHERE operation_id=? AND artifact_type=?",
            (operation["id"], file_type),
        ).fetchone()
        if not artifact:
            raise UploadValidationError("artifact_not_expected", "Tipo de artefato não esperado.")
        existing_upload = conn.execute(
            "SELECT * FROM mikrotik_ftp_uploads WHERE integration_id=? AND original_filename=? ORDER BY id LIMIT 1",
            (integration["id"], filename),
        ).fetchone()
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        if artifact["state"] not in {"valid", "late", "expired"}:
            conn.execute(
                """UPDATE backup_operation_artifacts SET state='received',filename=?,size_bytes=?,received_at=?,updated_at=?
                   WHERE id=?""", (filename, size, stamp, stamp, artifact["id"]),
            )
            conn.execute("UPDATE backup_operation_artifacts SET state='validating',updated_at=? WHERE id=?", (stamp, artifact["id"]))
            reduce_persisted_operation(conn, operation["id"], now=now)
        if existing_upload:
            return UploadMatch(existing_upload["id"], integration["id"], None, False, file_type,
                               operation["id"], artifact["id"], True, artifact["state"],
                               artifact["sha256"], artifact["backup_id"])
    cursor = conn.execute(
        """INSERT INTO mikrotik_ftp_uploads
           (uuid,integration_id,equipment_id,test_id,received_file_id,original_filename,file_type,size_bytes,
            status,is_test,routeros_major_version,backup_format,received_at)
           VALUES(?,?,?,?,?,?,?,?, 'validating',?,?,?,?)""",
        (str(uuid.uuid4()), integration["id"], integration["equipment_id"], test_id, received_file_id,
         filename, file_type, size, int(is_test), integration["routeros_major_version"], integration["backup_format"],
         now.strftime("%Y-%m-%d %H:%M:%S")),
    )
    if test_id:
        conn.execute("UPDATE mikrotik_ftp_tests SET status='validating',received_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (now.strftime("%Y-%m-%d %H:%M:%S"), test_id))
    return UploadMatch(cursor.lastrowid, integration["id"], test_id, is_test, file_type,
                       operation["id"] if operation else None, artifact["id"] if artifact else None,
                       v2, artifact["state"] if artifact else "", artifact["sha256"] if artifact else None,
                       artifact["backup_id"] if artifact else None)


def validate_artifact(match: UploadMatch, path, *, expected_sha256: str | None = None):
    validator = RouterOSExportValidator() if match.file_type == "rsc" else RouterOSBinaryBackupValidator()
    return validator.validate(path, expected_sha256=expected_sha256)


def reject_artifact(conn, match: UploadMatch, result, *, now: datetime) -> None:
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """UPDATE backup_operation_artifacts SET state=?,size_bytes=?,sha256=?,validation_code=?,
           validation_message=?,validation_metadata_json=?,validated_at=?,completed_at=?,updated_at=? WHERE id=?""",
        (result.state, result.size_bytes, result.sha256, result.code, result.message,
         safe_validation_metadata(result), stamp, stamp, stamp, match.artifact_id),
    )
    reduce_persisted_operation(conn, match.operation_id, now=now)


def artifact_duplicate_kind(conn, match: UploadMatch, digest: str) -> str:
    current = conn.execute("SELECT * FROM backup_operation_artifacts WHERE id=?", (match.artifact_id,)).fetchone()
    if current["backup_id"] and current["sha256"] == digest and current["state"] in {"valid", "late"}:
        return "idempotent"
    if current["backup_id"] and current["sha256"] != digest:
        return "conflict"
    # Identical content in a different operation is a normal new logical
    # artifact (for example an unchanged textual export).
    return "new"


def complete_artifact(conn, match: UploadMatch, result, *, backup_id: int, filename: str,
                      now: datetime) -> tuple[str, bool]:
    operation = conn.execute("SELECT * FROM backup_operations WHERE id=?", (match.operation_id,)).fetchone()
    deadline = datetime.fromisoformat(operation["deadline_at"].replace(" ", "T") + "+00:00")
    late = operation["status"] == "expired" or now >= deadline
    state = "late" if late else "valid"
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """UPDATE backup_operation_artifacts SET backup_id=?,state=?,filename=?,size_bytes=?,sha256=?,
           validation_code=?,validation_message=?,validation_metadata_json=?,validated_at=?,completed_at=?,updated_at=?
           WHERE id=?""",
        (backup_id, state, filename, result.size_bytes, result.sha256, result.code, result.message,
         safe_validation_metadata(result), stamp, stamp, stamp, match.artifact_id),
    )
    reduction, transitioned = reduce_persisted_operation(conn, match.operation_id, now=now)
    return reduction.status, transitioned


def mark_artifact_conflict(conn, match: UploadMatch, *, digest: str, now: datetime) -> None:
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """UPDATE backup_operation_artifacts SET state='suspicious',validation_code='artifact_content_conflict',
           validation_message='Conteúdo conflitante para o mesmo artefato.',validated_at=?,completed_at=?,updated_at=?
           WHERE id=?""", (stamp, stamp, stamp, match.artifact_id),
    )
    reduce_persisted_operation(conn, match.operation_id, now=now)


def complete_upload(conn, match: UploadMatch, *, digest: str, stored_filename: str, final_path: str,
                    backup_id: int | None, now: datetime, aggregate_success: bool = True) -> None:
    if not match.is_test and not match.artifacts_v2:
        duplicate = conn.execute(
            "SELECT id FROM mikrotik_ftp_uploads WHERE integration_id=? AND sha256=? AND id!=?",
            (match.integration_id, digest, match.upload_id),
        ).fetchone()
        if duplicate:
            conn.execute("UPDATE mikrotik_ftp_uploads SET status='duplicate',sha256=?,error_code='duplicate_content',error_message='Conteúdo já recebido.',updated_at=CURRENT_TIMESTAMP WHERE id=?", (digest, match.upload_id))
            raise UploadValidationError("duplicate", "Conteúdo já recebido.")
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    upload_digest = None if match.artifacts_v2 else digest
    conn.execute(
        """UPDATE mikrotik_ftp_uploads SET status='success',sha256=?,stored_filename=?,final_path=?,backup_id=?,
           validated_at=?,completed_at=?,temporary_path=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
        (upload_digest, stored_filename, final_path, backup_id, stamp, stamp, match.upload_id),
    )
    if aggregate_success:
        conn.execute("UPDATE mikrotik_ftp_integrations SET last_upload_at=?,last_success_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (stamp, stamp, match.integration_id))
    else:
        conn.execute("UPDATE mikrotik_ftp_integrations SET last_upload_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (stamp, match.integration_id))
    if match.test_id:
        conn.execute("""UPDATE mikrotik_ftp_tests SET status='validated',validated_at=?,completed_at=?,error_code=NULL,error_message=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status!='expired'""",
                     (stamp, stamp, match.test_id))
        conn.execute("UPDATE mikrotik_ftp_integrations SET last_test_status='validated',last_test_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (stamp, match.integration_id))


def fail_upload(conn, match: UploadMatch | None, code: str, message: str) -> None:
    if not match:
        return
    safe = sanitize_error(message)
    conn.execute("""UPDATE mikrotik_ftp_uploads SET status='invalid_file',error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND status!='success'""", (code, safe, match.upload_id))
    if match.test_id:
        conn.execute("UPDATE mikrotik_ftp_tests SET status='failed',error_code=?,error_message=?,completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status!='expired'",
                     (code, safe, match.test_id))
        conn.execute("UPDATE mikrotik_ftp_integrations SET last_test_status='failed',updated_at=CURRENT_TIMESTAMP WHERE id=?", (match.integration_id,))
