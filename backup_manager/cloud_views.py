from __future__ import annotations


def cloud_page_context(conn) -> dict[str, object]:
    targets = conn.execute(
        """SELECT t.*,r.remote_name AS rclone_remote,r.base_path AS rclone_path,r.status AS rclone_status,r.last_test_at AS rclone_last_test
           FROM cloud_targets t LEFT JOIN rclone_connections r ON r.target_id=t.id
           WHERE t.deleted_at IS NULL ORDER BY t.name"""
    ).fetchall()
    policies = conn.execute(
        """
        SELECT p.*, t.name AS target_name, e.hostname
        FROM cloud_sync_policies p
        JOIN cloud_targets t ON t.id = p.target_id
        LEFT JOIN equipment e ON e.id = p.equipment_id
        WHERE p.deleted_at IS NULL
        ORDER BY p.id DESC
        """
    ).fetchall()
    items = conn.execute(
        """
        SELECT i.*, t.name AS target_name, t.is_active AS target_is_active,
               t.deleted_at AS target_deleted_at, b.uuid AS backup_uuid, e.hostname
        FROM cloud_sync_items i
        JOIN cloud_targets t ON t.id = i.target_id
        JOIN backups b ON b.id = i.backup_id
        JOIN equipment e ON e.id = i.equipment_id
        ORDER BY i.queued_at DESC
        LIMIT 200
        """
    ).fetchall()
    equipment = conn.execute("SELECT id,hostname FROM equipment ORDER BY hostname").fetchall()
    return {
        "targets": targets,
        "policies": policies,
        "items": items,
        "equipment": equipment,
    }
