from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .db import SchemaCompatibilityError, connect, require_schema_current
from .artifacts import artifacts_v2_enabled, expire_due_operations
from .ftp import account_paths
from .ftp_pipeline import TEMP_SUFFIXES, discover, stabilization, validate_file
from .ftp_events import dispatch_backup_created
from .ftp_storage import store_backup
from .ftp_correlation import correlate
from .mikrotik_ftp_uploads import (
    UploadValidationError,
    TEST_NAME_RE,
    complete_upload as complete_mikrotik_upload,
    fail_upload as fail_mikrotik_upload,
    validate_artifact as validate_mikrotik_artifact,
    reject_artifact as reject_mikrotik_artifact,
    artifact_duplicate_kind,
    complete_artifact,
    mark_artifact_conflict,
)
from .mikrotik_ftp_service import expire_test
from .olt_operations import complete_received_file as complete_olt_received_file, expire_due_operations as expire_due_olt_operations
from .storage import atomic_move, load_config, safe_filename, sha256_file

@dataclass
class ScanResult:
    detected: int = 0
    waiting: int = 0
    waiting_pair: int = 0
    imported: int = 0
    validated_test: int = 0
    ignored_unmanaged: int = 0
    rejected: int = 0
    failed: int = 0
    expired_tests: int = 0


def _audit(conn, action: str, account, filename: str, details: dict) -> None:
    safe = {"account_uuid": account["uuid"], "equipment_id": account["equipment_id"], "filename": filename, **details}
    conn.execute("INSERT INTO audit_log(user_id, action, entity, entity_id, details, ip_address) VALUES(NULL, ?, 'ftp_received_file', ?, ?, '')",
                 (action, account["uuid"], json.dumps(safe, separators=(",", ":"))))


def _reject(conn, account, received, source: Path, code: str, message: str, result: ScanResult) -> None:
    target = account_paths(conn, account["uuid"]).rejected / f"{received['uuid']}-{source.name}"
    try:
        if source.exists() and not source.is_symlink():
            atomic_move(source, target)
    except OSError:
        pass
    conn.execute("UPDATE ftp_received_files SET status='rejected', error_code=?, error_message=?, processed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 (code, message[:240], received["id"]))
    _audit(conn, "ftp.upload_rejected", account, received["original_filename"], {"error_code": code})
    result.rejected += 1


def scan_once(conn=None, *, now: datetime | None = None) -> ScanResult:
    own = conn is None
    conn = conn or connect()
    result = ScanResult()
    current = now or datetime.now(timezone.utc)
    try:
        expired = conn.execute("""SELECT id FROM mikrotik_ftp_tests
            WHERE status IN ('pending','running','waiting_upload','validating') AND expires_at<=?""",
            (current.strftime("%Y-%m-%d %H:%M:%S"),)).fetchall()
        for row in expired:
            if expire_test(conn, row["id"], now=current)["status"] == "expired":
                result.expired_tests += 1
        if artifacts_v2_enabled(conn):
            expire_due_operations(conn, now=current)
            expire_due_olt_operations(conn, now=current)
        config = load_config(conn)
        stable_seconds = int(conn.execute("SELECT value FROM settings WHERE key='ftp_stable_seconds'").fetchone()[0])
        max_size = int(conn.execute("SELECT value FROM settings WHERE key='ftp_max_upload_size'").fetchone()[0])
        accounts = conn.execute("""SELECT ftp_accounts.*, equipment.is_active AS equipment_active
                                   FROM ftp_accounts JOIN equipment ON equipment.id=ftp_accounts.equipment_id
                                   WHERE ftp_accounts.is_active=1 AND ftp_accounts.deleted_at IS NULL""").fetchall()
        for account in accounts:
            paths = account_paths(conn, account["uuid"])
            paths.incoming.mkdir(parents=True, exist_ok=True, mode=0o750)
            for source in discover(paths.incoming):
                incoming_relative_path = source.relative_to(paths.incoming).as_posix()
                existing = conn.execute("SELECT * FROM ftp_received_files WHERE ftp_account_id=? AND incoming_relative_path=? AND status IN ('detected','waiting_stable','processing') ORDER BY id DESC LIMIT 1",
                                        (account["id"], incoming_relative_path)).fetchone()
                try:
                    info = source.lstat()
                except OSError:
                    continue
                if not existing:
                    rid = str(uuid.uuid4())
                    cursor = conn.execute("""INSERT INTO ftp_received_files
                        (uuid,ftp_account_id,equipment_id,original_filename,incoming_relative_path,status,file_size,observed_size,observed_mtime_ns)
                        VALUES(?,?,?,?,?,'waiting_stable',?,?,?)""",
                        (rid, account["id"], account["equipment_id"], source.name[:255], incoming_relative_path, info.st_size, info.st_size, info.st_mtime_ns))
                    existing = conn.execute("SELECT * FROM ftp_received_files WHERE id=?", (cursor.lastrowid,)).fetchone()
                    _audit(conn, "ftp.upload_detected", account, source.name[:255], {"size": info.st_size})
                    result.detected += 1
                    continue
                stability = stabilization(existing, info, current=current, stable_seconds=stable_seconds)
                if stability.state == "changed":
                    conn.execute("UPDATE ftp_received_files SET observed_size=?, observed_mtime_ns=?, file_size=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                 (stability.size, stability.mtime_ns, stability.size, existing["id"]))
                    result.waiting += 1
                    continue
                if stability.state == "waiting":
                    result.waiting += 1
                    continue
                issue = validate_file(info, max_size=max_size, account_quota=account["quota_bytes"])
                if issue:
                    _reject(conn, account, existing, source, issue.code, issue.message, result); continue
                imported_usage = conn.execute("SELECT COUNT(*),COALESCE(SUM(file_size),0) FROM ftp_received_files WHERE ftp_account_id=? AND status='imported'", (account["id"],)).fetchone()
                if account["max_files"] and imported_usage[0] >= account["max_files"]:
                    _reject(conn, account, existing, source, "max_files_exceeded", "Limite de arquivos da conta atingido.", result); continue
                if account["quota_bytes"] and imported_usage[1] + info.st_size > account["quota_bytes"]:
                    _reject(conn, account, existing, source, "quota_exceeded", "Quota da conta atingida.", result); continue
                if not account["equipment_active"]:
                    _reject(conn, account, existing, source, "equipment_inactive", "Equipamento inativo.", result); continue
                try:
                    original = source.name if TEST_NAME_RE.fullmatch(source.name) else safe_filename(source.name)
                except ValueError:
                    _reject(conn, account, existing, source, "invalid_filename", "Nome ou extensao nao permitida.", result); continue
                claim = conn.execute("""UPDATE ftp_received_files SET status='processing',stable_at=CURRENT_TIMESTAMP,
                                     updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='waiting_stable'""",
                                     (existing["id"],))
                if claim.rowcount != 1:
                    result.waiting += 1
                    continue
                # Publish ownership before correlation and hashing. Other importer
                # processes may observe the row, but cannot move/store this file.
                conn.commit()
                correlation = correlate(
                    conn, account_id=account["id"], received=existing, filename=original,
                    size=info.st_size, current=current, mtime_ns=info.st_mtime_ns,
                )
                if correlation.action == "unmanaged":
                    conn.execute("UPDATE ftp_received_files SET status='waiting_stable',error_code=?,error_message=? WHERE id=?",
                                 (correlation.code, correlation.message, existing["id"]))
                    result.ignored_unmanaged += 1
                    continue
                if correlation.action == "waiting_pair":
                    conn.execute("UPDATE ftp_received_files SET status='waiting_stable',error_code=?,error_message=? WHERE id=?",
                                 (correlation.code, correlation.message, existing["id"]))
                    _audit(conn, "ftp.upload_waiting_pair", account, original, {"reason": correlation.code})
                    result.waiting += 1
                    result.waiting_pair += 1
                    continue
                if correlation.action == "reject":
                    _reject(conn, account, existing, source, correlation.code, correlation.message, result)
                    continue
                mikrotik_match = correlation.match
                processing = paths.processing / f"{existing['uuid']}-{original}"
                target = None
                storage_savepoint = False
                try:
                    atomic_move(source, processing)
                    if mikrotik_match and mikrotik_match.artifacts_v2:
                        # Persist correlation before bounded validation; do not hold a write
                        # transaction while reading and hashing the artifact.
                        conn.commit()
                    digest = sha256_file(processing)
                    if mikrotik_match and mikrotik_match.is_test:
                        complete_mikrotik_upload(
                            conn, mikrotik_match, digest=digest, stored_filename="",
                            final_path="", backup_id=None, now=current,
                        )
                        processing.unlink(missing_ok=True)
                        conn.execute("UPDATE ftp_received_files SET status='imported',file_size=?,sha256=?,processed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                     (info.st_size, digest, existing["id"]))
                        conn.execute("UPDATE ftp_accounts SET last_upload_at=CURRENT_TIMESTAMP,last_upload_filename=?,last_upload_status='imported',updated_at=CURRENT_TIMESTAMP WHERE id=?", (original, account["id"]))
                        _audit(conn, "ftp.mikrotik_test_validated", account, original, {"size": info.st_size, "sha256": digest[:12]})
                        result.imported += 1
                        result.validated_test += 1
                        continue
                    validation = None
                    duplicate_kind = "new"
                    if mikrotik_match and mikrotik_match.artifacts_v2:
                        validation = validate_mikrotik_artifact(mikrotik_match, processing, expected_sha256=digest)
                        if validation.state in {"invalid", "suspicious"}:
                            reject_mikrotik_artifact(conn, mikrotik_match, validation, now=current)
                            raise UploadValidationError(validation.code, validation.message)
                        duplicate_kind = artifact_duplicate_kind(conn, mikrotik_match, digest)
                        if duplicate_kind == "idempotent":
                            processing.unlink(missing_ok=True)
                            conn.execute("""UPDATE ftp_received_files SET status='duplicate',file_size=?,sha256=?,backup_id=?,
                                         error_code='idempotent_retry',error_message='Artefato já importado.',processed_at=CURRENT_TIMESTAMP,
                                         updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                                         (info.st_size, digest, mikrotik_match.artifact_backup_id, existing["id"]))
                            continue
                        if duplicate_kind == "conflict":
                            mark_artifact_conflict(conn, mikrotik_match, digest=digest, now=current)
                            raise UploadValidationError("artifact_content_conflict", "Conteúdo conflitante para o mesmo artefato.")
                    if mikrotik_match and mikrotik_match.artifacts_v2:
                        conn.execute("SAVEPOINT mikrotik_artifact_storage")
                        storage_savepoint = True
                    stored = store_backup(
                        conn, equipment_id=account["equipment_id"], source=processing,
                        original_filename=original, file_size=info.st_size, digest=digest,
                        received_at=current, notes=f"Recebido pela conta FTP {account['uuid']}",
                    )
                    backup_id, backup_uuid, relative = stored.id, stored.uuid, stored.relative_path
                    target = config.backup_directory / relative
                    aggregate_status = "success"
                    became_success = True
                    if mikrotik_match and mikrotik_match.artifacts_v2:
                        aggregate_status, became_success = complete_artifact(
                            conn, mikrotik_match, validation, backup_id=backup_id, filename=original, now=current,
                        )
                    if mikrotik_match:
                        complete_mikrotik_upload(
                            conn, mikrotik_match, digest=digest, stored_filename=stored.filename,
                            final_path=relative, backup_id=backup_id, now=current,
                            aggregate_success=aggregate_status == "success",
                        )
                    conn.execute("UPDATE ftp_received_files SET status='imported',file_size=?,sha256=?,backup_id=?,processed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                 (info.st_size, digest, backup_id, existing["id"]))
                    conn.execute("UPDATE ftp_accounts SET last_upload_at=CURRENT_TIMESTAMP,last_upload_filename=?,last_upload_status='imported',updated_at=CURRENT_TIMESTAMP WHERE id=?", (original, account["id"]))
                    complete_olt_received_file(
                        conn, equipment_id=account["equipment_id"], filename=original, backup_id=backup_id,
                        size_bytes=info.st_size, sha256=digest, now=current,
                    )
                    _audit(conn, "ftp.upload_imported", account, original, {"size": info.st_size, "sha256": digest[:12], "backup_uuid": backup_uuid})
                    if not mikrotik_match or not mikrotik_match.artifacts_v2:
                        notification_backups = [(backup_id, backup_uuid, original, info.st_size, digest)]
                    elif became_success:
                        notification_backups = [tuple(row) for row in conn.execute(
                            """SELECT b.id,b.uuid,b.original_filename,b.file_size,b.sha256
                               FROM backup_operation_artifacts a JOIN backups b ON b.id=a.backup_id
                               WHERE a.operation_id=? AND a.state='valid' ORDER BY a.id""",
                            (mikrotik_match.operation_id,),
                        ).fetchall()]
                    else:
                        notification_backups = []
                    if notification_backups:
                        dispatch_backup_created(
                            conn, notification_backups,
                            audit_created=lambda filename, details: _audit(
                                conn, "backup.created_from_ftp", account, filename, details),
                        )
                    if storage_savepoint:
                        conn.execute("RELEASE SAVEPOINT mikrotik_artifact_storage")
                        storage_savepoint = False
                    result.imported += 1
                except UploadValidationError as exc:
                    fail_mikrotik_upload(conn, mikrotik_match, exc.code, exc.safe_message)
                    if processing.exists():
                        _reject(conn, account, existing, processing, exc.code, exc.safe_message, result)
                    else:
                        conn.execute("UPDATE ftp_received_files SET status='rejected',error_code=?,error_message=?,processed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                     (exc.code, exc.safe_message, existing["id"]))
                        result.rejected += 1
                except Exception:
                    if storage_savepoint:
                        conn.execute("ROLLBACK TO SAVEPOINT mikrotik_artifact_storage")
                        conn.execute("RELEASE SAVEPOINT mikrotik_artifact_storage")
                        storage_savepoint = False
                        if target and target.exists() and not processing.exists():
                            try:
                                atomic_move(target, processing)
                            except OSError:
                                pass
                    fail_mikrotik_upload(conn, mikrotik_match, "import_failed", "Falha interna ao importar.")
                    conn.execute("UPDATE ftp_received_files SET status='failed',error_code='import_failed',error_message='Falha interna ao importar.',processed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?", (existing["id"],))
                    _audit(conn, "ftp.upload_failed", account, original, {"error_code": "import_failed"})
                    result.failed += 1
        conn.commit()
        return result
    finally:
        if own:
            conn.close()


def main() -> None:
    try:
        require_schema_current()
    except SchemaCompatibilityError as exc:
        print(exc.code)
        raise SystemExit(3)
    result = scan_once()
    print(f"detected={result.detected} waiting_pair={result.waiting_pair} waiting={result.waiting} imported={result.imported} validated_test={result.validated_test} expired_tests={result.expired_tests} ignored_unmanaged={result.ignored_unmanaged} rejected={result.rejected} failed={result.failed}")


if __name__ == "__main__":
    main()
