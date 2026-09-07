from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path

from .storage import load_config, resolve_inside, sha256_file
from .rclone_backend import RcloneError, organized_path as rclone_organized_path, upload as rclone_upload

ACTIVE_STATUSES = ("queued", "validating", "uploading", "retry_wait")
SAFE_LOG_SUCCESS = "\n".join((
    "Iniciando validação do backup.", "Arquivo local validado.",
    "Sincronização simulada iniciada.", "Sincronização simulada concluída.",
    "Nenhum arquivo foi enviado para serviço externo.",
))


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reason_code: str = ""
    safe_message: str = "Backup elegível para sincronização simulada."


def list_targets(conn):
    return conn.execute(
        """SELECT id,uuid,name,provider,mode,is_active,destination_label
           FROM cloud_targets WHERE deleted_at IS NULL ORDER BY id"""
    ).fetchall()


def list_policies(conn):
    return conn.execute(
        """SELECT id,uuid,target_id,scope_type,equipment_id,enabled,sync_new_backups
           FROM cloud_sync_policies WHERE deleted_at IS NULL ORDER BY id"""
    ).fetchall()


def list_queue(conn, *, limit: int = 200):
    return conn.execute(
        """SELECT id,uuid,target_id,backup_id,status,attempt,max_attempts,next_attempt_at,bytes_total
           FROM cloud_sync_items ORDER BY queued_at DESC LIMIT ?""", (limit,)
    ).fetchall()


def _reject(code: str, message: str) -> Eligibility:
    return Eligibility(False, code, message)


def _audit(conn, action: str, item=None, user_id=None, details=None) -> None:
    data = details or {}
    if item is not None:
        data.update({"backup_uuid": item["backup_uuid"] if "backup_uuid" in item.keys() else "",
                     "target_uuid": item["target_uuid"] if "target_uuid" in item.keys() else "",
                     "status": item["status"], "attempt": item["attempt"],
                     "size": item["bytes_total"]})
    conn.execute("INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address) VALUES(?,?,?,?,?, '')",
                 (user_id, action, "cloud_sync", str(item["uuid"] if item is not None else ""),
                  json.dumps(data, ensure_ascii=True, separators=(",", ":"))))


def is_backup_cloud_eligible(conn, backup_id: int, target_id: int, policy_id: int, *, check_duplicate=True) -> Eligibility:
    backup = conn.execute("SELECT * FROM backups WHERE id=?", (backup_id,)).fetchone()
    target = conn.execute("SELECT * FROM cloud_targets WHERE id=?", (target_id,)).fetchone()
    policy = conn.execute("SELECT * FROM cloud_sync_policies WHERE id=?", (policy_id,)).fetchone()
    if not backup or backup["backup_status"] == "deleted" or backup["deleted_at"]:
        return _reject("BACKUP_DELETED", "Backup excluído.")
    if backup["backup_status"] == "trashed" or backup["trash_relative_path"]:
        return _reject("BACKUP_TRASHED", "Backup está na lixeira.")
    if backup["backup_status"] != "available":
        return _reject("BACKUP_NOT_AVAILABLE", "Backup local não está disponível.")
    if not target or target["deleted_at"] or not target["is_active"]:
        return _reject("TARGET_DISABLED", "Destino inativo.")
    if (target["provider"], target["mode"]) != ("simulate", "simulate"):
        return _reject("TARGET_DISABLED", "Provider ou modo do destino inválido.")
    rclone = conn.execute("SELECT status FROM rclone_connections WHERE target_id=?", (target_id,)).fetchone()
    if rclone and rclone["status"] not in {"configured", "verified"}:
        return _reject("RCLONE_NOT_READY", "Destino rclone ainda não está pronto.")
    if not policy or policy["deleted_at"] or not policy["enabled"]:
        return _reject("POLICY_DISABLED", "Política inativa.")
    if policy["target_id"] != target_id or (policy["scope_type"] == "equipment" and policy["equipment_id"] != backup["equipment_id"]):
        return _reject("POLICY_DISABLED", "Política não se aplica ao backup.")
    method = backup["source_method"]
    if (method == "ssh" and not policy["include_ssh"]) or (method == "ftp" and not policy["include_ftp"]):
        return _reject("METHOD_NOT_ALLOWED", "Método de origem não permitido pela política.")
    if policy["minimum_file_size"] is not None and backup["file_size"] < policy["minimum_file_size"]:
        return _reject("FILE_TOO_SMALL", "Arquivo abaixo do tamanho mínimo.")
    if policy["maximum_file_size"] is not None and backup["file_size"] > policy["maximum_file_size"]:
        return _reject("FILE_TOO_LARGE", "Arquivo acima do tamanho máximo.")
    if check_duplicate and conn.execute("SELECT 1 FROM cloud_sync_items WHERE target_id=? AND backup_id=? AND status!='cancelled'", (target_id, backup_id)).fetchone():
        return _reject("DUPLICATE_SYNC_ITEM", "Backup já possui item neste destino.")
    config = load_config(conn)
    try:
        path = resolve_inside(config.backup_directory, backup["relative_path"])
    except (ValueError, OSError):
        return _reject("BACKUP_FILE_MISSING", "Arquivo local ausente ou com caminho inválido.")
    if path.is_symlink() or not path.is_file():
        return _reject("BACKUP_FILE_MISSING", "Arquivo local ausente ou inválido.")
    if path.stat().st_size != backup["file_size"]:
        return _reject("BACKUP_SIZE_MISMATCH", "Tamanho do arquivo local não confere.")
    if not backup["sha256"] or sha256_file(path) != backup["sha256"]:
        return _reject("BACKUP_HASH_MISMATCH", "SHA-256 do arquivo local não confere.")
    return Eligibility(True)


def applicable_policies(conn, backup_id: int, *, automatic=False):
    backup = conn.execute("SELECT equipment_id FROM backups WHERE id=?", (backup_id,)).fetchone()
    if not backup:
        return []
    auto = " AND p.sync_new_backups=1" if automatic else ""
    rows = conn.execute(f"""SELECT p.* FROM cloud_sync_policies p JOIN cloud_targets t ON t.id=p.target_id
        WHERE p.deleted_at IS NULL AND p.enabled=1 AND t.deleted_at IS NULL AND t.is_active=1
          AND (p.scope_type='global' OR (p.scope_type='equipment' AND p.equipment_id=?)){auto}
        ORDER BY p.target_id, CASE p.scope_type WHEN 'equipment' THEN 0 ELSE 1 END""", (backup["equipment_id"],)).fetchall()
    chosen = {}
    for row in rows:
        chosen.setdefault(row["target_id"], row)
    return list(chosen.values())


def enqueue_backup(conn, backup_id: int, *, target_id=None, user_id=None, automatic=False):
    if automatic:
        enabled = conn.execute("SELECT value FROM settings WHERE key='cloud_sync_auto_enqueue'").fetchone()
        if not enabled or enabled[0] != "1":
            return []
    policies = applicable_policies(conn, backup_id, automatic=automatic)
    if target_id is not None:
        policies = [p for p in policies if p["target_id"] == target_id]
    backup = conn.execute("SELECT * FROM backups WHERE id=?", (backup_id,)).fetchone()
    created = []
    for policy in policies:
        eligibility = is_backup_cloud_eligible(conn, backup_id, policy["target_id"], policy["id"])
        if not eligibility.eligible:
            continue
        target = conn.execute("SELECT * FROM cloud_targets WHERE id=?", (policy["target_id"],)).fetchone()
        item_uuid = str(uuid.uuid4())
        try:
            conn.execute("""INSERT INTO cloud_sync_items(uuid,target_id,policy_id,backup_id,equipment_id,provider,mode,max_attempts,
                bytes_total,local_sha256,remote_object_name,remote_folder_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item_uuid, target["id"], policy["id"], backup_id, backup["equipment_id"], target["provider"], target["mode"],
                 target["max_attempts"], backup["file_size"], backup["sha256"], backup["original_filename"], target["remote_folder_id"]))
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                continue
            raise
        _audit(conn, "cloud.sync_queued", details={"backup_uuid": backup["uuid"], "target_uuid": target["uuid"], "size": backup["file_size"]}, user_id=user_id)
        created.append(item_uuid)
    return created


def enqueue_new_backup(conn, backup_id: int) -> list[str]:
    return enqueue_backup(conn, backup_id, automatic=True)


def enable_automatic_backup(conn, target_id: int) -> None:
    """Enable a global SSH/FTP policy for a newly verified external target."""
    policy = conn.execute(
        "SELECT id FROM cloud_sync_policies WHERE target_id=? AND scope_type='global' AND deleted_at IS NULL",
        (target_id,),
    ).fetchone()
    if policy:
        conn.execute("""UPDATE cloud_sync_policies SET enabled=1,sync_new_backups=1,
            include_ssh=1,include_ftp=1,updated_at=CURRENT_TIMESTAMP WHERE id=?""", (policy["id"],))
    else:
        conn.execute("""INSERT INTO cloud_sync_policies(
            uuid,target_id,scope_type,enabled,sync_new_backups,include_ssh,include_ftp)
            VALUES(?,?,'global',1,1,1,1)""", (str(uuid.uuid4()), target_id))
    for key in ("cloud_sync_enabled", "cloud_sync_auto_enqueue"):
        conn.execute("""INSERT INTO settings(key,value,updated_at) VALUES(?,'1',CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value='1',updated_at=CURRENT_TIMESTAMP""", (key,))


def window_open(start, end, current: datetime | None = None) -> bool:
    if not start and not end:
        return True
    if not start or not end:
        return False
    now_time = (current or datetime.now().astimezone()).timetz().replace(tzinfo=None)
    try:
        first, last = clock_time.fromisoformat(start), clock_time.fromisoformat(end)
    except ValueError:
        return False
    if first == last:
        return True
    return first <= now_time < last if first < last else now_time >= first or now_time < last


def _item(conn, item_id):
    return conn.execute("""SELECT i.*,b.uuid backup_uuid,b.original_filename,b.relative_path,b.received_at,e.hostname equipment_hostname,
        COALESCE(g.name,'') equipment_group,
        t.uuid target_uuid,t.is_active,t.deleted_at target_deleted,
        t.sync_window_start,t.sync_window_end,t.retry_base_seconds,t.bandwidth_limit_kbps,p.enabled policy_enabled,p.deleted_at policy_deleted
        FROM cloud_sync_items i JOIN backups b ON b.id=i.backup_id JOIN equipment e ON e.id=b.equipment_id
        LEFT JOIN equipment_groups g ON g.id=e.group_id JOIN cloud_targets t ON t.id=i.target_id
        JOIN cloud_sync_policies p ON p.id=i.policy_id WHERE i.id=?""", (item_id,)).fetchone()


def process_item(conn, item_id: int, *, simulate_failure=False, now=None) -> str:
    current = now or datetime.now(timezone.utc)
    item = _item(conn, item_id)
    if not item or item["status"] not in ("queued", "retry_wait"):
        return "skipped"
    if not window_open(item["sync_window_start"], item["sync_window_end"], current.astimezone()):
        return "window_closed"
    check = is_backup_cloud_eligible(conn, item["backup_id"], item["target_id"], item["policy_id"], check_duplicate=False)
    if not check.eligible:
        conn.execute("UPDATE cloud_sync_items SET status='skipped',finished_at=?,error_code=?,error_message=?,safe_log=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (current.strftime("%Y-%m-%d %H:%M:%S"), check.reason_code, check.safe_message, "Validação local recusou o item. Nenhum arquivo foi enviado.", item_id))
        return "skipped"
    started = time.monotonic()
    attempt = item["attempt"] + 1
    stamp = current.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE cloud_sync_items SET status='validating',attempt=?,started_at=?,error_code=NULL,error_message=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (attempt, stamp, item_id))
    rclone = conn.execute("SELECT remote_name,base_path FROM rclone_connections WHERE target_id=?", (item["target_id"],)).fetchone()
    rclone_real = bool(rclone)
    start_log="Iniciando validação do backup.\nArquivo local validado.\n"+("Envio seguro via rclone iniciado." if rclone_real else "Sincronização simulada iniciada.")
    conn.execute("UPDATE cloud_sync_items SET status='uploading',safe_log=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (start_log,item_id))
    started_item = _item(conn, item_id)
    _audit(conn, "cloud.sync_started", started_item)
    # Network transfers may run for up to an hour. Persist the claimed state and
    # release SQLite's write lock before invoking rclone so web requests and the
    # other workers can continue writing while the upload is in progress.
    conn.commit()
    forced = not rclone_real and (simulate_failure or "simulate_failure" in (conn.execute("SELECT notes FROM cloud_targets WHERE id=?", (item["target_id"],)).fetchone()[0] or "").lower())
    if forced:
        final = "failed" if attempt >= item["max_attempts"] else "retry_wait"
        next_at = None if final == "failed" else current + timedelta(seconds=item["retry_base_seconds"] * (2 ** (attempt - 1)))
        conn.execute("""UPDATE cloud_sync_items SET status=?,next_attempt_at=?,finished_at=?,duration_ms=?,error_code='CLOUD_SIMULATED_FAILURE',
            error_message='Falha controlada na sincronização simulada.',safe_log=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (final, next_at.strftime("%Y-%m-%d %H:%M:%S") if next_at else None, stamp, int((time.monotonic()-started)*1000),
             "Sincronização simulada interrompida por falha controlada. Nenhum arquivo foi enviado.", item_id))
        failed = _item(conn, item_id)
        _audit(conn, "cloud.sync_failed" if final == "failed" else "cloud.sync_retry_scheduled", failed)
        return final
    if rclone_real:
        try:
            config=load_config(conn); path=resolve_inside(config.backup_directory,item["relative_path"])
            system_name = conn.execute("SELECT value FROM settings WHERE key='installation_name'").fetchone()
            remote_path = rclone_organized_path(
                rclone["base_path"], item["equipment_group"], item["equipment_hostname"],
                item["received_at"], system_name=(system_name[0] if system_name else ""))
            uploaded=rclone_upload(path,rclone["remote_name"],remote_path,item["original_filename"],bandwidth_kbps=item["bandwidth_limit_kbps"])
        except (RcloneError, ValueError, OSError) as exc:
            code=exc.code if isinstance(exc,RcloneError) else "RCLONE_UPLOAD_FAILED"
            message=exc.safe_message if isinstance(exc,RcloneError) else "Falha segura no envio via rclone."
            final="failed" if attempt>=item["max_attempts"] or (isinstance(exc,RcloneError) and not exc.transient) else "retry_wait"
            next_at=None if final=="failed" else current+timedelta(seconds=item["retry_base_seconds"]*(2**(attempt-1)))
            conn.execute("""UPDATE cloud_sync_items SET status=?,next_attempt_at=?,finished_at=?,duration_ms=?,error_code=?,error_message=?,safe_log=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
              (final,next_at.strftime("%Y-%m-%d %H:%M:%S") if next_at else None,stamp,int((time.monotonic()-started)*1000),code,message,
               "Envio via rclone falhou de forma segura. Credenciais e saída técnica foram omitidas.",item_id))
            _audit(conn,"cloud.sync_failed" if final=="failed" else "cloud.sync_retry_scheduled",_item(conn,item_id))
            return final
        conn.execute("""UPDATE cloud_sync_items SET status='synced',finished_at=?,next_attempt_at=NULL,duration_ms=?,bytes_uploaded=bytes_total,
          remote_object_id=?,remote_object_name=?,safe_log=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
          (stamp,int((time.monotonic()-started)*1000),uploaded.remote_path,item["original_filename"],
           "Arquivo local validado.\nEnvio via rclone concluído.\nO backup local foi preservado.",item_id))
        _audit(conn,"cloud.rclone_upload_success",_item(conn,item_id))
    else:
        conn.execute("""UPDATE cloud_sync_items SET status='synced',finished_at=?,next_attempt_at=NULL,duration_ms=?,bytes_uploaded=bytes_total,
            remote_object_id=?,safe_log=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (stamp, int((time.monotonic()-started)*1000), f"simulate:{item['uuid']}", SAFE_LOG_SUCCESS, item_id))
    _audit(conn, "cloud.sync_success", _item(conn, item_id))
    return "synced"


def run_once(conn, *, simulate_failure=False, now=None, limit=100):
    current = now or datetime.now(timezone.utc)
    due = conn.execute("""SELECT id FROM cloud_sync_items WHERE status='queued'
        OR (status='retry_wait' AND next_attempt_at<=?) ORDER BY queued_at LIMIT ?""",
        (current.strftime("%Y-%m-%d %H:%M:%S"), limit)).fetchall()
    result = {"processed": 0, "synced": 0, "failed": 0, "retry_wait": 0, "skipped": 0, "window_closed": 0}
    for row in due:
        status = process_item(conn, row["id"], simulate_failure=simulate_failure, now=current)
        result[status] = result.get(status, 0) + 1
        if status != "window_closed": result["processed"] += 1
    conn.execute("INSERT INTO settings(key,value,updated_at) VALUES('cloud_sync_last_worker_at',?,CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP", (current.strftime("%Y-%m-%d %H:%M:%S"),))
    _audit(conn, "cloud.worker_executed", details=result)
    return result


def retry_item(conn, item_id: int, user_id=None) -> bool:
    row = _item(conn, item_id)
    if not row or row["status"] not in ("failed", "retry_wait", "skipped"):
        return False
    check = is_backup_cloud_eligible(conn, row["backup_id"], row["target_id"], row["policy_id"], check_duplicate=False)
    if not check.eligible: return False
    conn.execute("UPDATE cloud_sync_items SET status='queued',attempt=0,next_attempt_at=NULL,finished_at=NULL,error_code=NULL,error_message=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (item_id,))
    _audit(conn, "cloud.sync_queued", _item(conn, item_id), user_id)
    return True


def cancel_item(conn, item_id: int, user_id=None) -> bool:
    row = _item(conn, item_id)
    if not row or row["status"] in ("synced", "cancelled"): return False
    conn.execute("UPDATE cloud_sync_items SET status='cancelled',finished_at=CURRENT_TIMESTAMP,next_attempt_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (item_id,))
    _audit(conn, "cloud.sync_cancelled", _item(conn, item_id), user_id)
    return True


def stats(conn):
    row = conn.execute("""SELECT COUNT(*) total,SUM(status='queued') queued,SUM(status='synced') synced,
        SUM(status='failed') failed,SUM(status='retry_wait') retry_wait,SUM(status='uploading') uploading FROM cloud_sync_items""").fetchone()
    active = conn.execute("SELECT COUNT(*) FROM cloud_targets WHERE is_active=1 AND deleted_at IS NULL").fetchone()[0]
    rclone=conn.execute("""SELECT SUM(status='synced' AND date(finished_at)=date('now')) uploads_today,
      SUM(CASE WHEN status='synced' AND date(finished_at)=date('now') THEN bytes_uploaded ELSE 0 END) bytes_sent_today,
      AVG(CASE WHEN status='synced' THEN duration_ms END) average_upload_ms,
      SUM(status='failed') failures,MAX(CASE WHEN status='synced' THEN finished_at END) last_upload
      FROM cloud_sync_items WHERE target_id IN (SELECT target_id FROM rclone_connections)""").fetchone()
    return {"active_targets": active, **{key: int(row[key] or 0) for key in row.keys()},
      "rclone_uploads_today":int(rclone["uploads_today"] or 0),"rclone_bytes_sent_today":int(rclone["bytes_sent_today"] or 0),
      "rclone_average_upload_ms":int(rclone["average_upload_ms"] or 0),"rclone_failures":int(rclone["failures"] or 0),
      "rclone_last_upload":rclone["last_upload"] or ""}


class WorkerLock:
    def __enter__(self):
        lock_path = Path(os.environ.get("BACKUP_MANAGER_CLOUD_LOCK", "/tmp/backup-manager-cloud-sync.lock"))
        self.handle = lock_path.open("a+")
        fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return self
    def __exit__(self, *_):
        self.handle.close()
