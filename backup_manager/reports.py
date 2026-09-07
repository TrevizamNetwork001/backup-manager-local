from __future__ import annotations

import csv
import io
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape


REPORT_STATUSES = ("success", "failed", "partial", "running", "cancelled")
REPORT_METHODS = ("ssh", "ftp", "manual", "rclone", "telegram")
PAGE_SIZES = (25, 50, 100)


@dataclass(frozen=True)
class ReportFilters:
    start: str = ""
    end: str = ""
    company: str = ""
    equipment_id: int | None = None
    group_id: int | None = None
    vendor_id: int | None = None
    pop_id: int | None = None
    status: str = ""
    method: str = ""
    user_id: int | None = None
    event: str = ""
    page: int = 1
    per_page: int = 25


def _integer(value: object) -> int | None:
    text = str(value or "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def _iso_date(value: object) -> str:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat() if text else ""
    except ValueError:
        return ""


def parse_filters(values: dict[str, object]) -> ReportFilters:
    page = _integer(values.get("page")) or 1
    requested_size = _integer(values.get("per_page")) or 25
    return ReportFilters(
        start=_iso_date(values.get("start")),
        end=_iso_date(values.get("end")),
        company=str(values.get("company") or "").strip()[:120],
        equipment_id=_integer(values.get("equipment_id")),
        group_id=_integer(values.get("group_id")),
        vendor_id=_integer(values.get("vendor_id")),
        pop_id=_integer(values.get("pop_id")),
        status=str(values.get("status") or "") if values.get("status") in REPORT_STATUSES else "",
        method=str(values.get("method") or "") if values.get("method") in REPORT_METHODS else "",
        user_id=_integer(values.get("user_id")),
        event=str(values.get("event") or "").strip()[:120],
        page=min(page, 100000),
        per_page=requested_size if requested_size in PAGE_SIZES else 25,
    )


def filter_values(filters: ReportFilters, *, include_page: bool = True) -> dict[str, str]:
    result = {
        "start": filters.start,
        "end": filters.end,
        "company": filters.company,
        "equipment_id": str(filters.equipment_id or ""),
        "group_id": str(filters.group_id or ""),
        "vendor_id": str(filters.vendor_id or ""),
        "pop_id": str(filters.pop_id or ""),
        "status": filters.status,
        "method": filters.method,
        "user_id": str(filters.user_id or ""),
        "event": filters.event,
        "per_page": str(filters.per_page),
    }
    if include_page:
        result["page"] = str(filters.page)
    return {key: value for key, value in result.items() if value}


def _equipment_conditions(filters: ReportFilters, alias: str = "e") -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    for value, column in (
        (filters.equipment_id, f"{alias}.id"),
        (filters.group_id, f"{alias}.group_id"),
        (filters.vendor_id, f"{alias}.vendor_id"),
        (filters.pop_id, f"{alias}.pop_id"),
    ):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    return clauses, params


def _period_conditions(filters: ReportFilters, column: str) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if filters.start:
        clauses.append(f"{column} >= ?")
        params.append(filters.start)
    if filters.end:
        clauses.append(f"{column} < datetime(?, '+1 day')")
        params.append(filters.end)
    return clauses, params


def choices(conn) -> dict[str, list]:
    return {
        "equipment": conn.execute("SELECT id,hostname AS name FROM equipment ORDER BY hostname").fetchall(),
        "groups": conn.execute("SELECT id,name FROM equipment_groups ORDER BY name").fetchall(),
        "vendors": conn.execute("SELECT id,name FROM vendors ORDER BY name").fetchall(),
        "pops": conn.execute("SELECT id,name FROM pops ORDER BY name").fetchall(),
        "users": conn.execute("SELECT id,username AS name FROM users WHERE is_active=1 ORDER BY username").fetchall(),
    }


def overview(conn, filters: ReportFilters, storage_root: str) -> dict[str, object]:
    eq_clauses, eq_params = _equipment_conditions(filters)
    eq_where = " WHERE " + " AND ".join(eq_clauses) if eq_clauses else ""
    equipment = conn.execute(
        f"SELECT COUNT(*) total, SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) active, "
        f"SUM(CASE WHEN is_active=0 THEN 1 ELSE 0 END) archived FROM equipment e{eq_where}", eq_params,
    ).fetchone()
    joins = " FROM backups b JOIN equipment e ON e.id=b.equipment_id"
    backup_clauses, backup_params = _equipment_conditions(filters)
    period_clauses, period_params = _period_conditions(filters, "b.received_at")
    all_clauses = backup_clauses + period_clauses
    where = " WHERE " + " AND ".join(all_clauses) if all_clauses else ""
    backup = conn.execute(
        f"SELECT COUNT(*) total_files,COALESCE(SUM(file_size),0) bytes,"
        f"SUM(CASE WHEN backup_status='available' THEN 1 ELSE 0 END) success,"
        f"SUM(CASE WHEN backup_status IN ('failed','quarantined') THEN 1 ELSE 0 END) failed "
        f"{joins}{where}", backup_params + period_params,
    ).fetchone()
    recent = conn.execute(
        f"SELECT SUM(CASE WHEN b.received_at>=datetime('now','-24 hours') THEN 1 ELSE 0 END) h24,"
        f"SUM(CASE WHEN b.received_at>=datetime('now','-7 days') THEN 1 ELSE 0 END) d7,"
        f"SUM(CASE WHEN b.received_at>=datetime('now','-30 days') THEN 1 ELSE 0 END) d30 "
        f"{joins}" + (" WHERE " + " AND ".join(backup_clauses) if backup_clauses else ""), backup_params,
    ).fetchone()
    restored = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='lifecycle.backup_restored'").fetchone()[0]
    removed = conn.execute(
        "SELECT COALESCE(SUM(purged_count),0) FROM lifecycle_runs WHERE status='completed' AND mode IN ('execution','automatic')"
    ).fetchone()[0]
    root = Path(storage_root)
    while not root.exists() and root != root.parent:
        root = root.parent
    try:
        disk = shutil.disk_usage(root)
    except OSError:
        disk = shutil.disk_usage("/")
    attempts = int(backup["success"] or 0) + int(backup["failed"] or 0)
    return {
        "equipment_total": int(equipment["total"] or 0),
        "equipment_active": int(equipment["active"] or 0),
        "equipment_archived": int(equipment["archived"] or 0),
        "backups_24h": int(recent["h24"] or 0),
        "backups_7d": int(recent["d7"] or 0),
        "backups_30d": int(recent["d30"] or 0),
        "success_rate": (100.0 * int(backup["success"] or 0) / attempts) if attempts else 0.0,
        "failure_rate": (100.0 * int(backup["failed"] or 0) / attempts) if attempts else 0.0,
        "storage_used": disk.used,
        "storage_free": disk.free,
        "total_files": int(backup["total_files"] or 0),
        "total_restored": int(restored),
        "total_lifecycle_removed": int(removed),
    }


BACKUP_CTE = """
WITH report_rows AS (
 SELECT r.id sort_id,COALESCE(r.finished_at,r.started_at,r.created_at) occurred_at,r.duration_ms,
        COALESCE(b.file_size,0) file_size,COALESCE(b.sha256,'') sha256,
        CASE WHEN r.method='dry_run' THEN 'manual' ELSE r.method END method,
        CASE WHEN r.status='success' THEN 'success' WHEN r.status='failed' THEN 'failed'
             WHEN r.status IN ('timeout','skipped') THEN 'partial' WHEN r.status IN ('queued','running') THEN 'running'
             WHEN r.status='cancelled' THEN 'cancelled' ELSE r.status END status,
        e.id equipment_id,e.hostname,e.ip_address,e.group_id,e.vendor_id,e.pop_id,
        COALESCE(v.name,'') vendor,COALESCE(g.name,'') group_name,COALESCE(p.name,'') pop,
        e.hostname || CASE WHEN e.ip_address!='' THEN ' ('||e.ip_address||')' ELSE '' END origin,
        COALESCE(b.relative_path,b.trash_relative_path,'-') destination,
        COALESCE(u.username,'Sistema') operator,COALESCE(r.error_message,'') error_message,
        COALESCE(b.uuid,r.uuid) reference
 FROM backup_job_runs r JOIN equipment e ON e.id=r.equipment_id
 LEFT JOIN backups b ON b.id=r.created_backup_id LEFT JOIN users u ON u.id=r.created_by_user_id
 LEFT JOIN vendors v ON v.id=e.vendor_id LEFT JOIN equipment_groups g ON g.id=e.group_id LEFT JOIN pops p ON p.id=e.pop_id
 UNION ALL
 SELECT 1000000000+b.id,b.received_at,NULL,b.file_size,b.sha256,b.source_method,
        CASE WHEN b.backup_status='available' THEN 'success' WHEN b.backup_status IN ('failed','quarantined') THEN 'failed'
             WHEN b.backup_status IN ('receiving','validating') THEN 'running'
             WHEN b.backup_status IN ('trashed','deleted') THEN 'cancelled' ELSE 'partial' END,
        e.id,e.hostname,e.ip_address,e.group_id,e.vendor_id,e.pop_id,COALESCE(v.name,''),COALESCE(g.name,''),COALESCE(p.name,''),
        e.hostname || CASE WHEN e.ip_address!='' THEN ' ('||e.ip_address||')' ELSE '' END,
        COALESCE(b.relative_path,b.trash_relative_path,'-'),COALESCE(u.username,'Sistema'),b.notes,b.uuid
 FROM backups b JOIN equipment e ON e.id=b.equipment_id LEFT JOIN users u ON u.id=b.created_by_user_id
 LEFT JOIN vendors v ON v.id=e.vendor_id LEFT JOIN equipment_groups g ON g.id=e.group_id LEFT JOIN pops p ON p.id=e.pop_id
 WHERE NOT EXISTS(SELECT 1 FROM backup_job_runs r WHERE r.created_backup_id=b.id)
 UNION ALL
 SELECT 2000000000+i.id,COALESCE(i.finished_at,i.started_at,i.queued_at),i.duration_ms,i.bytes_total,
        COALESCE(i.local_sha256,b.sha256,''),'rclone',
        CASE WHEN i.status='synced' THEN 'success' WHEN i.status='failed' THEN 'failed'
             WHEN i.status IN ('queued','validating','uploading','retry_wait') THEN 'running'
             WHEN i.status='cancelled' THEN 'cancelled' ELSE 'partial' END,
        e.id,e.hostname,e.ip_address,e.group_id,e.vendor_id,e.pop_id,COALESCE(v.name,''),COALESCE(g.name,''),COALESCE(p.name,''),
        COALESCE(b.relative_path,e.hostname),t.name,COALESCE(u.username,'Sistema'),COALESCE(i.error_message,''),i.uuid
 FROM cloud_sync_items i JOIN equipment e ON e.id=i.equipment_id JOIN backups b ON b.id=i.backup_id
 JOIN cloud_targets t ON t.id=i.target_id JOIN rclone_connections rc ON rc.target_id=t.id LEFT JOIN users u ON u.id=t.created_by_user_id
 LEFT JOIN vendors v ON v.id=e.vendor_id LEFT JOIN equipment_groups g ON g.id=e.group_id LEFT JOIN pops p ON p.id=e.pop_id
)
"""


def backup_report(conn, filters: ReportFilters, *, paginate: bool = True) -> tuple[list, int]:
    clauses, params = _period_conditions(filters, "occurred_at")
    for value, column in (
        (filters.equipment_id, "equipment_id"), (filters.group_id, "group_id"),
        (filters.vendor_id, "vendor_id"), (filters.pop_id, "pop_id"),
    ):
        if value:
            clauses.append(f"{column}=?")
            params.append(value)
    if filters.status:
        clauses.append("status=?")
        params.append(filters.status)
    if filters.method:
        clauses.append("method=?")
        params.append(filters.method)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    total = conn.execute(BACKUP_CTE + "SELECT COUNT(*) FROM report_rows" + where, params).fetchone()[0]
    limit = " LIMIT ? OFFSET ?" if paginate else ""
    query_params = params + ([filters.per_page, (filters.page - 1) * filters.per_page] if paginate else [])
    rows = conn.execute(
        BACKUP_CTE + "SELECT occurred_at,duration_ms,file_size,sha256,method,status,equipment_id,hostname,vendor,group_name,pop,origin,destination,operator,error_message,reference "
        "FROM report_rows" + where + " ORDER BY occurred_at DESC,sort_id DESC" + limit,
        query_params,
    ).fetchall()
    return rows, int(total)


def daily_chart(conn, filters: ReportFilters, days: int = 14) -> list:
    eq, params = _equipment_conditions(filters, "e")
    period, period_params = _period_conditions(filters, "b.received_at")
    if period:
        eq.extend(period)
        params.extend(period_params)
    else:
        eq.append("b.received_at>=date('now',?)")
        params.append(f"-{days - 1} days")
    return conn.execute(
        "SELECT date(b.received_at) day,COUNT(*) total,"
        "SUM(CASE WHEN b.backup_status='available' THEN 1 ELSE 0 END) success,"
        "SUM(CASE WHEN b.backup_status IN ('failed','quarantined') THEN 1 ELSE 0 END) failed "
        "FROM backups b JOIN equipment e ON e.id=b.equipment_id WHERE " + " AND ".join(eq) +
        " GROUP BY date(b.received_at) ORDER BY day", params,
    ).fetchall()


def equipment_report(conn, filters: ReportFilters, *, paginate: bool = True) -> tuple[list, int]:
    clauses, params = _equipment_conditions(filters, "e")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    total = conn.execute("SELECT COUNT(*) FROM equipment e" + where, params).fetchone()[0]
    limit = " LIMIT ? OFFSET ?" if paginate else ""
    query_params = params + ([filters.per_page, (filters.page - 1) * filters.per_page] if paginate else [])
    rows = conn.execute(
        """WITH backup_stats AS (
          SELECT equipment_id,MAX(received_at) last_backup,COUNT(*) backup_count
          FROM backups WHERE backup_status='available' GROUP BY equipment_id
        ), failure_stats AS (
          SELECT equipment_id,MAX(COALESCE(finished_at,created_at)) last_failure,COUNT(*) failure_count
          FROM backup_job_runs WHERE status IN ('failed','timeout') GROUP BY equipment_id
        ), last_success AS (
          SELECT equipment_id,MAX(COALESCE(finished_at,created_at)) last_success
          FROM backup_job_runs WHERE status='success' GROUP BY equipment_id
        ), consecutive AS (
          SELECT r.equipment_id,COUNT(*) consecutive_failures FROM backup_job_runs r
          LEFT JOIN last_success s ON s.equipment_id=r.equipment_id
          WHERE r.status IN ('failed','timeout') AND (s.last_success IS NULL OR COALESCE(r.finished_at,r.created_at)>s.last_success)
          GROUP BY r.equipment_id
        ), restored AS (
          SELECT b.equipment_id,MAX(a.created_at) last_restore FROM audit_log a
          JOIN backups b ON b.uuid=a.entity_id WHERE a.action='lifecycle.backup_restored' GROUP BY b.equipment_id
        )
        SELECT e.id,e.hostname,e.ip_address,e.is_active,COALESCE(v.name,'') vendor,COALESCE(g.name,'') group_name,
               COALESCE(p.name,'') pop,bs.last_backup,COALESCE(bs.backup_count,0) backup_count,
               fs.last_failure,COALESCE(fs.failure_count,0) failure_count,COALESCE(c.consecutive_failures,0) consecutive_failures,
               r.last_restore,CAST(julianday('now')-julianday(bs.last_backup) AS INTEGER) days_without_backup
        FROM equipment e LEFT JOIN vendors v ON v.id=e.vendor_id LEFT JOIN equipment_groups g ON g.id=e.group_id
        LEFT JOIN pops p ON p.id=e.pop_id LEFT JOIN backup_stats bs ON bs.equipment_id=e.id
        LEFT JOIN failure_stats fs ON fs.equipment_id=e.id LEFT JOIN consecutive c ON c.equipment_id=e.id
        LEFT JOIN restored r ON r.equipment_id=e.id""" + where +
        " ORDER BY bs.last_backup IS NOT NULL,bs.last_backup ASC,e.hostname" + limit, query_params,
    ).fetchall()
    return rows, int(total)


def equipment_gap_summary(conn, filters: ReportFilters) -> dict[str, object]:
    clauses, params = _equipment_conditions(filters, "e")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    row = conn.execute(
        """WITH latest AS (
          SELECT equipment_id,MAX(received_at) last_backup FROM backups WHERE backup_status='available' GROUP BY equipment_id
        )
        SELECT SUM(CASE WHEN l.last_backup IS NULL OR l.last_backup<datetime('now','-24 hours') THEN 1 ELSE 0 END) h24,
               SUM(CASE WHEN l.last_backup IS NULL OR l.last_backup<datetime('now','-48 hours') THEN 1 ELSE 0 END) h48,
               SUM(CASE WHEN l.last_backup IS NULL OR l.last_backup<datetime('now','-72 hours') THEN 1 ELSE 0 END) h72,
               SUM(CASE WHEN l.last_backup IS NULL OR l.last_backup<datetime('now','-7 days') THEN 1 ELSE 0 END) d7,
               MIN(l.last_backup) oldest_backup
        FROM equipment e LEFT JOIN latest l ON l.equipment_id=e.id""" + where, params,
    ).fetchone()
    return {key: (int(row[key] or 0) if key != "oldest_backup" else row[key]) for key in row.keys()}


def failure_report(conn, filters: ReportFilters, limit: int = 100) -> list:
    clauses, params = _equipment_conditions(filters, "e")
    period, period_params = _period_conditions(filters, "occurred_at")
    clauses.extend(period)
    params.extend(period_params)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return conn.execute(
        """WITH failures AS (
          SELECT r.equipment_id,COALESCE(r.finished_at,r.created_at) occurred_at,
                 CASE WHEN r.method='ssh' THEN 'SSH' WHEN r.method='ftp' THEN 'FTP'
                      WHEN lower(COALESCE(r.error_code,'')||' '||COALESCE(r.error_message,'')) LIKE '%storage%' THEN 'Storage'
                      ELSE 'Worker' END category,COALESCE(NULLIF(r.error_message,''),NULLIF(r.error_code,''),'Falha não detalhada') reason
          FROM backup_job_runs r WHERE r.status IN ('failed','timeout')
          UNION ALL SELECT equipment_id,COALESCE(processed_at,updated_at),'FTP',COALESCE(NULLIF(error_message,''),NULLIF(error_code,''),'Arquivo rejeitado')
          FROM ftp_received_files WHERE status IN ('failed','rejected')
          UNION ALL SELECT i.equipment_id,COALESCE(i.finished_at,i.updated_at),'rclone',COALESCE(NULLIF(i.error_message,''),NULLIF(i.error_code,''),'Falha de sincronização')
          FROM cloud_sync_items i JOIN rclone_connections rc ON rc.target_id=i.target_id WHERE i.status='failed'
          UNION ALL SELECT COALESCE(b.equipment_id,fr.equipment_id),li.created_at,'Lifecycle',COALESCE(NULLIF(li.error_message,''),li.reason,'Falha de retenção')
          FROM lifecycle_items li LEFT JOIN backups b ON b.id=li.backup_id
          LEFT JOIN ftp_received_files fr ON fr.id=li.ftp_received_file_id
          WHERE li.result IN ('failed','missing') AND COALESCE(b.equipment_id,fr.equipment_id) IS NOT NULL
          UNION ALL SELECT equipment_id,received_at,'Storage',COALESCE(NULLIF(notes,''),'Backup indisponível')
          FROM backups WHERE backup_status IN ('failed','quarantined')
        )
        SELECT e.id,e.hostname,COUNT(*) failure_count,MAX(f.occurred_at) last_failure,
               SUM(CASE WHEN f.category='SSH' THEN 1 ELSE 0 END) ssh_failures,
               SUM(CASE WHEN f.category='FTP' THEN 1 ELSE 0 END) ftp_failures,
               SUM(CASE WHEN f.category='Storage' THEN 1 ELSE 0 END) storage_failures,
               SUM(CASE WHEN f.category='Lifecycle' THEN 1 ELSE 0 END) lifecycle_failures,
               SUM(CASE WHEN f.category='rclone' THEN 1 ELSE 0 END) drive_failures,
               SUM(CASE WHEN f.category='Worker' THEN 1 ELSE 0 END) worker_failures,
               GROUP_CONCAT(DISTINCT f.reason) reasons
        FROM failures f JOIN equipment e ON e.id=f.equipment_id""" + where +
        " GROUP BY e.id,e.hostname ORDER BY failure_count DESC,last_failure DESC LIMIT ?", params + [limit],
    ).fetchall()


def storage_report(conn, filters: ReportFilters, storage_root: str) -> dict[str, object]:
    clauses, params = _equipment_conditions(filters, "e")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    backup = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN b.backup_status='available' AND b.source_method!='ftp' THEN b.file_size ELSE 0 END),0) local_bytes,"
        "COALESCE(SUM(CASE WHEN b.backup_status='available' AND b.source_method='ftp' THEN b.file_size ELSE 0 END),0) ftp_bytes,"
        "COALESCE(SUM(CASE WHEN b.backup_status='trashed' THEN b.file_size ELSE 0 END),0) trash_bytes,"
        "COALESCE(MAX(b.file_size),0) largest,COALESCE(MIN(CASE WHEN b.file_size>0 THEN b.file_size END),0) smallest,"
        "COALESCE(AVG(CASE WHEN b.file_size>0 THEN b.file_size END),0) average "
        "FROM backups b JOIN equipment e ON e.id=b.equipment_id" + where, params,
    ).fetchone()
    rejected = conn.execute(
        "SELECT COALESCE(SUM(r.file_size),0) FROM ftp_received_files r JOIN equipment e ON e.id=r.equipment_id " +
        (where + (" AND " if where else " WHERE ") + "r.status='rejected'"), params,
    ).fetchone()[0]
    synced = conn.execute(
        "SELECT COALESCE(SUM(i.bytes_uploaded),0) FROM cloud_sync_items i JOIN equipment e ON e.id=i.equipment_id JOIN rclone_connections rc ON rc.target_id=i.target_id " +
        (where + (" AND " if where else " WHERE ") + "i.status='synced'"), params,
    ).fetchone()[0]
    root = Path(storage_root)
    while not root.exists() and root != root.parent:
        root = root.parent
    disk = shutil.disk_usage(root)
    return {**dict(backup), "rejected_bytes": int(rejected), "synced_bytes": int(synced),
            "disk_total": disk.total, "disk_used": disk.used, "disk_free": disk.free}


def ftp_report(conn, filters: ReportFilters) -> dict[str, object]:
    clauses, params = _equipment_conditions(filters, "e")
    period, period_params = _period_conditions(filters, "r.detected_at")
    clauses.extend(period)
    params.extend(period_params)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    totals = conn.execute(
        "SELECT COUNT(*) uploads,SUM(CASE WHEN r.status IN ('imported','duplicate') THEN 1 ELSE 0 END) imported,"
        "SUM(CASE WHEN r.status='rejected' THEN 1 ELSE 0 END) rejected,"
        "SUM(CASE WHEN r.status='failed' THEN 1 ELSE 0 END) failed,MAX(r.detected_at) last_upload,"
        "MAX(CASE WHEN r.status='imported' THEN r.processed_at END) last_import "
        "FROM ftp_received_files r JOIN equipment e ON e.id=r.equipment_id" + where, params,
    ).fetchone()
    account_clauses, account_params = _equipment_conditions(filters, "e")
    account_where = " WHERE a.deleted_at IS NULL" + (" AND " + " AND ".join(account_clauses) if account_clauses else "")
    accounts = conn.execute("SELECT COUNT(*) FROM ftp_accounts a JOIN equipment e ON e.id=a.equipment_id" + account_where, account_params).fetchone()[0]
    top = conn.execute(
        "SELECT e.hostname,COUNT(*) uploads,MAX(r.detected_at) last_upload FROM ftp_received_files r "
        "JOIN equipment e ON e.id=r.equipment_id" + where + " GROUP BY e.id,e.hostname ORDER BY uploads DESC,last_upload DESC LIMIT 10", params,
    ).fetchall()
    return {**dict(totals), "accounts": int(accounts), "top": top}


def telegram_report(conn, filters: ReportFilters) -> dict[str, object]:
    clauses, params = _equipment_conditions(filters, "e")
    period, period_params = _period_conditions(filters, "i.queued_at")
    clauses.extend(period); params.extend(period_params)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    totals = conn.execute("""SELECT COUNT(*) total,
        SUM(CASE WHEN i.status='sent' THEN 1 ELSE 0 END) sent,
        SUM(CASE WHEN i.status='failed' THEN 1 ELSE 0 END) failed,
        SUM(CASE WHEN i.status='skipped' THEN 1 ELSE 0 END) skipped,
        COALESCE(SUM(CASE WHEN i.status='sent' THEN i.bytes_sent ELSE 0 END),0) bytes_sent
        FROM telegram_backup_items i JOIN equipment e ON e.id=i.equipment_id""" + where, params).fetchone()
    top = conn.execute("""SELECT e.hostname,COUNT(*) sent,COALESCE(SUM(i.bytes_sent),0) bytes_sent
        FROM telegram_backup_items i JOIN equipment e ON e.id=i.equipment_id""" + where +
        " GROUP BY e.id ORDER BY sent DESC,bytes_sent DESC,e.hostname LIMIT 10", params).fetchall()
    total = int(totals["total"] or 0); sent = int(totals["sent"] or 0)
    return {"total": total, "sent": sent, "failed": int(totals["failed"] or 0),
            "skipped": int(totals["skipped"] or 0), "bytes_sent": int(totals["bytes_sent"] or 0),
            "success_rate": (100.0 * sent / total) if total else 0.0, "top_equipment": top}


def audit_report(conn, filters: ReportFilters, *, paginate: bool = True) -> tuple[list, int]:
    clauses, params = _period_conditions(filters, "a.created_at")
    if filters.user_id:
        clauses.append("a.user_id=?")
        params.append(filters.user_id)
    if filters.event:
        clauses.append("a.action LIKE ?")
        params.append(f"%{filters.event}%")
    else:
        # Heartbeats preservam valor técnico, mas não devem dominar a consulta
        # operacional. Continuam disponíveis quando procurados pelo filtro Evento.
        clauses.append("NOT (a.action='scheduler.executed' AND a.details LIKE '%\"queued\":0%')")
        clauses.append("a.action!='cloud.worker_executed'")
    if filters.equipment_id:
        clauses.append("(a.entity_id=? OR b.equipment_id=?)")
        params.extend([str(filters.equipment_id), filters.equipment_id])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    joins = " FROM audit_log a LEFT JOIN users u ON u.id=a.user_id LEFT JOIN backups b ON b.uuid=a.entity_id"
    total = conn.execute("SELECT COUNT(DISTINCT a.id)" + joins + where, params).fetchone()[0]
    limit = " LIMIT ? OFFSET ?" if paginate else ""
    query_params = params + ([filters.per_page, (filters.page - 1) * filters.per_page] if paginate else [])
    rows = conn.execute(
        "SELECT DISTINCT a.id,a.created_at,COALESCE(u.username,'Sistema') username,a.ip_address,a.action,a.entity,a.entity_id,a.details" +
        joins + where + " ORDER BY a.id DESC" + limit, query_params,
    ).fetchall()
    return rows, int(total)


EXPORT_COLUMNS = {
    "backups": (("Data", "occurred_at"), ("Equipamento", "hostname"), ("Fabricante", "vendor"),
                ("Grupo", "group_name"), ("POP", "pop"), ("Método", "method"), ("Status", "status"),
                ("Duração (ms)", "duration_ms"), ("Tamanho (bytes)", "file_size"), ("SHA-256", "sha256"),
                ("Origem", "origin"), ("Destino", "destination"), ("Operador", "operator"), ("Motivo", "error_message")),
    "equipment": (("Equipamento", "hostname"), ("IP", "ip_address"), ("Ativo", "is_active"),
                  ("Fabricante", "vendor"), ("Grupo", "group_name"), ("POP", "pop"),
                  ("Último backup", "last_backup"), ("Backups", "backup_count"),
                  ("Última falha", "last_failure"), ("Falhas", "failure_count"),
                  ("Falhas consecutivas", "consecutive_failures"), ("Última restauração", "last_restore")),
    "audit": (("Quando", "created_at"), ("Quem", "username"), ("Origem", "ip_address"),
              ("Evento", "action"), ("Entidade", "entity"), ("Descrição", "details")),
}


def export_dataset(conn, report: str, filters: ReportFilters) -> tuple[list[str], list[list[object]]]:
    if report == "equipment":
        records, _ = equipment_report(conn, filters, paginate=False)
    elif report == "audit":
        records, _ = audit_report(conn, filters, paginate=False)
    else:
        report = "backups"
        records, _ = backup_report(conn, filters, paginate=False)
    columns = EXPORT_COLUMNS[report]
    return [label for label, _ in columns], [[row[key] if row[key] is not None else "" for _, key in columns] for row in records]


def csv_export(headers: list[str], rows: list[list[object]], separator: str = ";") -> bytes:
    if separator not in {",", ";", "\t"}:
        separator = ";"
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=separator, lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def _excel_column(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def xlsx_export(headers: list[str], rows: list[list[object]], sheet_name: str = "Relatório") -> bytes:
    safe_name = re.sub(r"[\\/*?:\[\]]", " ", sheet_name)[:31] or "Relatório"
    all_rows = [headers, *rows]
    widths = [min(60, max(10, max((len(str(row[i])) if i < len(row) else 0 for row in all_rows), default=10) + 2)) for i in range(len(headers))]
    cols = "".join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i, width in enumerate(widths, 1))
    xml_rows = []
    for row_index, row in enumerate(all_rows, 1):
        cells = []
        for col_index, value in enumerate(row, 1):
            ref = f"{_excel_column(col_index)}{row_index}"
            style = ' s="1"' if row_index == 1 else ""
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}"{style}><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"{style}><is><t>{xml_escape(str(value or ""))}</t></is></c>')
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    last = f"{_excel_column(len(headers))}{max(1, len(all_rows))}"
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
             f'<cols>{cols}</cols><sheetData>{"".join(xml_rows)}</sheetData><autoFilter ref="A1:{last}"/></worksheet>')
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{xml_escape(safe_name)}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF12615D"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf fontId="0" fillId="0" borderId="0"/><xf fontId="1" fillId="2" borderId="0" applyFont="1" applyFill="1"/></cellXfs></styleSheet>')
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def _pdf_text(value: object) -> str:
    text = str(value or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return text.encode("cp1252", "replace").decode("latin1")


def pdf_export(headers: list[str], rows: list[list[object]], *, title: str, company: str, period: str) -> bytes:
    generated = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    printable = [[str(value or "") for value in row] for row in rows]
    per_page = 24
    pages = [printable[i:i + per_page] for i in range(0, len(printable), per_page)] or [[]]
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    page_ids = [5 + index * 2 for index in range(len(pages))]
    objects.append(f"<< /Type /Pages /Kids [{' '.join(f'{item} 0 R' for item in page_ids)}] /Count {len(pages)} >>".encode("ascii"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    for page_number, page_rows in enumerate(pages, 1):
        commands = ["0.07 0.38 0.36 rg 36 770 523 36 re f", "1 1 1 rg", "BT /F2 16 Tf 48 783 Td (BM  Backup Manager Local) Tj ET",
                    "0.1 0.1 0.1 rg", f"BT /F2 15 Tf 40 746 Td ({_pdf_text(title)}) Tj ET",
                    f"BT /F1 9 Tf 40 730 Td (Empresa: {_pdf_text(company or 'Não informada')}  |  Período: {_pdf_text(period)}) Tj ET",
                    f"BT /F1 8 Tf 40 716 Td (Gerado em: {_pdf_text(generated)}  |  Registros: {len(printable)}) Tj ET",
                    "0.88 0.92 0.92 rg 36 690 523 20 re f", "0.1 0.1 0.1 rg"]
        shown_headers = headers[:5]
        x_positions = [40, 145, 250, 355, 460]
        for x, header in zip(x_positions, shown_headers):
            commands.append(f"BT /F2 7 Tf {x} 697 Td ({_pdf_text(str(header)[:18])}) Tj ET")
        y = 677
        for index, row in enumerate(page_rows):
            if index % 2:
                commands.append(f"0.96 0.97 0.98 rg 36 {y - 4} 523 18 re f 0.1 0.1 0.1 rg")
            for x, value in zip(x_positions, row[:5]):
                commands.append(f"BT /F1 7 Tf {x} {y} Td ({_pdf_text(value[:20])}) Tj ET")
            y -= 19
        if not page_rows:
            commands.append("BT /F1 10 Tf 40 660 Td (Nenhum registro encontrado para os filtros selecionados.) Tj ET")
        commands.extend(["0.45 0.45 0.45 rg", "36 32 523 1 re f",
                         f"BT /F1 8 Tf 40 18 Td (Backup Manager Local - relatório administrativo) Tj ET",
                         f"BT /F1 8 Tf 500 18 Td (Página {page_number}/{len(pages)}) Tj ET"])
        stream = "\n".join(commands).encode("latin1", "replace")
        page_id = page_ids[page_number - 1]
        content_id = page_id + 1
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>".encode("ascii"))
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream")
    output = io.BytesIO()
    output.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(output.tell())
        output.write(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.write(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode("ascii"))
    return output.getvalue()
