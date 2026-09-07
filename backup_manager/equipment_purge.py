from __future__ import annotations

from pathlib import Path

from .equipment_dependencies import equipment_dependencies
from .storage import load_config, resolve_inside


def _tables(conn) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def purge_equipment(conn, equipment_id: int) -> dict[str, object]:
    equipment = conn.execute("SELECT * FROM equipment WHERE id=?", (equipment_id,)).fetchone()
    if not equipment:
        raise ValueError("Equipamento não encontrado.")
    report = equipment_dependencies(conn, equipment_id)
    config = load_config(conn)
    file_paths: list[Path] = []
    for row in conn.execute("SELECT relative_path,trash_relative_path FROM backups WHERE equipment_id=?", (equipment_id,)):
        if row["relative_path"]:
            file_paths.append(resolve_inside(config.backup_directory, row["relative_path"]))
        if row["trash_relative_path"]:
            file_paths.append(resolve_inside(config.trash_directory, row["trash_relative_path"]))

    tables = _tables(conn)
    backup_ids = [row[0] for row in conn.execute("SELECT id FROM backups WHERE equipment_id=?", (equipment_id,))]
    integration_ids = [row[0] for row in conn.execute("SELECT id FROM mikrotik_ftp_integrations WHERE equipment_id=?", (equipment_id,))]
    operation_ids = [row[0] for row in conn.execute("SELECT id FROM backup_operations WHERE equipment_id=?", (equipment_id,))] if "backup_operations" in tables else []

    conn.execute("BEGIN IMMEDIATE")
    try:
        if backup_ids:
            marks = ",".join("?" for _ in backup_ids)
            for table in tables:
                columns = _columns(conn, table)
                for column in ("backup_id", "created_backup_id"):
                    if column in columns:
                        conn.execute(f'DELETE FROM "{table}" WHERE "{column}" IN ({marks})', backup_ids)
        if integration_ids:
            marks = ",".join("?" for _ in integration_ids)
            for table in tables:
                if "integration_id" in _columns(conn, table):
                    conn.execute(f'DELETE FROM "{table}" WHERE integration_id IN ({marks})', integration_ids)
        if operation_ids and "backup_operation_artifacts" in tables:
            marks = ",".join("?" for _ in operation_ids)
            conn.execute(f"DELETE FROM backup_operation_artifacts WHERE operation_id IN ({marks})", operation_ids)
        # Children that also carry equipment_id must be removed before their parents.
        priority = (
            "telegram_backup_items", "telegram_test_items", "cloud_sync_items",
            "ftp_received_files", "mikrotik_ftp_uploads", "backup_job_runs",
            "backup_operation_artifacts", "backup_operations", "mikrotik_ftp_tests",
            "mikrotik_ftp_integrations", "backup_jobs", "ftp_accounts",
            "telegram_topic_mappings", "telegram_backup_policies", "cloud_sync_policies",
            "retention_policies", "device_credentials", "backups",
        )
        handled: set[str] = set()
        for table in priority:
            if table in tables and "equipment_id" in _columns(conn, table):
                conn.execute(f'DELETE FROM "{table}" WHERE equipment_id=?', (equipment_id,))
                handled.add(table)
        for table in tables:
            if table != "equipment" and table not in handled and "equipment_id" in _columns(conn, table):
                conn.execute(f'DELETE FROM "{table}" WHERE equipment_id=?', (equipment_id,))
        conn.execute("DELETE FROM equipment WHERE id=?", (equipment_id,))
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise ValueError("A exclusão deixaria referências inválidas; nenhuma alteração foi aplicada.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    file_failures: list[str] = []
    for path in file_paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            file_failures.append(path.name)
    return {"hostname": equipment["hostname"], "counts": report["counts"],
            "files": len(file_paths), "file_failures": file_failures}
