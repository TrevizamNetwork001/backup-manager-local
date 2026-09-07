from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .storage import load_config
from .geoip import database_status, lookup_ip

LEVELS = {"green": 0, "yellow": 1, "red": 2}
SNAPSHOT_PATH = Path(os.environ.get("BACKUP_MANAGER_SERVICE_SNAPSHOT", "/opt/backup-manager-local/data/service-status.json"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _db_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age_hours(value: str | None, now: datetime) -> float | None:
    parsed = _db_datetime(value)
    return max(0.0, (now - parsed).total_seconds() / 3600) if parsed else None


def _worst(items: list[dict], default: str = "green") -> str:
    return max((item["level"] for item in items), key=lambda level: LEVELS[level], default=default)


def _quantity(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def backup_status(last_backup: str | None, now: datetime) -> dict:
    """Classifica a validade usando os mesmos limites operacionais de 24h/72h."""
    age = _age_hours(last_backup, now)
    if age is None:
        return {"age_hours": None, "level": "red", "status": "nunca executado", "protected": False}
    if age > 72:
        return {"age_hours": age, "level": "red", "status": "sem backup válido", "protected": False}
    if age > 24:
        return {"age_hours": age, "level": "yellow", "status": "backup atrasado", "protected": False}
    return {"age_hours": age, "level": "green", "status": "em dia", "protected": True}


def load_service_snapshot(now: datetime | None = None) -> tuple[dict, bool]:
    current = now or _utcnow()
    try:
        if SNAPSHOT_PATH.is_symlink() or not SNAPSHOT_PATH.is_file() or SNAPSHOT_PATH.stat().st_size > 256 * 1024:
            return {}, True
        payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        generated = _db_datetime(payload.get("generated_at"))
        stale = not generated or abs((current - generated).total_seconds()) > 90
        return payload.get("services", {}), stale
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}, True


def service_health(conn, now: datetime | None = None) -> tuple[list[dict], list[dict]]:
    current = now or _utcnow()
    snapshot, stale = load_service_snapshot(current)
    ftp_enabled_row = conn.execute("SELECT value FROM settings WHERE key='ftp_enabled'").fetchone()
    ftp_enabled = bool(ftp_enabled_row and ftp_enabled_row[0] == "1")
    definitions = (
        ("backup-manager-local", "Aplicação", True, "long"),
        ("worker", "Worker", True, "oneshot"),
        ("scheduler", "Scheduler", True, "long"),
        ("ftp-importer", "FTP importer", ftp_enabled, "long"),
        ("pure-ftpd", "Pure-FTPd", ftp_enabled, "long"),
    )
    services, alerts = [], []
    for key, label, expected, kind in definitions:
        state = snapshot.get(key, {})
        active, result, load = state.get("active", "unknown"), state.get("result", "unknown"), state.get("load", "unknown")
        if not expected:
            level, summary = "green", "não habilitado"
        elif stale or not state:
            level, summary = "yellow", "snapshot ausente ou desatualizado"
        elif load == "not-found":
            level, summary = "red", "unit não instalada"
        elif active == "failed" or result == "failed" or (state.get("exit_status", "") not in ("", "0") and kind == "oneshot"):
            level, summary = "red", "falha registrada"
        elif kind == "oneshot" and active == "inactive" and result in ("success", "unknown"):
            level, summary = "green", "última execução concluída"
        elif kind == "oneshot" and active in ("activating", "deactivating"):
            # Start/stop is the normal running state of a short-lived oneshot worker.
            level, summary = "green", "execução em andamento"
        elif active == "active":
            level, summary = "green", "ativo"
        else:
            level, summary = "red", f"estado {active}/{state.get('sub', 'unknown')}"
        item = {"key": key, "label": label, "level": level, "summary": summary,
                "active": active, "sub": state.get("sub", "unknown"), "changed_at": state.get("changed_at", "")}
        services.append(item)
        if level != "green":
            alerts.append({"level": level, "source": label, "message": summary})
    return services, alerts


def equipment_health(conn, now: datetime | None = None) -> tuple[list[dict], list[dict]]:
    current = now or _utcnow()
    rows = conn.execute("""SELECT equipment.id,equipment.hostname,equipment.name,equipment.ssh_backup_driver,
             environments.name AS environment,
             MAX(CASE WHEN backups.backup_status='available' THEN backups.received_at END) AS last_backup,
             (SELECT source_method FROM backups b2 WHERE b2.equipment_id=equipment.id AND b2.backup_status='available'
              ORDER BY datetime(b2.received_at) DESC,b2.id DESC LIMIT 1) AS method,
             EXISTS(SELECT 1 FROM ftp_accounts fa WHERE fa.equipment_id=equipment.id AND fa.is_active=1 AND fa.deleted_at IS NULL) AS has_ftp
             FROM equipment LEFT JOIN backups ON backups.equipment_id=equipment.id
             LEFT JOIN environments ON environments.id=equipment.environment_id
             WHERE equipment.is_active=1 GROUP BY equipment.id""").fetchall()
    result, alerts = [], []
    for row in rows:
        health = backup_status(row["last_backup"], current)
        age, level, status = health["age_hours"], health["level"], health["status"]
        method = (row["method"] or ("ftp" if row["has_ftp"] else "ssh" if row["ssh_backup_driver"] else "-")).upper()
        item = {"id": row["id"], "name": row["name"] or row["hostname"], "hostname": row["hostname"], "environment": row["environment"],
                "last_backup": row["last_backup"], "method": method, "age_hours": age, "level": level, "status": status,
                "protected": health["protected"]}
        result.append(item)
        if level != "green":
            alerts.append({"level": level, "source": row["hostname"], "message": status,
                           "equipment_id": row["id"]})
    result.sort(key=lambda item: (-LEVELS[item["level"]],
                                  -(item["age_hours"] if item["age_hours"] is not None else 10**9), item["hostname"]))
    return result, alerts


def active_job_failures(conn) -> list[dict]:
    """Return only failures that are still the latest terminal run of an active job.

    Historical failures remain stored for reports and success-rate calculations, but
    a later successful run automatically resolves the operational alert.
    """
    return [dict(row) for row in conn.execute("""SELECT r.id,r.job_id,r.equipment_id,r.method,r.status,
        r.created_at,COALESCE(environments.name,'Sem ambiente') environment
        FROM backup_job_runs r
        JOIN backup_jobs j ON j.id=r.job_id
        JOIN equipment e ON e.id=r.equipment_id
        LEFT JOIN environments ON environments.id=e.environment_id
        WHERE j.status='active' AND j.deleted_at IS NULL AND e.is_active=1
          AND r.status IN ('failed','timeout')
          AND NOT EXISTS (
              SELECT 1 FROM backup_job_runs newer
              WHERE newer.job_id=r.job_id
                AND newer.status IN ('success','failed','timeout','cancelled','skipped')
                AND newer.id>r.id
          )
        ORDER BY r.id DESC""")]


def timeline(conn) -> list[dict]:
    labels = {
        "backup.manual_uploaded": "Backup recebido",
        "backup.created_from_ssh": "Backup SSH concluído",
        "backup.created_from_ftp": "Backup FTP concluído",
        "ftp.upload_imported": "Upload FTP recebido",
        "ftp.upload_rejected": "Backup FTP rejeitado",
        "ftp.upload_failed": "Erro FTP",
        "ssh.backup_failed": "Erro SSH",
        "job.run_failed": "Job falhou",
        "job.run_timeout": "Job expirou",
        "lifecycle.health_checked": "Integridade executada",
        "lifecycle.executed": "Limpeza e retenção",
        "lifecycle.backup_restored": "Backup restaurado",
        "lifecycle.policy_updated": "Política de retenção alterada",
    }
    placeholders = ",".join("?" for _ in labels)
    rows = conn.execute(f"""SELECT action,entity,entity_id,details,created_at FROM audit_log
                         WHERE action IN ({placeholders}) ORDER BY id DESC LIMIT 30""", tuple(labels)).fetchall()
    result = []
    for row in rows:
        level = "red" if row["action"] in {"ftp.upload_failed", "ssh.backup_failed", "job.run_failed", "job.run_timeout"} else (
            "yellow" if row["action"] == "ftp.upload_rejected" else "green")
        result.append({"label": labels[row["action"]], "action": row["action"], "level": level,
                       "entity": row["entity"], "entity_id": row["entity_id"], "details": row["details"], "created_at": row["created_at"]})
    return result


def security_activity(conn) -> dict:
    """Summarize authentication pressure, including distributed attempts."""
    totals = conn.execute("""SELECT
        SUM(action='login_failed' AND created_at>=datetime('now','-10 minutes')) failed_10m,
        SUM(action='login_failed' AND created_at>=datetime('now','-24 hours')) failed_24h,
        SUM(action='login_failed' AND created_at>=datetime('now','-7 days')) failed_7d,
        COUNT(DISTINCT CASE WHEN action='login_failed' AND created_at>=datetime('now','-10 minutes') THEN NULLIF(ip_address,'') END) ips_10m,
        COUNT(DISTINCT CASE WHEN action='login_failed' AND created_at>=datetime('now','-24 hours') THEN NULLIF(ip_address,'') END) ips_24h,
        SUM(action='login_throttled' AND created_at>=datetime('now','-24 hours')) throttled_24h,
        SUM(action='login' AND created_at>=datetime('now','-24 hours')) successful_24h
        FROM audit_log WHERE action IN ('login_failed','login_throttled','login')""").fetchone()
    data = {key: int(totals[key] or 0) for key in totals.keys()}
    top_ips = [dict(row) for row in conn.execute("""SELECT COALESCE(NULLIF(ip_address,''),'Desconhecido') value,COUNT(*) attempts,
        MAX(created_at) last_seen FROM audit_log WHERE action='login_failed' AND created_at>=datetime('now','-24 hours')
        GROUP BY COALESCE(NULLIF(ip_address,''),'Desconhecido') ORDER BY attempts DESC,last_seen DESC LIMIT 5""")]
    for row in top_ips:
        row.update(lookup_ip(row["value"]))
    top_accounts = [dict(row) for row in conn.execute("""SELECT COALESCE(NULLIF(entity_id,''),'Não informado') value,COUNT(*) attempts,
        COUNT(DISTINCT NULLIF(ip_address,'')) distinct_ips,MAX(created_at) last_seen FROM audit_log
        WHERE action='login_failed' AND created_at>=datetime('now','-24 hours')
        GROUP BY lower(COALESCE(NULLIF(entity_id,''),'Não informado')) ORDER BY attempts DESC,last_seen DESC LIMIT 5""")]
    recent = [dict(row) for row in conn.execute("""SELECT a.created_at,a.action,a.entity_id,a.ip_address,a.details,
        COALESCE(NULLIF(u.username,''),NULLIF(a.entity_id,''),'Não informado') account
        FROM audit_log a LEFT JOIN users u ON u.id=a.user_id
        WHERE a.action IN ('login_failed','login_throttled','login') ORDER BY a.id DESC LIMIT 20""")]
    for row in recent:
        row.update(lookup_ip(row["ip_address"] or ""))
    if data["failed_10m"] >= 50 or data["ips_10m"] >= 20 or data["throttled_24h"] >= 10:
        level, label = "red", "Crítico"
    elif data["failed_10m"] >= 10 or data["failed_24h"] >= 20 or data["ips_24h"] >= 10 or data["throttled_24h"]:
        level, label = "yellow", "Atenção"
    else:
        level, label = "green", "Normal"
    reasons = []
    if data["failed_10m"]: reasons.append(_quantity(data["failed_10m"], "falha em 10 min", "falhas em 10 min"))
    if data["ips_10m"]: reasons.append(_quantity(data["ips_10m"], "IP em 10 min", "IPs em 10 min"))
    if data["throttled_24h"]: reasons.append(_quantity(data["throttled_24h"], "bloqueio em 24h", "bloqueios em 24h"))
    return {**data, "level": level, "label": label, "reasons": reasons, "top_ips": top_ips,
            "top_accounts": top_accounts, "recent": recent, "geoip": database_status()}


def dashboard_snapshot(conn, now: datetime | None = None) -> dict:
    current = now or _utcnow()
    config = load_config(conn)
    disk = shutil.disk_usage(config.storage_root)
    disk_percent = int((disk.used / disk.total) * 100) if disk.total else 0
    alerts: list[dict] = []
    services, service_alerts = service_health(conn, current); alerts.extend(service_alerts)
    equipment, equipment_alerts = equipment_health(conn, current); alerts.extend(equipment_alerts)
    security = security_activity(conn)
    if security["level"] != "green":
        detail = ", ".join(security["reasons"][:2]) or "atividade de autenticação incomum"
        alerts.append({"level": security["level"], "source": "Segurança", "message": detail})
    protected = sum(1 for item in equipment if item["protected"])
    unprotected = len(equipment) - protected
    coverage_percent = round((protected / len(equipment)) * 100) if equipment else None
    coverage_level = "green" if coverage_percent is None or coverage_percent == 100 else ("yellow" if coverage_percent >= 80 else "red")
    unprotected_reasons: dict[str, int] = {}
    for item in equipment:
        if not item["protected"]:
            unprotected_reasons[item["status"]] = unprotected_reasons.get(item["status"], 0) + 1
    if disk_percent >= 95:
        alerts.append({"level": "red", "source": "Storage", "message": f"disco com {disk_percent}% de uso"})
    elif disk_percent >= 85:
        alerts.append({"level": "yellow", "source": "Storage", "message": f"disco com {disk_percent}% de uso"})
    failed_24h = conn.execute("SELECT COUNT(*) FROM backup_job_runs WHERE status IN ('failed','timeout') AND created_at>=datetime('now','-24 hours')").fetchone()[0]
    failed_7d = conn.execute("SELECT COUNT(*) FROM backup_job_runs WHERE status IN ('failed','timeout') AND created_at>=datetime('now','-7 days')").fetchone()[0]
    success_7d = conn.execute("SELECT COUNT(*) FROM backup_job_runs WHERE status='success' AND created_at>=datetime('now','-7 days')").fetchone()[0]
    executions_7d = success_7d + failed_7d
    success_rate = round(success_7d * 100 / executions_7d, 1) if executions_7d else None
    unresolved_failures = active_job_failures(conn)
    active_failed = len(unresolved_failures)
    if active_failed:
        alerts.append({"level": "red", "source": "Jobs", "message": f"{_quantity(active_failed, 'falha ativa', 'falhas ativas')}"})
    ftp_failed = conn.execute("SELECT COUNT(*) FROM ftp_received_files WHERE status='failed' AND updated_at>=datetime('now','-24 hours')").fetchone()[0]
    if ftp_failed:
        alerts.append({"level": "red", "source": "FTP", "message": f"{_quantity(ftp_failed, 'falha', 'falhas')} nas últimas 24h"})
    ssh_failed = sum(1 for item in unresolved_failures if item["method"] == "ssh")
    if ssh_failed:
        alerts.append({"level": "red", "source": "SSH", "message": f"{_quantity(ssh_failed, 'falha ativa', 'falhas ativas')}"})
    rejected_24h = conn.execute("SELECT COUNT(*) FROM ftp_received_files WHERE status='rejected' AND updated_at>=datetime('now','-24 hours')").fetchone()[0]
    if rejected_24h:
        alerts.append({"level": "yellow", "source": "FTP", "message": f"{_quantity(rejected_24h, 'rejeitado', 'rejeitados')} nas últimas 24h"})
    cloud = conn.execute("""SELECT
        (SELECT COUNT(*) FROM cloud_targets WHERE is_active=1 AND deleted_at IS NULL) active_targets,
        SUM(status='queued') queued,SUM(status='synced' AND date(finished_at)=date('now')) synced_today,
        SUM(status='failed' AND updated_at>=datetime('now','-24 hours')) recent_failed,
        SUM(status='uploading' AND updated_at<datetime('now','-15 minutes')) stuck_uploading,
        MAX(CASE WHEN status='synced' THEN finished_at END) last_sync FROM cloud_sync_items""").fetchone()
    cloud = {key: cloud[key] or 0 for key in cloud.keys()}
    stale_queue = conn.execute("SELECT COUNT(*) FROM cloud_sync_items WHERE status='queued' AND queued_at<datetime('now','-15 minutes')").fetchone()[0]
    if cloud["recent_failed"]: alerts.append({"level":"red","source":"Backup externo","message":f"{_quantity(cloud['recent_failed'], 'falha', 'falhas')} recentes"})
    if stale_queue: alerts.append({"level":"yellow","source":"Backup externo","message":f"{_quantity(stale_queue, 'item', 'itens')} com fila parada"})
    if cloud["stuck_uploading"]: alerts.append({"level":"red","source":"Backup externo","message":f"{_quantity(cloud['stuck_uploading'], 'item preso', 'itens presos')} em envio"})
    last_worker_row = conn.execute("SELECT value FROM settings WHERE key='cloud_sync_last_worker_at'").fetchone()
    worker_age = _age_hours(last_worker_row[0] if last_worker_row else None, current)
    if cloud["active_targets"] and (worker_age is None or worker_age > 0.1):
        alerts.append({"level":"yellow","source":"Cloud sync worker","message":"worker sem execução recente"})
    telegram = conn.execute("""SELECT
        SUM(status='sent' AND date(sent_at)=date('now')) sent_today,
        SUM(status IN ('queued','validating','preparing','uploading','retry_wait')) queued,
        SUM(status='failed' AND updated_at>=datetime('now','-24 hours')) recent_failed,
        SUM(CASE WHEN status='sent' AND date(sent_at)=date('now') THEN bytes_sent ELSE 0 END) bytes_today,
        SUM(status='uploading' AND updated_at<datetime('now','-15 minutes')) stuck_uploading,
        MAX(CASE WHEN status='sent' THEN sent_at END) last_file FROM telegram_backup_items""").fetchone()
    telegram = {key: telegram[key] or 0 for key in telegram.keys()}
    telegram_stale = conn.execute("SELECT COUNT(*) FROM telegram_backup_items WHERE status='queued' AND queued_at<datetime('now','-15 minutes')").fetchone()[0]
    if telegram["recent_failed"]: alerts.append({"level":"red","source":"Telegram backup","message":f"{_quantity(telegram['recent_failed'], 'falha', 'falhas')} recentes"})
    if telegram_stale: alerts.append({"level":"yellow","source":"Telegram backup","message":f"{_quantity(telegram_stale, 'item', 'itens')} com fila parada"})
    if telegram["stuck_uploading"]: alerts.append({"level":"red","source":"Telegram backup","message":f"{_quantity(telegram['stuck_uploading'], 'item preso', 'itens presos')} em uploading"})
    telegram_notifications = conn.execute("""SELECT
        (SELECT COUNT(*) FROM notification_history WHERE status='sent' AND date(created_at)=date('now')) delivered_today,
        (SELECT COUNT(*) FROM notification_history WHERE status='failed' AND created_at>=datetime('now','-24 hours')) recent_failed,
        (SELECT MAX(sent_at) FROM telegram_summary_runs WHERE status='sent') last_summary""").fetchone()
    telegram_notifications = {key: telegram_notifications[key] or 0 for key in telegram_notifications.keys()}
    health = conn.execute("SELECT report_json,created_at FROM lifecycle_runs WHERE mode='health_check' ORDER BY id DESC LIMIT 1").fetchone()
    health_problems = 0
    if health:
        try: health_problems = len(json.loads(health["report_json"]).get("problems", []))
        except (ValueError, TypeError): health_problems = 1
        if health_problems: alerts.append({"level": "red", "source": "Integridade", "message": f"{_quantity(health_problems, 'problema', 'problemas')} na última verificação de integridade"})
    else:
        alerts.append({"level": "yellow", "source": "Integridade", "message": "Verificação de integridade pendente. A rotina de health check ainda não foi executada."})
    alerts.sort(key=lambda item: (-LEVELS[item["level"]], item["source"]))
    counts = {}
    for method in ("ssh", "ftp"):
        counts[method] = conn.execute("SELECT COUNT(*) FROM backups WHERE source_method=? AND backup_status='available'", (method,)).fetchone()[0]
    counts.update({
        "rejected": conn.execute("SELECT COUNT(*) FROM ftp_received_files WHERE status='rejected'").fetchone()[0],
        "trash": conn.execute("SELECT COUNT(*) FROM backups WHERE backup_status='trashed'").fetchone()[0],
        "available": conn.execute("SELECT COUNT(*) FROM backups WHERE backup_status='available'").fetchone()[0],
        "jobs_active": conn.execute("SELECT COUNT(*) FROM backup_jobs WHERE status='active'").fetchone()[0],
        "jobs_queued": conn.execute("SELECT COUNT(*) FROM backup_job_runs WHERE status='queued'").fetchone()[0],
        "audit": conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        "backups_today": conn.execute("SELECT COUNT(*) FROM backups WHERE backup_status='available' AND date(received_at)=date('now')").fetchone()[0],
    })
    bytes_row = conn.execute("SELECT COALESCE(SUM(file_size),0) FROM backups WHERE backup_status='available'").fetchone()
    last_backup = conn.execute("""SELECT backups.id,backups.received_at,backups.source_method,equipment.id equipment_id,
        equipment.name equipment_name,equipment.hostname,environments.name environment
        FROM backups JOIN equipment ON equipment.id=backups.equipment_id
        LEFT JOIN environments ON environments.id=equipment.environment_id
        WHERE backups.backup_status='available' ORDER BY datetime(backups.received_at) DESC,backups.id DESC LIMIT 1""").fetchone()
    next_backup = conn.execute("""SELECT backup_jobs.uuid,backup_jobs.next_run_at,backup_jobs.schedule_type,
        backup_jobs.schedule_time,equipment.id equipment_id,equipment.name equipment_name,equipment.hostname,
        environments.name environment FROM backup_jobs JOIN equipment ON equipment.id=backup_jobs.equipment_id
        LEFT JOIN environments ON environments.id=equipment.environment_id
        WHERE backup_jobs.status='active' AND backup_jobs.schedule_enabled=1 AND backup_jobs.next_run_at IS NOT NULL
        ORDER BY datetime(backup_jobs.next_run_at),backup_jobs.id LIMIT 1""").fetchone()
    upcoming_backups = conn.execute("""SELECT backup_jobs.uuid,backup_jobs.next_run_at,backup_jobs.schedule_type,
        backup_jobs.method,equipment.id equipment_id,equipment.name equipment_name,equipment.hostname
        FROM backup_jobs JOIN equipment ON equipment.id=backup_jobs.equipment_id
        WHERE backup_jobs.status='active' AND backup_jobs.schedule_enabled=1 AND backup_jobs.next_run_at IS NOT NULL
        ORDER BY datetime(backup_jobs.next_run_at),backup_jobs.id LIMIT 3""").fetchall()
    running_operations = conn.execute("""SELECT backup_job_runs.uuid,backup_job_runs.method,backup_job_runs.status,
        backup_job_runs.started_at,equipment.id equipment_id,equipment.name equipment_name,equipment.hostname
        FROM backup_job_runs JOIN equipment ON equipment.id=backup_job_runs.equipment_id
        WHERE backup_job_runs.status IN ('running','queued')
        ORDER BY CASE backup_job_runs.status WHEN 'running' THEN 0 ELSE 1 END,backup_job_runs.id LIMIT 5""").fetchall()
    environment_failures: dict[str, int] = {}
    for failure in unresolved_failures:
        environment_failures[failure["environment"]] = environment_failures.get(failure["environment"], 0) + 1
    environments = []
    for name in sorted({item["environment"] or "Sem ambiente" for item in equipment}):
        items = [item for item in equipment if (item["environment"] or "Sem ambiente") == name]
        env_protected = sum(1 for item in items if item["protected"])
        environments.append({"name": name, "equipment": len(items), "protected": env_protected,
                             "percent": round(env_protected * 100 / len(items)),
                             "failures_24h": environment_failures.get(name, 0)})
    environments.sort(key=lambda item: (-item["failures_24h"], item["percent"], item["name"]))
    method_counts: dict[str, int] = {}
    for item in equipment:
        method_counts[item["method"]] = method_counts.get(item["method"], 0) + 1
    service_levels = {item["key"]: item["level"] for item in services}
    indicators = [
        {"label": "SSH", "level": "red" if ssh_failed else "green", "value": f"{_quantity(ssh_failed, 'falha', 'falhas')} em 24h"},
        {"label": "FTP", "level": "red" if ftp_failed else "yellow" if rejected_24h else service_levels.get("pure-ftpd", "yellow"),
         "value": f"{_quantity(ftp_failed, 'falha', 'falhas')} / {_quantity(rejected_24h, 'rejeitado', 'rejeitados')}"},
        {"label": "Worker", "level": service_levels.get("worker", "yellow"), "value": "processamento"},
        {"label": "Scheduler", "level": service_levels.get("scheduler", "yellow"), "value": f"{_quantity(counts['jobs_queued'], 'job', 'jobs')} na fila"},
        {"label": "Storage", "level": "red" if health_problems else "yellow" if not health else "green", "value": _quantity(health_problems, "problema", "problemas")},
        {"label": "Disco", "level": "red" if disk_percent >= 95 else "yellow" if disk_percent >= 85 else "green", "value": f"{disk_percent}% usado"},
        {"label": "Equipamentos", "level": _worst(equipment, "green"), "value": _quantity(len(equipment), "ativo", "ativos")},
        {"label": "Jobs", "level": "red" if active_failed else "green", "value": _quantity(active_failed, "falha ativa", "falhas ativas")},
        {"label": "Backup externo", "level": "red" if cloud["recent_failed"] or cloud["stuck_uploading"] else "yellow" if stale_queue else "green",
         "value": f"{_quantity(cloud['active_targets'], 'destino', 'destinos')} / {_quantity(cloud['queued'], 'item', 'itens')} na fila"},
        {"label": "Telegram backup", "level": "red" if telegram["recent_failed"] or telegram["stuck_uploading"] else "yellow" if telegram_stale else "green",
         "value": f"{_quantity(telegram['sent_today'], 'enviado', 'enviados')} hoje / {_quantity(telegram['queued'], 'item', 'itens')} na fila"},
        {"label": "Telegram notificações", "level": "red" if telegram_notifications["recent_failed"] else "green",
         "value": f"{_quantity(telegram_notifications['delivered_today'], 'entrega', 'entregas')} hoje"},
    ]
    return {"level": _worst(alerts), "generated_at": current.strftime("%Y-%m-%d %H:%M:%S"), "alerts": alerts,
            "services": services, "equipment": equipment, "timeline": timeline(conn), "counts": counts, "indicators": indicators,
            "storage": {"total": disk.total, "used": disk.used, "free": disk.free, "percent": disk_percent,
                        "backup_bytes": bytes_row[0]}, "health": {"problems": health_problems, "last_check": health["created_at"] if health else None},
            "jobs": {"failed_24h": failed_24h, "failed_7d": failed_7d, "active_failed": active_failed}, "cloud": cloud, "telegram": telegram,
            "telegram_notifications": telegram_notifications, "security": security,
            "coverage": {"total": len(equipment), "protected": protected, "unprotected": unprotected,
                         "percent": coverage_percent, "level": coverage_level, "reasons": unprotected_reasons},
            "success_rate": {"percent": success_rate, "success": success_7d, "failed": failed_7d,
                             "total": executions_7d},
            "last_backup": dict(last_backup) if last_backup else None,
            "next_backup": dict(next_backup) if next_backup else None,
            "upcoming_backups": [dict(row) for row in upcoming_backups],
            "running_operations": [dict(row) for row in running_operations],
            "environments": environments[:5], "method_distribution": method_counts}
