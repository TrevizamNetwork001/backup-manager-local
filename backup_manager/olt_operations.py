from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from .artifacts import reduce_persisted_operation


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def create_operation(conn, *, equipment_id: int, expected_files: tuple[str, ...],
                     now: datetime | None = None, timeout_seconds: int = 180, job_run_id: int | None = None):
    if not expected_files or len(set(expected_files)) != len(expected_files):
        raise ValueError("Arquivos esperados da OLT inválidos.")
    now = now or datetime.now(timezone.utc)
    deadline = now + timedelta(seconds=timeout_seconds)
    correlation = uuid.uuid4().hex
    cursor = conn.execute(
        """INSERT INTO backup_operations(uuid,provider_key,equipment_id,job_run_id,correlation_key,source_method,status,deadline_at,started_at)
           VALUES(?, 'olt_ftp', ?, ?, ?, 'ftp', 'waiting_upload', ?, ?)""",
        (str(uuid.uuid4()), equipment_id, job_run_id, correlation, _stamp(deadline), _stamp(now)),
    )
    operation_id = cursor.lastrowid
    for index, filename in enumerate(expected_files, 1):
        conn.execute(
            """INSERT INTO backup_operation_artifacts
               (uuid,operation_id,artifact_type,is_required,state,filename,deadline_at)
               VALUES(?,?,?,1,'expected',?,?)""",
            (str(uuid.uuid4()), operation_id, f"file_{index}", filename, _stamp(deadline)),
        )
    return conn.execute("SELECT * FROM backup_operations WHERE id=?", (operation_id,)).fetchone()


def complete_received_file(conn, *, equipment_id: int, filename: str, backup_id: int | None,
                           size_bytes: int, sha256: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    artifact = conn.execute(
        """SELECT a.*,o.deadline_at,o.status operation_status FROM backup_operation_artifacts a
           JOIN backup_operations o ON o.id=a.operation_id
           WHERE o.provider_key='olt_ftp' AND o.equipment_id=? AND o.status IN ('waiting_upload','validating')
             AND a.filename=? AND a.state IN ('expected','waiting','received','validating')
           ORDER BY o.id DESC LIMIT 1""",
        (equipment_id, filename),
    ).fetchone()
    if not artifact:
        return False
    stamp = _stamp(now)
    deadline = datetime.fromisoformat(artifact["deadline_at"].replace(" ", "T") + "+00:00")
    state = "late" if now.astimezone(timezone.utc) >= deadline else "valid"
    conn.execute(
        """UPDATE backup_operation_artifacts SET backup_id=?,state=?,size_bytes=?,sha256=?,received_at=?,
           validated_at=?,completed_at=?,updated_at=? WHERE id=?""",
        (backup_id, state, size_bytes, sha256, stamp, stamp, stamp, stamp, artifact["id"]),
    )
    reduction, transitioned = reduce_persisted_operation(conn, artifact["operation_id"], now=now)
    if transitioned and reduction.status == "success":
        operation = conn.execute("SELECT job_run_id FROM backup_operations WHERE id=?", (artifact["operation_id"],)).fetchone()
        if operation and operation["job_run_id"]:
            conn.execute("""UPDATE backup_job_runs SET status='success',finished_at=?,created_backup_id=?,
                safe_log='Todos os arquivos esperados da OLT foram recebidos e validados.' WHERE id=? AND status='running'""",
                (stamp, backup_id, operation["job_run_id"]))
            conn.execute("""UPDATE backup_jobs SET last_run_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=(
                SELECT job_id FROM backup_job_runs WHERE id=?)""", (stamp, operation["job_run_id"]))
    return transitioned and reduction.status == "success"


def fail_operation(conn, operation_id: int, code: str, message: str) -> None:
    conn.execute(
        """UPDATE backup_operations SET status='failed',error_code=?,error_message=?,completed_at=CURRENT_TIMESTAMP,
           updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('waiting_upload','validating')""",
        (code[:80], message[:240], operation_id),
    )


def expire_due_operations(conn, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    stamp = _stamp(now)
    rows = conn.execute("""SELECT id,job_run_id FROM backup_operations WHERE provider_key='olt_ftp'
        AND status IN ('waiting_upload','validating') AND deadline_at<=?""", (stamp,)).fetchall()
    for row in rows:
        conn.execute("""UPDATE backup_operation_artifacts SET state='expired',validation_code='artifact_expired',
            validation_message='Arquivo não recebido dentro do prazo.',completed_at=?,updated_at=?
            WHERE operation_id=? AND state IN ('expected','waiting','received','validating')""",
            (stamp, stamp, row["id"]))
        conn.execute("""UPDATE backup_operations SET status='expired',error_code='required_artifact_expired',
            error_message='Um ou mais arquivos da OLT não foram recebidos.',completed_at=?,updated_at=? WHERE id=?""",
            (stamp, stamp, row["id"]))
        if row["job_run_id"]:
            conn.execute("""UPDATE backup_job_runs SET status='timeout',finished_at=?,error_code='FTP_FILE_NOT_RECEIVED',
                error_message='Arquivos esperados da OLT não foram recebidos.',safe_log='Prazo de recebimento FTP expirado.'
                WHERE id=? AND status='running'""", (stamp, row["job_run_id"]))
    return len(rows)
