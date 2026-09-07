from __future__ import annotations

from .mikrotik_ftp_service import equipment_snapshot as mikrotik_equipment_snapshot
from .storage import load_config


def equipment_detail_context(conn, equipment_id: int, *, selected_run_uuid: str = "") -> dict[str, object]:
    item = conn.execute(
        """
        SELECT equipment.*, vendors.name AS vendor, equipment_groups.name AS group_name,
               pops.name AS pop, environments.name AS environment
        FROM equipment
        LEFT JOIN vendors ON vendors.id = equipment.vendor_id
        LEFT JOIN equipment_groups ON equipment_groups.id = equipment.group_id
        LEFT JOIN pops ON pops.id = equipment.pop_id
        LEFT JOIN environments ON environments.id = equipment.environment_id
        WHERE equipment.id = ?
        """,
        (equipment_id,),
    ).fetchone()
    if not item:
        return {"item": None}
    rows = conn.execute(
        "SELECT * FROM backups WHERE equipment_id = ? AND backup_status != 'deleted' ORDER BY received_at DESC, id DESC LIMIT 100",
        (equipment_id,),
    ).fetchall()
    jobs = conn.execute(
        """
        SELECT backup_jobs.*, equipment.hostname
        FROM backup_jobs JOIN equipment ON equipment.id = backup_jobs.equipment_id
        WHERE backup_jobs.equipment_id = ? AND backup_jobs.status != 'deleted'
        ORDER BY backup_jobs.created_at DESC
        """,
        (equipment_id,),
    ).fetchall()
    credentials = conn.execute(
        """
        SELECT * FROM device_credentials
        WHERE equipment_id = ? AND credential_type = 'ssh' AND deleted_at IS NULL
        ORDER BY is_active DESC, updated_at DESC, id DESC
        """,
        (equipment_id,),
    ).fetchall()
    last_ssh_backup = conn.execute(
        """
        SELECT received_at FROM backups
        WHERE equipment_id = ? AND source_method = 'ssh' AND backup_status = 'available'
        ORDER BY received_at DESC, id DESC LIMIT 1
        """,
        (equipment_id,),
    ).fetchone()
    last_ssh_run = None
    if selected_run_uuid:
        last_ssh_run = conn.execute(
            """
            SELECT backup_job_runs.*, backups.uuid AS created_backup_uuid,
                   backups.original_filename AS created_backup_name,
                   backups.file_size AS created_backup_size
            FROM backup_job_runs
            LEFT JOIN backups ON backups.id = backup_job_runs.created_backup_id
            WHERE backup_job_runs.uuid = ? AND backup_job_runs.equipment_id = ? AND backup_job_runs.method = 'ssh'
            """,
            (selected_run_uuid, equipment_id),
        ).fetchone()
    if not last_ssh_run:
        last_ssh_run = conn.execute(
            """
            SELECT backup_job_runs.*, backups.uuid AS created_backup_uuid,
                   backups.original_filename AS created_backup_name,
                   backups.file_size AS created_backup_size
            FROM backup_job_runs
            LEFT JOIN backups ON backups.id = backup_job_runs.created_backup_id
            WHERE backup_job_runs.equipment_id = ? AND backup_job_runs.method = 'ssh'
            ORDER BY backup_job_runs.created_at DESC, backup_job_runs.id DESC LIMIT 1
            """,
            (equipment_id,),
        ).fetchone()
    config = load_config(conn)
    timezone_name = conn.execute("SELECT value FROM settings WHERE key='timezone'").fetchone()
    timezone_name = timezone_name[0] if timezone_name and timezone_name[0] else "America/Sao_Paulo"
    artifacts_v2_row = conn.execute("SELECT value FROM settings WHERE key='backup_artifacts_v2_enabled'").fetchone()
    artifacts_v2 = bool(artifacts_v2_row and artifacts_v2_row[0] == "1")
    ftp_host_row = conn.execute("SELECT value FROM settings WHERE key='ftp_public_ip'").fetchone()
    if not ftp_host_row or not ftp_host_row[0]:
        ftp_host_row = conn.execute("SELECT value FROM settings WHERE key='ftp_public_ipv6'").fetchone()
    ftp_port_row = conn.execute("SELECT value FROM settings WHERE key='ftp_control_port'").fetchone()
    mikrotik_snapshot = mikrotik_equipment_snapshot(conn, equipment_id, artifacts_v2=artifacts_v2)
    olt_operation = conn.execute(
        "SELECT * FROM backup_operations WHERE provider_key='olt_ftp' AND equipment_id=? ORDER BY id DESC LIMIT 1",
        (equipment_id,),
    ).fetchone()
    olt_artifacts = (conn.execute(
        "SELECT * FROM backup_operation_artifacts WHERE operation_id=? ORDER BY id", (olt_operation["id"],),
    ).fetchall() if olt_operation else [])
    return {
        "item": item,
        "rows": rows,
        "jobs": jobs,
        "credentials": credentials,
        "last_ssh_backup": last_ssh_backup,
        "last_ssh_run": last_ssh_run,
        "config": config,
        "timezone_name": timezone_name,
        "artifacts_v2": artifacts_v2,
        "default_ftp_host": ftp_host_row[0] if ftp_host_row else "",
        "default_ftp_port": ftp_port_row[0] if ftp_port_row and ftp_port_row[0] else "21",
        "mikrotik_snapshot": mikrotik_snapshot,
        "olt_operation": olt_operation,
        "olt_artifacts": olt_artifacts,
    }
