from __future__ import annotations


def handle_backup_import_logic(
    *,
    conn,
    user,
    equipment_id: int,
    field,
    fields: dict[str, str],
    config,
    environ: dict,
    safe_filename,
    now_utc,
    write_upload_to_temporary,
    backup_relative_path,
    resolve_inside,
    atomic_move,
    enqueue_new_backup,
    enqueue_new_telegram_backup,
    audit,
    backup_details_json,
    detail_page,
    success_response,
):
    try:
        original = safe_filename(field.filename)
    except ValueError as exc:
        return detail_page(environ, equipment_id, str(exc))
    backup_uuid = str(__import__("uuid").uuid4())
    received_at = now_utc()
    try:
        temp_path, size, digest = write_upload_to_temporary(
            field.file, config.temporary_directory, config.maximum_upload_size, backup_uuid
        )
    except ValueError as exc:
        return detail_page(environ, equipment_id, str(exc))
    relative_path = backup_relative_path(equipment_id, backup_uuid, received_at)
    target = resolve_inside(config.backup_directory, relative_path)
    if target.exists():
        temp_path.unlink(missing_ok=True)
        return detail_page(environ, equipment_id, "Tentativa de sobrescrita bloqueada.")
    conn.execute("BEGIN")
    conn.execute(
        """
        INSERT INTO backups(uuid, equipment_id, original_filename, stored_filename, relative_path,
                            file_size, sha256, source_method, backup_reason, backup_status,
                            received_at, created_by_user_id, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', 'manual', 'receiving', ?, ?, ?)
        """,
        (
            backup_uuid,
            equipment_id,
            original,
            target.name,
            relative_path,
            size,
            digest,
            received_at.strftime("%Y-%m-%d %H:%M:%S"),
            user["id"],
            fields.get("notes", "").strip()[:500],
        ),
    )
    conn.execute("UPDATE backups SET backup_status = 'validating' WHERE uuid = ?", (backup_uuid,))
    atomic_move(temp_path, target)
    conn.execute("UPDATE backups SET backup_status = 'available' WHERE uuid = ?", (backup_uuid,))
    row = conn.execute("SELECT * FROM backups WHERE uuid = ?", (backup_uuid,)).fetchone()
    enqueue_new_backup(conn, row["id"])
    enqueue_new_telegram_backup(conn, row["id"])
    audit(conn, user["id"], "backup.manual_uploaded", "backup", backup_uuid, backup_details_json(row), environ.get("REMOTE_ADDR", ""))
    conn.commit()
    return success_response(equipment_id)
