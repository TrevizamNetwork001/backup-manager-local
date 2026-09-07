from __future__ import annotations
import json
import uuid

PENDING_TYPES = {"test", "summary", "alert", "backup_file", "all"}

def list_destinations(conn):
    return conn.execute(
        """SELECT id,uuid,name,chat_id,destination_type,default_thread_id,is_active
           FROM telegram_destinations WHERE deleted_at IS NULL ORDER BY id"""
    ).fetchall()

def destination_by_uuid(conn, destination_uuid):
    return conn.execute("SELECT * FROM telegram_destinations WHERE uuid=? AND deleted_at IS NULL", (destination_uuid.strip(),)).fetchone()

def validate_destination_name(conn, name, *, exclude_id=None):
    value = name.strip()
    if not value: raise ValueError("Nome do destino é obrigatório.")
    duplicate = conn.execute("SELECT 1 FROM telegram_destinations WHERE is_active=1 AND deleted_at IS NULL AND lower(trim(name))=lower(?) AND (? IS NULL OR id<>?)", (value, exclude_id, exclude_id)).fetchone()
    if duplicate: raise ValueError("Já existe um destino ativo com esse nome. Renomeie ou desative um deles.")
    return value[:120]

def pending_counts(conn, destination_id):
    counts = {
        "test": conn.execute("SELECT COUNT(*) FROM telegram_test_items WHERE destination_id=? AND status='queued'", (destination_id,)).fetchone()[0],
        "summary": conn.execute("SELECT COUNT(*) FROM telegram_summary_runs WHERE destination_id=? AND status IN ('queued','retry_wait')", (destination_id,)).fetchone()[0],
        "alert": conn.execute("SELECT COUNT(*) FROM notification_queue WHERE destination_id=? AND status IN ('pending','processing')", (destination_id,)).fetchone()[0],
        "backup_file": conn.execute("SELECT COUNT(*) FROM telegram_backup_items WHERE destination_id=? AND status IN ('queued','retry_wait')", (destination_id,)).fetchone()[0],
    }
    counts["all"] = sum(counts.values()); return counts

def disable_destination(conn, destination_uuid, *, user_id=None, reason="destination_invalid"):
    row = destination_by_uuid(conn, destination_uuid)
    if not row: raise ValueError("Destino Telegram não encontrado.")
    counts = pending_counts(conn, row["id"])
    conn.execute("UPDATE telegram_destinations SET is_active=0,disabled_at=CURRENT_TIMESTAMP,disabled_reason=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (reason[:120], row["id"]))
    conn.execute("INSERT INTO telegram_destination_actions(uuid,destination_id,user_id,action,item_type,item_count,reason) VALUES(?,?,?,'disable','all',?,?)", (str(uuid.uuid4()), row["id"], user_id, counts["all"], reason[:200]))
    conn.execute("INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address) VALUES(?,?,?,?,?,'')", (user_id, "telegram.destination_disabled", "telegram_destination", row["uuid"], json.dumps({"pending": counts, "reason": reason})))
    return counts

def cancel_pending(conn, destination_uuid, item_type, *, user_id=None, confirm_all=False):
    if item_type not in PENDING_TYPES: raise ValueError("Tipo pendente inválido.")
    if item_type == "all" and not confirm_all: raise ValueError("O cancelamento de todos os tipos exige confirmação explícita.")
    row = destination_by_uuid(conn, destination_uuid)
    if not row: raise ValueError("Destino Telegram não encontrado.")
    if row["is_active"]: raise ValueError("Desative o destino antes de cancelar itens pendentes.")
    total = 0
    statements = {
        "test": ("UPDATE telegram_test_items SET status='failed',error_code='DESTINATION_DISABLED',finished_at=CURRENT_TIMESTAMP WHERE destination_id=? AND status='queued'",),
        "summary": ("UPDATE telegram_summary_runs SET status='skipped',error_code='DESTINATION_DISABLED',updated_at=CURRENT_TIMESTAMP WHERE destination_id=? AND status IN ('queued','retry_wait')",),
        "alert": ("UPDATE notification_queue SET status='suppressed',updated_at=CURRENT_TIMESTAMP WHERE destination_id=? AND status IN ('pending','processing')",),
        "backup_file": ("UPDATE telegram_backup_items SET status='cancelled',error_code='DESTINATION_DISABLED',error_message='destination_disabled',finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE destination_id=? AND status IN ('queued','retry_wait')",),
    }
    for kind, (sql,) in statements.items():
        if item_type in {kind, "all"}: total += conn.execute(sql, (row["id"],)).rowcount
    conn.execute("INSERT INTO telegram_destination_actions(uuid,destination_id,user_id,action,item_type,item_count,reason) VALUES(?,?,?,'cancel_pending',?,?,'destination_disabled')", (str(uuid.uuid4()), row["id"], user_id, item_type, total))
    conn.execute("INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address) VALUES(?,?,?,?,?,'')", (user_id, "telegram.destination_pending_cancelled", "telegram_destination", row["uuid"], json.dumps({"type": item_type, "count": total})))
    return total
