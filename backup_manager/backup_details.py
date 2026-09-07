from __future__ import annotations


def backup_detail_context(conn, backup_uuid: str) -> dict[str, object]:
    row = conn.execute(
        """
        SELECT backups.*, equipment.hostname
        FROM backups JOIN equipment ON equipment.id = backups.equipment_id
        WHERE backups.uuid = ?
        """,
        (backup_uuid,),
    ).fetchone()
    cloud_items = conn.execute(
        """SELECT i.status,i.attempt,i.finished_at,i.error_code,i.remote_object_id,i.remote_folder_id,i.provider,t.name target_name
            FROM cloud_sync_items i JOIN cloud_targets t ON t.id=i.target_id WHERE i.backup_id=(SELECT id FROM backups WHERE uuid=?)
            ORDER BY i.created_at DESC""",
        (backup_uuid,),
    ).fetchall() if row else []
    telegram_items = conn.execute(
        """SELECT i.status,i.attempt,i.sent_at,i.error_code,d.name destination_name
            FROM telegram_backup_items i JOIN telegram_destinations d ON d.id=i.destination_id
            WHERE i.backup_id=(SELECT id FROM backups WHERE uuid=?) ORDER BY i.created_at DESC""",
        (backup_uuid,),
    ).fetchall() if row else []
    return {"row": row, "cloud_items": cloud_items, "telegram_items": telegram_items}

