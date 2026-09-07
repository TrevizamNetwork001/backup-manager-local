from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .storage import atomic_move, backup_relative_path, load_config, resolve_inside


@dataclass(frozen=True)
class StoredBackup:
    id: int
    uuid: str
    filename: str
    relative_path: str


def store_backup(conn, *, equipment_id: int, source: Path, original_filename: str,
                 file_size: int, digest: str, received_at: datetime, notes: str) -> StoredBackup:
    """Create one database record and atomically move its file into managed storage."""
    config = load_config(conn)
    backup_uuid = str(uuid.uuid4())
    relative = backup_relative_path(equipment_id, backup_uuid, received_at)
    target = resolve_inside(config.backup_directory, relative)
    conn.execute("""INSERT INTO backups(uuid,equipment_id,original_filename,stored_filename,relative_path,file_size,sha256,
                  source_method,backup_reason,backup_status,received_at,created_by_user_id,notes)
                  VALUES(?,?,?,?,?,?,?,'ftp','event','validating',?,NULL,?)""",
                 (backup_uuid, equipment_id, original_filename, target.name, relative, file_size, digest,
                  received_at.strftime("%Y-%m-%d %H:%M:%S"), notes))
    backup_id = conn.execute("SELECT id FROM backups WHERE uuid=?", (backup_uuid,)).fetchone()[0]
    atomic_move(source, target)
    conn.execute("UPDATE backups SET backup_status='available' WHERE id=?", (backup_id,))
    return StoredBackup(backup_id, backup_uuid, target.name, relative)
