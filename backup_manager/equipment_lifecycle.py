from __future__ import annotations

import json

from .lifecycle import effective_policy
from .storage import atomic_move, load_config, resolve_inside, trash_expiration, trash_relative_path


def archive_equipment(conn, equipment_id: int, *, file_action: str, user_id: int,
                      ip_address: str = "") -> dict:
    if file_action not in {"preserve", "trash"}:
        raise ValueError("Ação para os arquivos é inválida.")
    equipment = conn.execute("SELECT * FROM equipment WHERE id=?", (equipment_id,)).fetchone()
    if not equipment:
        raise ValueError("Equipamento não encontrado.")
    active_accounts = conn.execute("""SELECT COUNT(*) FROM ftp_accounts WHERE equipment_id=?
        AND is_active=1 AND deleted_at IS NULL""", (equipment_id,)).fetchone()[0]
    active_integrations = conn.execute("""SELECT COUNT(*) FROM mikrotik_ftp_integrations
        WHERE equipment_id=? AND is_active=1""", (equipment_id,)).fetchone()[0]
    if active_integrations:
        raise ValueError("Desative a integração FTP Push antes de arquivar este equipamento.")
    if active_accounts:
        raise ValueError("Desative ou exclua as contas FTP ativas antes de arquivar este equipamento.")

    moved = moved_bytes = 0
    if file_action == "trash":
        config = load_config(conn)
        policy = effective_policy(conn, equipment_id)
        rows = conn.execute("SELECT * FROM backups WHERE equipment_id=? AND backup_status='available' ORDER BY id",
                            (equipment_id,)).fetchall()
        for row in rows:
            source = resolve_inside(config.backup_directory, row["relative_path"])
            if not source.is_file():
                raise ValueError(f"Arquivo do backup {row['uuid']} não foi encontrado; arquivamento cancelado.")
            target_rel = trash_relative_path(row["uuid"])
            target = resolve_inside(config.trash_directory, target_rel)
            atomic_move(source, target)
            conn.execute("""UPDATE backups SET backup_status='trashed',deleted_at=CURRENT_TIMESTAMP,
                deleted_by_user_id=?,trash_relative_path=?,trash_expires_at=? WHERE id=?""",
                (user_id, target_rel, trash_expiration(policy.trash_retention_days), row["id"]))
            moved += 1
            moved_bytes += row["file_size"]

    conn.execute("UPDATE equipment SET is_active=0 WHERE id=?", (equipment_id,))
    conn.execute("UPDATE backup_jobs SET status='disabled',updated_at=CURRENT_TIMESTAMP WHERE equipment_id=? AND status!='deleted'",
                 (equipment_id,))
    conn.execute("UPDATE device_credentials SET is_active=0,updated_at=CURRENT_TIMESTAMP WHERE equipment_id=? AND deleted_at IS NULL",
                 (equipment_id,))
    conn.execute("UPDATE cloud_sync_policies SET enabled=0,updated_at=CURRENT_TIMESTAMP WHERE equipment_id=? AND deleted_at IS NULL",
                 (equipment_id,))
    conn.execute("UPDATE telegram_backup_policies SET enabled=0,updated_at=CURRENT_TIMESTAMP WHERE equipment_id=? AND deleted_at IS NULL",
                 (equipment_id,))
    details = {"hostname": equipment["hostname"], "file_action": file_action,
               "moved_backups": moved, "moved_bytes": moved_bytes}
    conn.execute("""INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address)
                  VALUES(?,'equipment.archived','equipment',?,?,?)""",
                 (user_id, str(equipment_id), json.dumps(details, separators=(",", ":")), ip_address))
    return details
