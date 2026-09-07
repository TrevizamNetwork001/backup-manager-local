from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .storage import atomic_move, filesystem_usage, load_config, resolve_inside, sha256_file, trash_relative_path

CONFIRMATION = "MOVER_PARA_LIXEIRA"
PURGE_CONFIRMATION = "APAGAR_DEFINITIVAMENTE"
EMPTY_TRASH_CONFIRMATION = "ESVAZIAR_LIXEIRA"


@dataclass(frozen=True)
class Policy:
    max_count: int
    max_age_days: int
    trash_retention_days: int
    rejected_retention_days: int
    is_enabled: bool = True


def _now(value: datetime | None = None) -> datetime:
    return value or datetime.now(timezone.utc)


def _sql_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _audit(conn, user_id, action: str, entity_id: str, details: dict) -> None:
    conn.execute(
        "INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address) VALUES(?,?,'lifecycle',?,?, '')",
        (user_id, action, entity_id, json.dumps(details, separators=(",", ":"))),
    )


def global_policy(conn) -> Policy:
    row = conn.execute("SELECT * FROM retention_policies WHERE equipment_id IS NULL").fetchone()
    if not row:
        return Policy(0, 365, 30, 30)
    return Policy(row["max_count"] or 0, row["max_age_days"] or 0,
                  row["trash_retention_days"] or 30, row["rejected_retention_days"] or 30,
                  bool(row["is_enabled"]))


def effective_policy(conn, equipment_id: int) -> Policy:
    base = global_policy(conn)
    row = conn.execute("SELECT * FROM retention_policies WHERE equipment_id=?", (equipment_id,)).fetchone()
    if not row:
        return base
    return Policy(
        base.max_count if row["max_count"] is None else row["max_count"],
        base.max_age_days if row["max_age_days"] is None else row["max_age_days"],
        base.trash_retention_days if row["trash_retention_days"] is None else row["trash_retention_days"],
        base.rejected_retention_days if row["rejected_retention_days"] is None else row["rejected_retention_days"],
        bool(row["is_enabled"]),
    )


def save_policy(conn, *, equipment_id: int | None, max_count: int | None, max_age_days: int | None,
                trash_days: int | None, rejected_days: int | None, enabled: bool = True) -> None:
    if equipment_id is None and None in (max_count, max_age_days, trash_days, rejected_days):
        raise ValueError("A politica global exige todos os valores.")
    for value in (max_count, max_age_days):
        if value is not None and value < 0:
            raise ValueError("Quantidade e idade nao podem ser negativas.")
    for value in (trash_days, rejected_days):
        if value is not None and value < 1:
            raise ValueError("Retencoes da lixeira e rejeitados devem ser positivas.")
    row = conn.execute("SELECT id FROM retention_policies WHERE equipment_id IS ?", (equipment_id,)).fetchone()
    values = (max_count, max_age_days, trash_days, rejected_days, int(enabled))
    if row:
        conn.execute("""UPDATE retention_policies SET max_count=?,max_age_days=?,trash_retention_days=?,
                     rejected_retention_days=?,is_enabled=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""", (*values, row["id"]))
    else:
        conn.execute("""INSERT INTO retention_policies(equipment_id,max_count,max_age_days,trash_retention_days,
                     rejected_retention_days,is_enabled) VALUES(?,?,?,?,?,?)""", (equipment_id, *values))


def candidates(conn, now: datetime | None = None) -> list[dict]:
    current = _now(now)
    result: list[dict] = []
    equipment_ids = [row[0] for row in conn.execute(
        "SELECT DISTINCT equipment_id FROM backups WHERE backup_status='available'")]
    for equipment_id in equipment_ids:
        policy = effective_policy(conn, equipment_id)
        if not policy.is_enabled:
            continue
        rows = conn.execute("""SELECT * FROM backups WHERE equipment_id=? AND backup_status='available'
                             ORDER BY datetime(received_at) DESC,id DESC""", (equipment_id,)).fetchall()
        cutoff = current - timedelta(days=policy.max_age_days) if policy.max_age_days else None
        for index, row in enumerate(rows):
            received = datetime.fromisoformat(row["received_at"].replace(" ", "T")).replace(tzinfo=timezone.utc)
            reasons = []
            if policy.max_count and index >= policy.max_count:
                reasons.append(f"excede quantidade {policy.max_count}")
            if cutoff and received < cutoff:
                reasons.append(f"idade superior a {policy.max_age_days} dias")
            if reasons:
                result.append({"row": row, "reason": "; ".join(reasons), "policy": policy})
    return result


def _new_run(conn, mode: str, user_id, report: dict) -> tuple[int, str]:
    run_uuid = str(uuid.uuid4())
    cursor = conn.execute("""INSERT INTO lifecycle_runs(uuid,mode,status,requested_by_user_id,candidate_count,
                           candidate_bytes,report_json) VALUES(?,?,'running',?,?,?,?)""",
                          (run_uuid, mode, user_id, report["candidate_count"], report["candidate_bytes"],
                           json.dumps(report, separators=(",", ":"))))
    return cursor.lastrowid, run_uuid


def simulate(conn, *, user_id=None, now: datetime | None = None) -> dict:
    current = _now(now)
    selected = candidates(conn, current)
    policy = global_policy(conn)
    rejected_cutoff = _sql_time(current - timedelta(days=policy.rejected_retention_days))
    rejected = conn.execute("""SELECT ftp_received_files.*,ftp_accounts.uuid AS account_uuid FROM ftp_received_files
                            JOIN ftp_accounts ON ftp_accounts.id=ftp_received_files.ftp_account_id
                            WHERE ftp_received_files.status='rejected' AND lifecycle_trashed_at IS NULL
                            AND processed_at IS NOT NULL AND processed_at < ?""", (rejected_cutoff,)).fetchall()
    expired_backups = conn.execute("SELECT * FROM backups WHERE backup_status='trashed' AND trash_expires_at <= ?", (_sql_time(current),)).fetchall()
    expired_rejected = conn.execute("SELECT * FROM ftp_received_files WHERE lifecycle_expires_at <= ?", (_sql_time(current),)).fetchall()
    report = {
        "candidate_count": len(selected),
        "candidate_bytes": sum(item["row"]["file_size"] for item in selected),
        "rejected_count": len(rejected),
        "rejected_bytes": sum(row["file_size"] for row in rejected),
        "purge_count": len(expired_backups) + len(expired_rejected),
        "purge_bytes": sum(row["file_size"] for row in expired_backups) + sum(row["file_size"] for row in expired_rejected),
        "items": [{"uuid": item["row"]["uuid"], "equipment_id": item["row"]["equipment_id"],
                   "filename": item["row"]["original_filename"], "size": item["row"]["file_size"],
                   "sha256": item["row"]["sha256"], "reason": item["reason"]} for item in selected],
    }
    run_id, run_uuid = _new_run(conn, "simulation", user_id, report)
    for item in selected:
        row = item["row"]
        conn.execute("""INSERT INTO lifecycle_items(run_id,backup_id,action,reason,file_size,sha256,
                     source_relative_path,result) VALUES(?,?,'trash_backup',?,?,?,?, 'planned')""",
                     (run_id, row["id"], item["reason"], row["file_size"], row["sha256"], row["relative_path"]))
    config = load_config(conn)
    for row in rejected:
        conn.execute("""INSERT INTO lifecycle_items(run_id,ftp_received_file_id,action,reason,file_size,sha256,
                     source_relative_path,result) VALUES(?,?,'trash_rejected',?,?,?,?, 'planned')""",
                     (run_id, row["id"], f"rejeitado ha mais de {policy.rejected_retention_days} dias", row["file_size"],
                      row["sha256"], str(_rejected_path(config, row))))
    for row in expired_backups:
        conn.execute("""INSERT INTO lifecycle_items(run_id,backup_id,action,reason,file_size,sha256,
                     trash_relative_path,result) VALUES(?,?,'purge_backup','retencao da lixeira expirada',?,?,?,'planned')""",
                     (run_id, row["id"], row["file_size"], row["sha256"], row["trash_relative_path"]))
    for row in expired_rejected:
        conn.execute("""INSERT INTO lifecycle_items(run_id,ftp_received_file_id,action,reason,file_size,sha256,
                     trash_relative_path,result) VALUES(?,?,'purge_rejected','retencao da lixeira expirada',?,?,?,'planned')""",
                     (run_id, row["id"], row["file_size"], row["sha256"], row["lifecycle_trash_relative_path"]))
    conn.execute("UPDATE lifecycle_runs SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE id=?", (run_id,))
    _audit(conn, user_id, "lifecycle.simulated", run_uuid,
           {"candidates": report["candidate_count"], "bytes": report["candidate_bytes"],
            "rejected": report["rejected_count"], "purge": report["purge_count"], "purge_bytes": report["purge_bytes"]})
    report["run_uuid"] = run_uuid
    return report


def execute(conn, *, confirmation: str, user_id=None, automatic: bool = False,
            now: datetime | None = None) -> dict:
    if not automatic and confirmation != CONFIRMATION:
        raise ValueError(f"Confirmacao obrigatoria: {CONFIRMATION}")
    current = _now(now)
    selected = candidates(conn, current)
    report = {"candidate_count": len(selected), "candidate_bytes": sum(x["row"]["file_size"] for x in selected), "items": []}
    run_id, run_uuid = _new_run(conn, "automatic" if automatic else "execution", user_id, report)
    config = load_config(conn)
    moved_count = moved_bytes = 0
    try:
        for item in selected:
            row, policy = item["row"], item["policy"]
            source = resolve_inside(config.backup_directory, row["relative_path"])
            target_rel = trash_relative_path(row["uuid"])
            target = resolve_inside(config.trash_directory, target_rel)
            result, error = "completed", ""
            try:
                if not source.is_file():
                    result = "missing"
                else:
                    atomic_move(source, target)
                    conn.execute("""UPDATE backups SET backup_status='trashed',deleted_at=?,deleted_by_user_id=?,
                                 trash_relative_path=?,trash_expires_at=? WHERE id=? AND backup_status='available'""",
                                 (_sql_time(current), user_id, target_rel,
                                  _sql_time(current + timedelta(days=policy.trash_retention_days)), row["id"]))
                    moved_count += 1
                    moved_bytes += row["file_size"]
            except OSError as exc:
                result, error = "failed", str(exc)[:240]
            conn.execute("""INSERT INTO lifecycle_items(run_id,backup_id,action,reason,file_size,sha256,
                         source_relative_path,trash_relative_path,result,error_message) VALUES(?,?,'trash_backup',?,?,?,?,?,?,?)""",
                         (run_id, row["id"], item["reason"], row["file_size"], row["sha256"], row["relative_path"], target_rel, result, error))
            report["items"].append({"uuid": row["uuid"], "result": result, "reason": item["reason"]})
        rejected_count = trash_rejected(conn, run_id, current)
        purged_count, purged_bytes = purge_expired(conn, run_id, current)
        conn.execute("""UPDATE lifecycle_runs SET status='completed',moved_count=?,moved_bytes=?,purged_count=?,
                     purged_bytes=?,rejected_count=?,report_json=?,completed_at=CURRENT_TIMESTAMP WHERE id=?""",
                     (moved_count, moved_bytes, purged_count, purged_bytes, rejected_count,
                      json.dumps(report, separators=(",", ":")), run_id))
        conn.execute("UPDATE settings SET value=?,updated_at=CURRENT_TIMESTAMP WHERE key='lifecycle_last_run_at'", (_sql_time(current),))
        _audit(conn, user_id, "lifecycle.executed", run_uuid,
               {"moved": moved_count, "bytes": moved_bytes, "purged": purged_count,
                "purged_bytes": purged_bytes, "rejected": rejected_count, "automatic": automatic})
    except Exception:
        conn.execute("UPDATE lifecycle_runs SET status='failed',completed_at=CURRENT_TIMESTAMP WHERE id=?", (run_id,))
        raise
    return {**report, "run_uuid": run_uuid, "moved_count": moved_count, "moved_bytes": moved_bytes,
            "purged_count": purged_count, "purged_bytes": purged_bytes, "rejected_count": rejected_count}


def _rejected_path(config, row) -> Path:
    name = f"{row['uuid']}-{row['original_filename']}"
    return config.storage_root / "ftp-incoming" / "accounts" / row["account_uuid"] / "rejected" / name


def trash_rejected(conn, run_id: int, current: datetime) -> int:
    policy = global_policy(conn)
    cutoff = _sql_time(current - timedelta(days=policy.rejected_retention_days))
    rows = conn.execute("""SELECT ftp_received_files.*,ftp_accounts.uuid AS account_uuid FROM ftp_received_files
                         JOIN ftp_accounts ON ftp_accounts.id=ftp_received_files.ftp_account_id
                         WHERE ftp_received_files.status='rejected' AND lifecycle_trashed_at IS NULL
                         AND processed_at IS NOT NULL AND processed_at < ?""", (cutoff,)).fetchall()
    config = load_config(conn)
    count = 0
    for row in rows:
        source = _rejected_path(config, row)
        target_rel = str(Path("rejected") / current.strftime("%Y/%m") / f"{row['uuid']}-{row['original_filename']}")
        target = resolve_inside(config.trash_directory, target_rel)
        result = "missing"
        if source.is_file():
            atomic_move(source, target)
            result, count = "completed", count + 1
            conn.execute("""UPDATE ftp_received_files SET lifecycle_trash_relative_path=?,lifecycle_trashed_at=?,
                         lifecycle_expires_at=? WHERE id=?""", (target_rel, _sql_time(current),
                         _sql_time(current + timedelta(days=policy.trash_retention_days)), row["id"]))
        conn.execute("""INSERT INTO lifecycle_items(run_id,ftp_received_file_id,action,reason,file_size,sha256,
                     source_relative_path,trash_relative_path,result) VALUES(?,?,'trash_rejected',?,?,?,?,?,?)""",
                     (run_id, row["id"], f"rejeitado ha mais de {policy.rejected_retention_days} dias", row["file_size"],
                      row["sha256"], str(source), target_rel, result))
    return count


def purge_expired(conn, run_id: int, current: datetime) -> tuple[int, int]:
    config = load_config(conn)
    count = total = 0
    for row in conn.execute("SELECT * FROM backups WHERE backup_status='trashed' AND trash_expires_at <= ?", (_sql_time(current),)).fetchall():
        path = resolve_inside(config.trash_directory, row["trash_relative_path"])
        result = "missing"
        if path.is_file():
            path.unlink()
            result, count, total = "completed", count + 1, total + row["file_size"]
        conn.execute("UPDATE backups SET backup_status='deleted',relative_path=NULL,trash_relative_path=NULL WHERE id=?", (row["id"],))
        conn.execute("""INSERT INTO lifecycle_items(run_id,backup_id,action,reason,file_size,sha256,trash_relative_path,result)
                     VALUES(?,?,'purge_backup','retencao da lixeira expirada',?,?,?,?)""",
                     (run_id, row["id"], row["file_size"], row["sha256"], row["trash_relative_path"], result))
    for row in conn.execute("SELECT * FROM ftp_received_files WHERE lifecycle_expires_at <= ?", (_sql_time(current),)).fetchall():
        path = resolve_inside(config.trash_directory, row["lifecycle_trash_relative_path"])
        result = "missing"
        if path.is_file():
            path.unlink(); result, count, total = "completed", count + 1, total + row["file_size"]
        conn.execute("UPDATE ftp_received_files SET lifecycle_trash_relative_path=NULL,lifecycle_expires_at=NULL WHERE id=?", (row["id"],))
        conn.execute("""INSERT INTO lifecycle_items(run_id,ftp_received_file_id,action,reason,file_size,sha256,
                     trash_relative_path,result) VALUES(?,?,'purge_rejected','retencao da lixeira expirada',?,?,?,?)""",
                     (run_id, row["id"], row["file_size"], row["sha256"], row["lifecycle_trash_relative_path"], result))
    return count, total


def purge_backup(conn, backup_uuid: str, *, confirmation: str, user_id=None) -> dict:
    """Permanently remove one already-trashed file while retaining its database history."""
    if confirmation != PURGE_CONFIRMATION:
        raise ValueError(f"Confirmação obrigatória: {PURGE_CONFIRMATION}")
    row = conn.execute("SELECT * FROM backups WHERE uuid=? AND backup_status='trashed'", (backup_uuid,)).fetchone()
    if not row:
        raise ValueError("Backup não está disponível na lixeira.")
    config = load_config(conn)
    path = resolve_inside(config.trash_directory, row["trash_relative_path"])
    if path.is_file():
        if sha256_file(path) != row["sha256"]:
            raise ValueError("Hash da lixeira diverge; exclusão definitiva recusada.")
        path.unlink()
        result = "deleted"
    else:
        result = "already_missing"
    conn.execute("""UPDATE backups SET backup_status='deleted',relative_path=NULL,trash_relative_path=NULL,
                 trash_expires_at=NULL WHERE id=?""", (row["id"],))
    _audit(conn, user_id, "lifecycle.backup_purged", backup_uuid,
           {"bytes": row["file_size"], "result": result})
    return {"uuid": backup_uuid, "bytes": row["file_size"], "result": result}


def empty_trash(conn, *, confirmation: str, user_id=None) -> dict:
    """Purge valid files from controlled trash; mismatches stay recoverable and are reported."""
    if confirmation != EMPTY_TRASH_CONFIRMATION:
        raise ValueError(f"Confirmação obrigatória: {EMPTY_TRASH_CONFIRMATION}")
    rows = conn.execute("SELECT * FROM backups WHERE backup_status='trashed' ORDER BY id").fetchall()
    removed = removed_bytes = 0
    failures = []
    for row in rows:
        try:
            result = purge_backup(conn, row["uuid"], confirmation=PURGE_CONFIRMATION, user_id=user_id)
            removed += 1
            removed_bytes += result["bytes"]
        except (OSError, ValueError) as exc:
            failures.append({"uuid": row["uuid"], "error": str(exc)[:160]})
    _audit(conn, user_id, "lifecycle.trash_emptied", "trash",
           {"removed": removed, "bytes": removed_bytes, "failures": len(failures)})
    return {"removed": removed, "bytes": removed_bytes, "failures": failures}


def restore(conn, backup_uuid: str, *, user_id=None) -> None:
    row = conn.execute("SELECT * FROM backups WHERE uuid=? AND backup_status='trashed'", (backup_uuid,)).fetchone()
    if not row:
        raise ValueError("Backup nao esta disponivel na lixeira.")
    config = load_config(conn)
    source = resolve_inside(config.trash_directory, row["trash_relative_path"])
    target = resolve_inside(config.backup_directory, row["relative_path"])
    if not source.is_file() or target.exists():
        raise ValueError("Arquivo da lixeira ausente ou destino ocupado.")
    if sha256_file(source) != row["sha256"]:
        raise ValueError("Hash da lixeira diverge; restauracao recusada.")
    atomic_move(source, target)
    conn.execute("""UPDATE backups SET backup_status='available',deleted_at=NULL,deleted_by_user_id=NULL,
                 trash_relative_path=NULL,trash_expires_at=NULL WHERE id=?""", (row["id"],))
    _audit(conn, user_id, "lifecycle.backup_restored", backup_uuid, {"sha256": row["sha256"]})


def stats(conn) -> dict:
    config = load_config(conn)
    disk = filesystem_usage(config)
    result = {"disk_total": disk.total, "disk_used": disk.used, "disk_free": disk.free}
    for status in ("available", "trashed", "deleted", "quarantined"):
        row = conn.execute("SELECT COUNT(*),COALESCE(SUM(file_size),0) FROM backups WHERE backup_status=?", (status,)).fetchone()
        result[f"{status}_count"], result[f"{status}_bytes"] = row[0], row[1]
    result["rejected_count"] = conn.execute("SELECT COUNT(*) FROM ftp_received_files WHERE status='rejected' AND lifecycle_trashed_at IS NULL").fetchone()[0]
    return result


def health_check(conn, *, include_hash: bool = True, user_id=None) -> dict:
    config = load_config(conn)
    problems = []
    checked = 0
    for row in conn.execute("SELECT * FROM backups WHERE backup_status IN ('available','trashed')").fetchall():
        base = config.trash_directory if row["backup_status"] == "trashed" else config.backup_directory
        relative = row["trash_relative_path"] if row["backup_status"] == "trashed" else row["relative_path"]
        try:
            path = resolve_inside(base, relative)
            if not path.is_file():
                problems.append({"uuid": row["uuid"], "problem": "arquivo ausente"}); continue
            checked += 1
            if path.stat().st_size != row["file_size"]:
                problems.append({"uuid": row["uuid"], "problem": "tamanho divergente"})
            elif include_hash and sha256_file(path) != row["sha256"]:
                problems.append({"uuid": row["uuid"], "problem": "hash divergente"})
        except (OSError, ValueError) as exc:
            problems.append({"uuid": row["uuid"], "problem": str(exc)})
    report = {"checked": checked, "problems": problems, "ok": not problems, "include_hash": include_hash}
    run_id, run_uuid = _new_run(conn, "health_check", user_id, {"candidate_count": 0, "candidate_bytes": 0, **report})
    conn.execute("UPDATE lifecycle_runs SET status='completed',report_json=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",
                 (json.dumps(report, separators=(",", ":")), run_id))
    _audit(conn, user_id, "lifecycle.health_checked", run_uuid, {"checked": checked, "problems": len(problems)})
    report["run_uuid"] = run_uuid
    return report
