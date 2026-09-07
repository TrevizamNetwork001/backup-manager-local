from __future__ import annotations

from collections.abc import Callable, Iterable


def dispatch_backup_created(conn, backups: Iterable[tuple[int, str, str, int, str]],
                            *, audit_created: Callable[[str, dict], None]) -> int:
    """Fan out a stored FTP backup to configured delivery queues exactly once."""
    from .cloud_sync import enqueue_new_backup
    from .telegram_backup import enqueue_new_backup as enqueue_new_telegram_backup

    dispatched = 0
    for backup_id, backup_uuid, filename, file_size, digest in backups:
        enqueue_new_backup(conn, backup_id)
        enqueue_new_telegram_backup(conn, backup_id)
        audit_created(filename, {"size": file_size, "sha256": digest[:12], "backup_uuid": backup_uuid})
        dispatched += 1
    return dispatched
