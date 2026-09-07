from __future__ import annotations

from .jobs import JOB_METHODS, JOB_STATUSES, RUN_STATUSES, SCHEDULE_TYPES, TRIGGER_TYPES


def jobs_page_context(conn, filters: dict[str, str]) -> dict[str, object]:
    where = ["backup_jobs.status != 'deleted'"]
    params: list[object] = []
    joins = """
        FROM backup_jobs
        JOIN equipment ON equipment.id = backup_jobs.equipment_id
        LEFT JOIN environments ON environments.id = equipment.environment_id
        LEFT JOIN equipment_groups ON equipment_groups.id = equipment.group_id
        LEFT JOIN vendors ON vendors.id = equipment.vendor_id
        LEFT JOIN pops ON pops.id = equipment.pop_id
    """
    for field, column in (
        ("equipment_id", "equipment.id"),
        ("environment_id", "environments.id"),
        ("group_id", "equipment_groups.id"),
        ("vendor_id", "vendors.id"),
        ("pop_id", "pops.id"),
    ):
        if filters.get(field):
            where.append(f"{column} = ?")
            params.append(filters[field])
    if filters.get("method") in JOB_METHODS:
        where.append("backup_jobs.method = ?")
        params.append(filters["method"])
    if filters.get("status") in JOB_STATUSES:
        where.append("backup_jobs.status = ?")
        params.append(filters["status"])
    if filters.get("schedule_type") in SCHEDULE_TYPES:
        where.append("backup_jobs.schedule_type = ?")
        params.append(filters["schedule_type"])
    where_sql = " WHERE " + " AND ".join(where)
    rows = conn.execute(
        f"SELECT backup_jobs.*, equipment.hostname {joins} {where_sql} ORDER BY backup_jobs.updated_at DESC, backup_jobs.id DESC LIMIT 100",
        params,
    ).fetchall()
    return {
        "rows": rows,
        "equipment": conn.execute("SELECT id, hostname AS name FROM equipment ORDER BY hostname").fetchall(),
        "environments": conn.execute("SELECT id, name FROM environments ORDER BY name").fetchall(),
        "groups": conn.execute("SELECT id, name FROM equipment_groups ORDER BY name").fetchall(),
        "vendors": conn.execute("SELECT id, name FROM vendors ORDER BY name").fetchall(),
        "pops": conn.execute("SELECT id, name FROM pops ORDER BY name").fetchall(),
    }


def job_runs_page_context(conn, filters: dict[str, str], job_uuid: str | None = None) -> dict[str, object]:
    where = ["1 = 1"]
    params: list[object] = []
    joins = """
        FROM backup_job_runs
        JOIN backup_jobs ON backup_jobs.id = backup_job_runs.job_id
        JOIN equipment ON equipment.id = backup_job_runs.equipment_id
    """
    if job_uuid:
        where.append("backup_jobs.uuid = ?")
        params.append(job_uuid)
    if filters.get("equipment_id"):
        where.append("equipment.id = ?")
        params.append(filters["equipment_id"])
    if filters.get("job"):
        where.append("backup_jobs.uuid = ?")
        params.append(filters["job"])
    if filters.get("status") in RUN_STATUSES:
        where.append("backup_job_runs.status = ?")
        params.append(filters["status"])
    if filters.get("trigger") in TRIGGER_TYPES:
        where.append("backup_job_runs.trigger_type = ?")
        params.append(filters["trigger"])
    if filters.get("start"):
        where.append("backup_job_runs.created_at >= ?")
        params.append(filters["start"])
    if filters.get("end"):
        where.append("backup_job_runs.created_at <= ?")
        params.append(filters["end"] + " 23:59:59")
    where_sql = " WHERE " + " AND ".join(where)
    return {
        "rows": conn.execute(
            f"SELECT backup_job_runs.*, equipment.hostname,equipment.ssh_backup_driver,backup_jobs.uuid AS job_uuid {joins} {where_sql} ORDER BY backup_job_runs.created_at DESC, backup_job_runs.id DESC LIMIT 100",
            params,
        ).fetchall(),
        "equipment": conn.execute("SELECT id, hostname AS name FROM equipment ORDER BY hostname").fetchall(),
    }


def run_detail_context(conn, run_uuid: str) -> dict[str, object]:
    row = conn.execute(
        """
        SELECT backup_job_runs.*, equipment.hostname,equipment.ssh_backup_driver,backup_jobs.uuid AS job_uuid
        FROM backup_job_runs
        JOIN backup_jobs ON backup_jobs.id = backup_job_runs.job_id
        JOIN equipment ON equipment.id = backup_job_runs.equipment_id
        WHERE backup_job_runs.uuid = ?
        """,
        (run_uuid,),
    ).fetchone()
    return {"row": row}
