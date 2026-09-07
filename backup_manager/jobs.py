from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

JOB_METHODS = ("manual", "ssh", "ftp", "sftp", "tftp", "api", "dry_run")
JOB_STATUSES = ("active", "paused", "disabled", "deleted")
JOB_TYPES = ("manual", "scheduled", "test")
RUN_STATUSES = ("queued", "running", "success", "failed", "cancelled", "timeout", "skipped")
TRIGGER_TYPES = ("manual", "scheduled", "system", "retry")
SCHEDULE_TYPES = ("none", "daily", "weekly")
DRY_RUN_SUCCESS_LOG = "Execucao dry-run concluida. Nenhuma conexao externa foi realizada."
SIMULATED_FAILURE_CODE = "DRY_RUN_SIMULATED_FAILURE"
SIMULATED_FAILURE_MESSAGE = "Falha simulada para teste do motor de jobs."


@dataclass(frozen=True)
class WorkerResult:
    due_jobs: int = 0
    queued_runs: int = 0
    processed_runs: int = 0
    success_runs: int = 0
    failed_runs: int = 0
    timeout_runs: int = 0
    skipped_runs: int = 0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def dt_to_db(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def db_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def safe_tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def parse_schedule_time(value: str | None) -> dt_time:
    if not value:
        return dt_time(0, 0)
    parts = value.split(":", 1)
    hour = max(0, min(23, int(parts[0])))
    minute = max(0, min(59, int(parts[1] if len(parts) > 1 else 0)))
    return dt_time(hour, minute)


def parse_schedule_days(value: str | None) -> set[int]:
    days: set[int] = set()
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            day = int(part)
        except ValueError:
            continue
        if 0 <= day <= 6:
            days.add(day)
    return days


def calculate_next_run(schedule_type: str, schedule_time: str, schedule_days: str, tz_name: str, after: datetime | None = None) -> str | None:
    if schedule_type not in ("daily", "weekly"):
        return None
    tz = safe_tz(tz_name)
    base = (after or utc_now()).astimezone(tz)
    target_time = parse_schedule_time(schedule_time)
    if schedule_type == "daily":
        candidate = datetime.combine(base.date(), target_time, tz)
        if candidate <= base:
            candidate += timedelta(days=1)
        return dt_to_db(candidate)
    days = parse_schedule_days(schedule_days)
    if not days:
        days = {base.weekday()}
    for offset in range(0, 8):
        day = base + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        candidate = datetime.combine(day.date(), target_time, tz)
        if candidate > base:
            return dt_to_db(candidate)
    return dt_to_db(datetime.combine((base + timedelta(days=7)).date(), target_time, tz))


def audit_event(conn, user_id: int | None, action: str, entity: str, entity_id: object = "", details: dict | None = None) -> None:
    conn.execute(
        """
        INSERT INTO audit_log(user_id, action, entity, entity_id, details, ip_address)
        VALUES (?, ?, ?, ?, ?, '')
        """,
        (user_id, action, entity, str(entity_id), json.dumps(details or {}, separators=(",", ":"))),
    )


def job_details(row, run=None, status: str | None = None) -> dict:
    details = {
        "equipment_id": row["equipment_id"],
        "job_uuid": row["uuid"],
        "method": row["method"],
        "status": status or row["status"],
    }
    if run is not None:
        details["run_uuid"] = run["uuid"]
        details["trigger"] = run["trigger_type"]
        details["run_status"] = run["status"]
    return details


def acquire_lock(conn, lock_key: str, locked_by: str, ttl_seconds: int, metadata: str = "") -> bool:
    now = dt_to_db(utc_now())
    expires = dt_to_db(utc_now() + timedelta(seconds=ttl_seconds))
    conn.execute("DELETE FROM backup_worker_locks WHERE expires_at <= ?", (now,))
    try:
        conn.execute(
            """
            INSERT INTO backup_worker_locks(lock_key, locked_by, locked_at, expires_at, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (lock_key, locked_by, now, expires, metadata[:500]),
        )
        return True
    except Exception:
        return False


def release_lock(conn, lock_key: str, locked_by: str) -> None:
    conn.execute("DELETE FROM backup_worker_locks WHERE lock_key = ? AND locked_by = ?", (lock_key, locked_by))


def create_job(conn, equipment_id: int, user_id: int | None, job_type: str = "manual", method: str = "dry_run",
               schedule_enabled: bool = False, schedule_type: str = "none", schedule_time: str = "",
               schedule_days: str = "", timezone_name: str = "UTC", notes: str = ""):
    if method not in ("manual", "dry_run", "ssh", "ftp"):
        raise ValueError("Metodo de job invalido.")
    if job_type not in JOB_TYPES or schedule_type not in SCHEDULE_TYPES:
        raise ValueError("Configuracao de job invalida.")
    if schedule_type == "none":
        schedule_enabled = False
    next_run = calculate_next_run(schedule_type, schedule_time, schedule_days, timezone_name) if schedule_enabled else None
    job_uuid = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO backup_jobs(uuid, equipment_id, job_type, method, schedule_enabled, schedule_type,
                                schedule_time, schedule_days, timezone, status, next_run_at,
                                created_by_user_id, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        """,
        (job_uuid, equipment_id, job_type, method, int(schedule_enabled), schedule_type, schedule_time, schedule_days, timezone_name, next_run, user_id, notes[:500]),
    )
    row = conn.execute("SELECT * FROM backup_jobs WHERE uuid = ?", (job_uuid,)).fetchone()
    audit_event(conn, user_id, "job.created", "backup_job", job_uuid, job_details(row))
    return row


def update_job(conn, job_id: int, user_id: int | None, method: str, schedule_enabled: bool, schedule_type: str,
               schedule_time: str, schedule_days: str, timezone_name: str, notes: str):
    if method not in ("manual", "dry_run", "ssh", "ftp"):
        raise ValueError("Metodo de job invalido.")
    if schedule_type not in SCHEDULE_TYPES:
        raise ValueError("Tipo de agendamento invalido.")
    if schedule_type == "none":
        schedule_enabled = False
    next_run = calculate_next_run(schedule_type, schedule_time, schedule_days, timezone_name) if schedule_enabled else None
    conn.execute(
        """
        UPDATE backup_jobs
        SET method = ?, schedule_enabled = ?, schedule_type = ?, schedule_time = ?,
            schedule_days = ?, timezone = ?, next_run_at = ?, notes = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status != 'deleted'
        """,
        (method, int(schedule_enabled), schedule_type, schedule_time, schedule_days, timezone_name, next_run, notes[:500], job_id),
    )
    row = conn.execute("SELECT * FROM backup_jobs WHERE id = ?", (job_id,)).fetchone()
    if row:
        audit_event(conn, user_id, "job.updated", "backup_job", row["uuid"], job_details(row))
    return row


def queue_run(conn, job_id: int, trigger_type: str, user_id: int | None = None):
    job = conn.execute(
        """
        SELECT backup_jobs.*, equipment.is_active AS equipment_active
        FROM backup_jobs JOIN equipment ON equipment.id = backup_jobs.equipment_id
        WHERE backup_jobs.id = ? AND backup_jobs.status != 'deleted'
        """,
        (job_id,),
    ).fetchone()
    if not job:
        raise ValueError("Job nao encontrado.")
    if job["status"] != "active":
        raise ValueError("Job nao esta ativo.")
    if not job["equipment_active"]:
        raise ValueError("Equipamento inativo.")
    existing = conn.execute(
        """
        SELECT id FROM backup_job_runs
        WHERE status IN ('queued', 'running') AND (job_id = ? OR equipment_id = ?)
        LIMIT 1
        """,
        (job_id, job["equipment_id"]),
    ).fetchone()
    if existing:
        return None
    run_uuid = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO backup_job_runs(uuid, job_id, equipment_id, trigger_type, method, status, created_by_user_id)
        VALUES (?, ?, ?, ?, ?, 'queued', ?)
        """,
        (run_uuid, job_id, job["equipment_id"], trigger_type, job["method"], user_id),
    )
    run = conn.execute("SELECT * FROM backup_job_runs WHERE uuid = ?", (run_uuid,)).fetchone()
    if trigger_type == "manual":
        audit_event(conn, user_id, "job.manual_queued", "backup_job_run", run_uuid, job_details(job, run))
    return run


def due_jobs(conn, now: datetime | None = None):
    current = dt_to_db(now or utc_now())
    return conn.execute(
        """
        SELECT backup_jobs.*, equipment.hostname, equipment.is_active AS equipment_active
        FROM backup_jobs JOIN equipment ON equipment.id = backup_jobs.equipment_id
        WHERE backup_jobs.status = 'active'
          AND backup_jobs.schedule_enabled = 1
          AND backup_jobs.next_run_at IS NOT NULL
          AND backup_jobs.next_run_at <= ?
          AND equipment.is_active = 1
        ORDER BY backup_jobs.next_run_at ASC, backup_jobs.id ASC
        """,
        (current,),
    ).fetchall()


def mark_timeouts(conn, worker_id: str, timeout_seconds: int) -> int:
    cutoff = dt_to_db(utc_now() - timedelta(seconds=timeout_seconds))
    rows = conn.execute("SELECT * FROM backup_job_runs WHERE status = 'running' AND started_at <= ?", (cutoff,)).fetchall()
    for run in rows:
        conn.execute(
            """
            UPDATE backup_job_runs
            SET status = 'timeout', finished_at = CURRENT_TIMESTAMP,
                duration_ms = CAST((julianday(CURRENT_TIMESTAMP) - julianday(started_at)) * 86400000 AS INTEGER),
                error_code = 'RUN_TIMEOUT', error_message = 'Execucao excedeu o timeout configurado.',
                safe_log = 'Execucao marcada como timeout pelo worker.'
            WHERE id = ?
            """,
            (run["id"],),
        )
        audit_event(conn, None, "job.run_timeout", "backup_job_run", run["uuid"], {"run_uuid": run["uuid"], "status": "timeout", "worker_id": worker_id})
    return len(rows)


def enqueue_due_jobs(conn, user_id: int | None = None) -> int:
    count = 0
    for job in due_jobs(conn):
        run = queue_run(conn, job["id"], "scheduled", user_id)
        next_run = calculate_next_run(job["schedule_type"], job["schedule_time"], job["schedule_days"], job["timezone"], utc_now())
        conn.execute(
            "UPDATE backup_jobs SET next_run_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (next_run, job["id"]),
        )
        if run:
            count += 1
    return count


def process_run(conn, run, worker_id: str, lock_ttl: int, force_failure: bool = False) -> str:
    job = conn.execute("SELECT * FROM backup_jobs WHERE id = ?", (run["job_id"],)).fetchone()
    if not job:
        conn.execute("UPDATE backup_job_runs SET status = 'skipped', finished_at = CURRENT_TIMESTAMP, safe_log = 'Job ausente.' WHERE id = ?", (run["id"],))
        return "skipped"
    locks = [f"job:{run['job_id']}", f"equipment:{run['equipment_id']}"]
    acquired: list[str] = []
    try:
        for key in locks:
            if not acquire_lock(conn, key, worker_id, lock_ttl, f"run={run['uuid']}"):
                return "skipped"
            acquired.append(key)
        start = utc_now()
        conn.execute(
            "UPDATE backup_job_runs SET status = 'running', started_at = ?, worker_id = ? WHERE id = ? AND status = 'queued'",
            (dt_to_db(start), worker_id, run["id"]),
        )
        running = conn.execute("SELECT * FROM backup_job_runs WHERE id = ?", (run["id"],)).fetchone()
        audit_event(conn, None, "job.run_started", "backup_job_run", run["uuid"], job_details(job, running, "running"))
        # Device connections are external and may take minutes. Persist the run
        # claim and its locks before opening SSH/Telnet so SQLite remains
        # available to the web application and the independent workers.
        conn.commit()
        time.sleep(0.01)
        should_fail = force_failure or "simulate_failure" in (job["notes"] or "").lower()
        if running["method"] == "ssh" and not should_fail:
            from .ssh import ERROR_MESSAGES, SSHBackupError, perform_backup

            try:
                reason = "scheduled" if running["trigger_type"] == "scheduled" else "manual"
                backup, safe_log = perform_backup(conn, running["equipment_id"], running, running["created_by_user_id"], reason)
                finish = utc_now()
                duration = int((finish - start).total_seconds() * 1000)
                conn.execute(
                    """
                    UPDATE backup_job_runs
                    SET status = 'success', finished_at = ?, duration_ms = ?, safe_log = ?,
                        created_backup_id = ?
                    WHERE id = ?
                    """,
                    (dt_to_db(finish), duration, safe_log, backup["id"], run["id"]),
                )
                conn.execute("UPDATE backup_jobs SET last_run_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (dt_to_db(finish), job["id"]))
                updated = conn.execute("SELECT * FROM backup_job_runs WHERE id = ?", (run["id"],)).fetchone()
                audit_event(conn, None, "job.run_success", "backup_job_run", run["uuid"], job_details(job, updated, "success"))
                return "success"
            except SSHBackupError as exc:
                finish = utc_now()
                duration = int((finish - start).total_seconds() * 1000)
                message = ERROR_MESSAGES.get(exc.code, str(exc))
                conn.execute(
                    """
                    UPDATE backup_job_runs
                    SET status = 'failed', finished_at = ?, duration_ms = ?, error_code = ?,
                        error_message = ?, safe_log = ?
                    WHERE id = ?
                    """,
                    (dt_to_db(finish), duration, exc.code, message, f"Backup SSH falhou: {exc.code}.", run["id"]),
                )
                updated = conn.execute("SELECT * FROM backup_job_runs WHERE id = ?", (run["id"],)).fetchone()
                audit_event(conn, None, "job.run_failed", "backup_job_run", run["uuid"], job_details(job, updated, "failed"))
                return "failed"
        if running["method"] == "ftp" and not should_fail:
            equipment = conn.execute("SELECT ssh_backup_driver FROM equipment WHERE id=?", (running["equipment_id"],)).fetchone()
            if equipment and equipment["ssh_backup_driver"] == "vsol_olt_telnet_cli":
                from .ssh import ERROR_MESSAGES, SSHBackupError, perform_backup
                try:
                    reason = "scheduled" if running["trigger_type"] == "scheduled" else "manual"
                    backup, safe_log = perform_backup(
                        conn, running["equipment_id"], running, running["created_by_user_id"], reason,
                    )
                    finish = utc_now()
                    duration = int((finish - start).total_seconds() * 1000)
                    conn.execute("""UPDATE backup_job_runs SET status='success',finished_at=?,safe_log=?,
                        duration_ms=?,created_backup_id=? WHERE id=?""",
                                 (dt_to_db(finish), safe_log, duration, backup["id"], running["id"]))
                    conn.execute("UPDATE backup_jobs SET last_run_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                 (dt_to_db(finish), job["id"]))
                    return "success"
                except SSHBackupError as exc:
                    finish = utc_now()
                    duration = int((finish - start).total_seconds() * 1000)
                    message = str(exc) if exc.code == "TELNET_CONNECTION_FAILED" else ERROR_MESSAGES.get(exc.code, str(exc))
                    conn.execute("""UPDATE backup_job_runs SET status='failed',finished_at=?,error_code=?,
                        error_message=?,duration_ms=?,safe_log='Backup Telnet falhou sem expor credenciais.' WHERE id=?""",
                                 (dt_to_db(finish), exc.code, message, duration, running["id"]))
                    return "failed"
            from .olt_runtime import trigger_olt_backup
            from .olt_backup import OLTBackupError
            from .security import SecretKeyError
            try:
                _, operation, _ = trigger_olt_backup(
                    conn, running["equipment_id"], job_run_id=running["id"],
                )
                if operation:
                    conn.execute("UPDATE backup_job_runs SET safe_log='Comandos enviados; aguardando arquivos FTP.' WHERE id=?", (running["id"],))
                    return "waiting"
                finish = utc_now()
                conn.execute("UPDATE backup_job_runs SET status='success',finished_at=?,safe_log='Configuração automática aplicada.' WHERE id=?",
                             (dt_to_db(finish), running["id"]))
                return "success"
            except (OLTBackupError, SecretKeyError, ValueError) as exc:
                finish = utc_now()
                message = (str(exc) if isinstance(exc, OLTBackupError)
                           else "Não foi possível abrir uma credencial necessária para acionar a OLT.")
                conn.execute("""UPDATE backup_job_runs SET status='failed',finished_at=?,error_code='OLT_TRIGGER_FAILED',
                    error_message=?,safe_log='Acionamento OLT falhou sem expor credenciais.' WHERE id=?""",
                    (dt_to_db(finish), message, running["id"]))
                return "failed"
        finish = utc_now()
        duration = int((finish - start).total_seconds() * 1000)
        if should_fail:
            conn.execute(
                """
                UPDATE backup_job_runs
                SET status = 'failed', finished_at = ?, duration_ms = ?, error_code = ?,
                    error_message = ?, safe_log = ?
                WHERE id = ?
                """,
                (dt_to_db(finish), duration, SIMULATED_FAILURE_CODE, SIMULATED_FAILURE_MESSAGE, SIMULATED_FAILURE_MESSAGE, run["id"]),
            )
            updated = conn.execute("SELECT * FROM backup_job_runs WHERE id = ?", (run["id"],)).fetchone()
            audit_event(conn, None, "job.run_failed", "backup_job_run", run["uuid"], job_details(job, updated, "failed"))
            return "failed"
        conn.execute(
            """
            UPDATE backup_job_runs
            SET status = 'success', finished_at = ?, duration_ms = ?, safe_log = ?
            WHERE id = ?
            """,
            (dt_to_db(finish), duration, DRY_RUN_SUCCESS_LOG, run["id"]),
        )
        conn.execute("UPDATE backup_jobs SET last_run_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (dt_to_db(finish), job["id"]))
        updated = conn.execute("SELECT * FROM backup_job_runs WHERE id = ?", (run["id"],)).fetchone()
        audit_event(conn, None, "job.run_success", "backup_job_run", run["uuid"], job_details(job, updated, "success"))
        return "success"
    finally:
        for key in acquired:
            release_lock(conn, key, worker_id)


def run_once(conn, force_failure: bool = False, worker_id: str | None = None) -> WorkerResult:
    worker = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"
    timeout_seconds = int(_setting(conn, "job_run_timeout_seconds", "300"))
    configured_lock_ttl = int(_setting(conn, "job_worker_lock_seconds", "120"))
    # A committed lock must outlive the maximum run duration; otherwise a
    # second worker could acquire the same equipment while the first is active.
    lock_ttl = max(configured_lock_ttl, timeout_seconds + 60)
    timeouts = mark_timeouts(conn, worker, timeout_seconds)
    queued = 0
    if acquire_lock(conn, "scheduler", worker, lock_ttl, "jobs-run-once"):
        try:
            queued = enqueue_due_jobs(conn)
            audit_event(conn, None, "scheduler.executed", "scheduler", "", {"queued": queued, "worker_id": worker})
        finally:
            release_lock(conn, "scheduler", worker)
    runs = conn.execute(
        """
        SELECT * FROM backup_job_runs
        WHERE status = 'queued'
        ORDER BY created_at ASC, id ASC
        LIMIT 25
        """
    ).fetchall()
    success = failed = skipped = processed = 0
    for run in runs:
        status = process_run(conn, run, worker, lock_ttl, force_failure)
        if status == "success":
            success += 1
            processed += 1
        elif status == "failed":
            failed += 1
            processed += 1
        elif status == "skipped":
            skipped += 1
    return WorkerResult(due_jobs=len(due_jobs(conn)), queued_runs=queued, processed_runs=processed, success_runs=success, failed_runs=failed, timeout_runs=timeouts, skipped_runs=skipped)


def stats(conn) -> dict[str, object]:
    total_jobs = conn.execute("SELECT COUNT(*) FROM backup_jobs WHERE status != 'deleted'").fetchone()[0]
    active_jobs = conn.execute("SELECT COUNT(*) FROM backup_jobs WHERE status = 'active'").fetchone()[0]
    paused_jobs = conn.execute("SELECT COUNT(*) FROM backup_jobs WHERE status = 'paused'").fetchone()[0]
    queued = conn.execute("SELECT COUNT(*) FROM backup_job_runs WHERE status = 'queued'").fetchone()[0]
    running = conn.execute("SELECT COUNT(*) FROM backup_job_runs WHERE status = 'running'").fetchone()[0]
    success = conn.execute("SELECT COUNT(*) FROM backup_job_runs WHERE status = 'success'").fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM backup_job_runs WHERE status = 'failed'").fetchone()[0]
    last_run = conn.execute("SELECT created_at FROM backup_job_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    next_job = conn.execute("SELECT next_run_at FROM backup_jobs WHERE status = 'active' AND next_run_at IS NOT NULL ORDER BY next_run_at LIMIT 1").fetchone()
    return {
        "total_jobs": total_jobs,
        "active_jobs": active_jobs,
        "paused_jobs": paused_jobs,
        "queued_runs": queued,
        "running_runs": running,
        "success_runs": success,
        "failed_runs": failed,
        "last_run": last_run["created_at"] if last_run else "",
        "next_job": next_job["next_run_at"] if next_job else "",
    }


def check_health(conn) -> dict[str, int]:
    timeout = int(_setting(conn, "job_run_timeout_seconds", "300"))
    stale_running = conn.execute(
        """SELECT COUNT(*) FROM backup_job_runs WHERE status='running'
                                  AND started_at < datetime('now', ?)""",
        (f"-{timeout} seconds",),
    ).fetchone()[0]
    stale_queued = conn.execute(
        """SELECT COUNT(*) FROM backup_job_runs WHERE status='queued'
                                 AND created_at < datetime('now','-10 minutes')""",
    ).fetchone()[0]
    return {"running_expirados": stale_running, "queued_atrasados": stale_queued}


def _setting(conn, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default
