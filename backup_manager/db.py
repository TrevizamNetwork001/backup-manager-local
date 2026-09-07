from __future__ import annotations

import os
import fcntl
import errno
import hashlib
import json
import sqlite3
import stat
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .security import hash_password

BASE_DIR = Path(os.environ.get("BACKUP_MANAGER_HOME", "/opt/backup-manager-local"))
DATA_DIR = Path(os.environ.get("BACKUP_MANAGER_DATA", str(BASE_DIR / "data")))
DB_PATH = Path(os.environ.get("BACKUP_MANAGER_DB", str(DATA_DIR / "backup_manager.sqlite3")))

# A descoberta do unittest importa todos os módulos antes de executar os
# fixtures. Alguns módulos antigos só configuram BACKUP_MANAGER_DB durante a
# própria importação; sem esta barreira, o primeiro import de db.py pode fixar
# o caminho padrão de produção durante uma suíte local.
if any("unittest" in argument for argument in sys.argv):
    production_db = (BASE_DIR / "data" / "backup_manager.sqlite3").resolve()
    if DB_PATH.expanduser().resolve() == production_db:
        test_data_dir = Path(tempfile.mkdtemp(prefix="backup-manager-unittest-"))
        DATA_DIR = test_data_dir
        DB_PATH = test_data_dir / "test.sqlite3"
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
MIGRATIONS_MANIFEST = MIGRATIONS_DIR / "manifest.json"
DEFAULT_MIGRATION_LOCK = Path("/run/lock/backup-manager-migrate.lock")
FALLBACK_MIGRATION_LOCK = DATA_DIR / "locks" / "backup-manager-migrate.lock"

TEMP_ADMIN_PASSWORD = "ChangeMe!2026"
REQUIRED_SCHEMA = {"notification_queue": {"destination_id", "thread_id"}}


class SchemaCompatibilityError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class MigrationError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SchemaVersionStatus:
    current: int
    expected: int
    code: str = ""

    @property
    def ready(self) -> bool:
        return not self.code


@dataclass(frozen=True)
class MigrationEntry:
    version: int
    name: str
    sha256: str


@dataclass(frozen=True)
class MigrationResult:
    from_version: int
    to_version: int
    applied: tuple[int, ...]
    backup_path: str = ""


class ManagedConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15, factory=ManagedConnection)
    try:
        DB_PATH.chmod(0o640)
    except OSError:
        conn.close()
        raise
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def schema_problems(conn: sqlite3.Connection) -> list[str]:
    problems: list[str] = []
    for table, required_columns in REQUIRED_SCHEMA.items():
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            problems.append(table)
            continue
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        problems.extend(f"{table}.{column}" for column in sorted(required_columns - present))
    return problems


def _apply_notification_queue_repair(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(notification_queue)")}
    if "destination_id" not in columns:
        conn.execute("ALTER TABLE notification_queue ADD COLUMN destination_id INTEGER REFERENCES telegram_destinations(id)")
    if "thread_id" not in columns:
        conn.execute("ALTER TABLE notification_queue ADD COLUMN thread_id INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notification_queue_destination ON notification_queue(destination_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notification_queue_thread ON notification_queue(thread_id)")


def _apply_telegram_backup_delivery(conn: sqlite3.Connection) -> None:
    policy_columns = {row[1] for row in conn.execute("PRAGMA table_info(telegram_backup_policies)")}
    item_columns = {row[1] for row in conn.execute("PRAGMA table_info(telegram_backup_items)")}
    if "all_artifacts" not in policy_columns:
        conn.execute("ALTER TABLE telegram_backup_policies ADD COLUMN all_artifacts INTEGER NOT NULL DEFAULT 1 CHECK(all_artifacts IN (0,1))")
    if "file_types" not in policy_columns:
        conn.execute("ALTER TABLE telegram_backup_policies ADD COLUMN file_types TEXT NOT NULL DEFAULT ''")
    if "idempotency_key" not in item_columns:
        conn.execute("ALTER TABLE telegram_backup_items ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''")
    if "last_attempt_at" not in item_columns:
        conn.execute("ALTER TABLE telegram_backup_items ADD COLUMN last_attempt_at TEXT")
    conn.execute("DROP INDEX IF EXISTS idx_telegram_backup_unique")
    conn.execute("""UPDATE telegram_backup_items
        SET idempotency_key=printf('%d:%d:%d:%s',backup_id,destination_id,COALESCE(thread_id,0),local_sha256)
        WHERE idempotency_key=''""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_backup_idempotency ON telegram_backup_items(idempotency_key)")
    conn.executemany("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING", (
        ("telegram_backup_max_file_bytes", "52428800"), ("telegram_backup_all_artifacts", "1"),
        ("telegram_backup_default_destination", ""), ("telegram_backup_max_attempts", "5"),
        ("telegram_backup_retry_base_seconds", "60")))


def _apply_migration(conn: sqlite3.Connection, path: Path) -> None:
    version = path.stem
    if version == "026_telegram_backup_delivery":
        conn.execute("BEGIN IMMEDIATE")
        try:
            _apply_telegram_backup_delivery(conn)
            conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise sqlite3.IntegrityError("foreign_key_check failed")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return
    if version == "020_fix_notification_queue_destinations":
        conn.execute("BEGIN IMMEDIATE")
        try:
            _apply_notification_queue_repair(conn)
            conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise sqlite3.IntegrityError("foreign_key_check failed")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return
    if version in {"022_mikrotik_test_without_token", "023_mikrotik_test_lifecycle",
                   "024_ftp_account_lifecycle", "029_standalone_ftp_accounts"}:
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            quoted_version = version.replace("'", "''")
            sql = path.read_text(encoding="utf-8")
            conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nINSERT INTO schema_migrations(version) VALUES ('{quoted_version}');\nCOMMIT;")
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise sqlite3.IntegrityError("foreign_key_check failed")
        return
    quoted_version = version.replace("'", "''")
    sql = path.read_text(encoding="utf-8")
    conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nINSERT INTO schema_migrations(version) VALUES ('{quoted_version}');\nCOMMIT;")


def _manifest(path: Path = MIGRATIONS_MANIFEST) -> tuple[int, tuple[MigrationEntry, ...]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        release = int(payload["release_version"])
        entries = tuple(MigrationEntry(int(row["version"]), str(row["name"]), str(row["sha256"]))
                        for row in payload["migrations"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise MigrationError("MIGRATION_MANIFEST_INVALID") from exc
    versions = [entry.version for entry in entries]
    names = [entry.name for entry in entries]
    if (not entries or release != versions[-1] or versions != list(range(1, release + 1))
            or len(names) != len(set(names))):
        raise MigrationError("MIGRATION_MANIFEST_INVALID")
    for entry in entries:
        if (entry.name != f"{entry.version:03d}_" + entry.name.split("_", 1)[-1]
                or not entry.name.endswith(".sql")
                or len(entry.sha256) != 64
                or any(char not in "0123456789abcdef" for char in entry.sha256)):
            raise MigrationError("MIGRATION_MANIFEST_INVALID")
    return release, entries


def release_schema_version(*, manifest_path: Path | None = None) -> int:
    release, _ = _manifest(manifest_path or MIGRATIONS_MANIFEST)
    return release


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _applied_versions(path: Path) -> set[str]:
    if not path.is_file() or path.is_symlink():
        raise SchemaCompatibilityError("SCHEMA_INVALID")
    try:
        conn = _readonly_connection(path)
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone():
                raise SchemaCompatibilityError("SCHEMA_INVALID")
            return {str(row[0]) for row in conn.execute("SELECT version FROM schema_migrations")}
        finally:
            conn.close()
    except SchemaCompatibilityError:
        raise
    except sqlite3.Error as exc:
        raise SchemaCompatibilityError("SCHEMA_INVALID") from exc


def check_schema_version(*, db_path: Path | None = None,
                         manifest_path: Path | None = None) -> SchemaVersionStatus:
    db_path = db_path or DB_PATH
    manifest_path = manifest_path or MIGRATIONS_MANIFEST
    try:
        release, entries = _manifest(manifest_path)
        applied = _applied_versions(db_path)
    except MigrationError as exc:
        return SchemaVersionStatus(0, 0, "SCHEMA_INVALID")
    except SchemaCompatibilityError as exc:
        return SchemaVersionStatus(0, release if 'release' in locals() else 0, exc.code)
    known = {Path(entry.name).stem: entry.version for entry in entries}
    unknown = {version for version in applied if version not in known}
    if unknown:
        numeric = [int(value.split("_", 1)[0]) for value in unknown if value.split("_", 1)[0].isdigit()]
        code = "SCHEMA_AHEAD" if any(value > release for value in numeric) else "SCHEMA_INVALID"
        return SchemaVersionStatus(max(numeric, default=0), release, code)
    current = max((known[value] for value in applied), default=0)
    expected_applied = {Path(entry.name).stem for entry in entries[:current]}
    if applied != expected_applied:
        return SchemaVersionStatus(current, release, "SCHEMA_INVALID")
    if current < release:
        return SchemaVersionStatus(current, release, "SCHEMA_OUTDATED")
    if current > release:
        return SchemaVersionStatus(current, release, "SCHEMA_AHEAD")
    return SchemaVersionStatus(current, release)


def require_schema_current(*, db_path: Path | None = None,
                           manifest_path: Path | None = None) -> SchemaVersionStatus:
    db_path = db_path or DB_PATH
    manifest_path = manifest_path or MIGRATIONS_MANIFEST
    status = check_schema_version(db_path=db_path, manifest_path=manifest_path)
    if not status.ready:
        raise SchemaCompatibilityError(status.code)
    return status


@contextmanager
def _migration_lock(timeout: float, *, lock_path: Path | None = None, data_dir: Path = DATA_DIR):
    candidates = (lock_path,) if lock_path is not None else (
        DEFAULT_MIGRATION_LOCK, FALLBACK_MIGRATION_LOCK)
    descriptor = None
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
            if candidate != DEFAULT_MIGRATION_LOCK:
                candidate.parent.chmod(0o750)
            if lock_path is not None:
                data_root = data_dir.resolve()
                parent = candidate.parent.resolve()
                if parent != data_root and data_root not in parent.parents:
                    raise MigrationError("MIGRATION_LOCK_INVALID")
            nofollow = getattr(os, "O_NOFOLLOW", None)
            if nofollow is None:
                raise MigrationError("MIGRATION_LOCK_INVALID")
            descriptor = os.open(candidate, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | nofollow, 0o600)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor); descriptor = None
                raise MigrationError("MIGRATION_LOCK_INVALID")
            os.fchmod(descriptor, 0o600)
            break
        except PermissionError:
            if descriptor is not None:
                os.close(descriptor); descriptor = None
            continue
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            if (lock_path is None and candidate == DEFAULT_MIGRATION_LOCK
                    and exc.errno in {errno.EACCES, errno.ENOENT, errno.EROFS, errno.EPERM}):
                descriptor = None
                continue
            raise MigrationError("MIGRATION_LOCK_INVALID") from exc
    if descriptor is None:
        raise MigrationError("MIGRATION_LOCK_INVALID")
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise MigrationError("MIGRATION_LOCKED")
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _validate_migration_files(entries: tuple[MigrationEntry, ...], target: int, migrations_dir: Path) -> None:
    selected = entries[:target]
    authorized = {entry.name for entry in selected}
    for path in migrations_dir.glob("*.sql"):
        prefix = path.name.split("_", 1)[0]
        if prefix.isdigit() and int(prefix) <= target and path.name not in authorized:
            raise MigrationError("MIGRATION_FILE_UNAUTHORIZED")
    for entry in selected:
        path = migrations_dir / entry.name
        if not path.is_file() or path.is_symlink():
            raise MigrationError("MIGRATION_FILE_MISSING")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry.sha256:
            raise MigrationError("MIGRATION_HASH_MISMATCH")


def _sqlite_backup(source: Path, data_dir: Path) -> Path:
    backup_dir = data_dir / "migration-backups"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
    if backup_dir.is_symlink() or data_dir.resolve() not in backup_dir.resolve().parents:
        raise MigrationError("MIGRATION_BACKUP_FAILED")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    target = backup_dir / f"{source.name}.pre-migration-{stamp}-{uuid.uuid4().hex}.bak"
    try:
        incoming = _readonly_connection(source)
        outgoing = sqlite3.connect(target)
        try:
            incoming.backup(outgoing)
        finally:
            outgoing.close(); incoming.close()
        checked = _readonly_connection(target)
        try:
            if checked.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise MigrationError("MIGRATION_BACKUP_FAILED")
        finally:
            checked.close()
        target.chmod(0o640)
        return target
    except MigrationError:
        target.unlink(missing_ok=True)
        raise
    except (OSError, sqlite3.Error) as exc:
        target.unlink(missing_ok=True)
        raise MigrationError("MIGRATION_BACKUP_FAILED") from exc


def _seed_defaults_complete(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        conn = _readonly_connection(path)
        try:
            checks = (
                "SELECT 1 FROM roles WHERE slug='admin'",
                "SELECT 1 FROM users WHERE username='admin'",
                "SELECT 1 FROM settings WHERE key='storage_root'",
            )
            return all(conn.execute(sql).fetchone() for sql in checks)
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _verify_database(path: Path, expected_versions: set[str]) -> None:
    try:
        conn = _readonly_connection(path)
        try:
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise MigrationError("MIGRATION_FOREIGN_KEY_FAILED")
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise MigrationError("MIGRATION_INTEGRITY_FAILED")
            actual = {str(row[0]) for row in conn.execute("SELECT version FROM schema_migrations")}
            if actual != expected_versions:
                raise MigrationError("MIGRATION_TARGET_NOT_REACHED")
        finally:
            conn.close()
    except MigrationError:
        raise
    except sqlite3.Error as exc:
        raise MigrationError("SCHEMA_INVALID") from exc


def migrate(to_version: int, *, lock_timeout: float = 10.0, _lock_path: Path | None = None,
            _manifest_path: Path | None = None, _migrations_dir: Path | None = None,
            _db_path: Path | None = None, _data_dir: Path | None = None,
            _seed: bool = True) -> MigrationResult:
    _manifest_path = _manifest_path or MIGRATIONS_MANIFEST
    _migrations_dir = _migrations_dir or MIGRATIONS_DIR
    _db_path = _db_path or DB_PATH
    _data_dir = _data_dir or DATA_DIR
    with _migration_lock(lock_timeout, lock_path=_lock_path, data_dir=_data_dir):
        release, entries = _manifest(_manifest_path)
        if not isinstance(to_version, int) or isinstance(to_version, bool) or not 1 <= to_version <= release:
            raise MigrationError("MIGRATION_TARGET_INVALID")
        _validate_migration_files(entries, to_version, _migrations_dir)
        applied: set[str] = set()
        if _db_path.exists() and _db_path.stat().st_size:
            try:
                applied = _applied_versions(_db_path)
            except SchemaCompatibilityError as exc:
                raise MigrationError(exc.code) from exc
        known = {Path(entry.name).stem: entry.version for entry in entries}
        if any(value not in known for value in applied):
            raise MigrationError("SCHEMA_AHEAD")
        current = max((known[value] for value in applied), default=0)
        if current > to_version:
            raise MigrationError("SCHEMA_AHEAD")
        expected = {Path(entry.name).stem for entry in entries[:current]}
        if applied != expected:
            raise MigrationError("SCHEMA_INVALID")
        pending = tuple(entry for entry in entries[:to_version] if Path(entry.name).stem not in applied)
        target_versions = {Path(entry.name).stem for entry in entries[:to_version]}
        seed_needed = _seed and not _seed_defaults_complete(_db_path)
        if not pending and not seed_needed:
            _verify_database(_db_path, target_versions)
            return MigrationResult(current, to_version, ())
        backup_path = ""
        if pending and _db_path.exists() and _db_path.stat().st_size:
            backup_path = str(_sqlite_backup(_db_path, _data_dir))
        _db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        conn = sqlite3.connect(_db_path, timeout=15, factory=ManagedConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            conn.commit()
            completed: list[int] = []
            for entry in pending:
                _apply_migration(conn, _migrations_dir / entry.name)
                completed.append(entry.version)
            if _seed:
                seed_defaults(conn)
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise MigrationError("MIGRATION_FOREIGN_KEY_FAILED")
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise MigrationError("MIGRATION_INTEGRITY_FAILED")
            final = {str(row[0]) for row in conn.execute("SELECT version FROM schema_migrations")}
            if final != target_versions:
                raise MigrationError("MIGRATION_TARGET_NOT_REACHED")
            conn.commit()
            return MigrationResult(current, to_version, tuple(completed), backup_path)
        finally:
            conn.close()


def seed_defaults(conn: sqlite3.Connection) -> None:
    roles = [
        ("admin", "Admin", 1, 1),
        ("read_download", "Leitura/Download", 1, 0),
    ]
    conn.executemany(
        """
        INSERT INTO roles(slug, name, can_download, can_admin)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(slug) DO NOTHING
        """,
        roles,
    )

    admin_role_id = conn.execute("SELECT id FROM roles WHERE slug = 'admin'").fetchone()["id"]
    password_hash = hash_password(TEMP_ADMIN_PASSWORD)
    conn.execute(
        """
        INSERT INTO users(username, full_name, password_hash, role_id, must_change_password)
        VALUES ('admin', 'Administrador', ?, ?, 1)
        ON CONFLICT(username) DO NOTHING
        """,
        (password_hash, admin_role_id),
    )

    for group_name in ("Core", "Acesso", "Distribuicao", "Clientes"):
        conn.execute(
            "INSERT INTO equipment_groups(name) VALUES (?) ON CONFLICT(name) DO NOTHING",
            (group_name,),
        )

    for vendor_name in (
        "Cisco", "Juniper", "Huawei", "MikroTik", "Ubiquiti",
        "ZTE", "Intelbras", "VSOL", "Parks", "C-DATA", "FiberHome", "Datacom",
    ):
        conn.execute(
            "INSERT INTO vendors(name) VALUES (?) ON CONFLICT(name) DO NOTHING",
            (vendor_name,),
        )

    storage_defaults = {
        "storage_root": os.environ.get("BACKUP_MANAGER_STORAGE_ROOT", "/var/lib/backup-manager-local"),
        "backup_directory": str(Path(os.environ.get("BACKUP_MANAGER_STORAGE_ROOT", "/var/lib/backup-manager-local")) / "backups"),
        "trash_directory": str(Path(os.environ.get("BACKUP_MANAGER_STORAGE_ROOT", "/var/lib/backup-manager-local")) / "trash"),
        "quarantine_directory": str(Path(os.environ.get("BACKUP_MANAGER_STORAGE_ROOT", "/var/lib/backup-manager-local")) / "quarantine"),
        "temporary_directory": str(Path(os.environ.get("BACKUP_MANAGER_STORAGE_ROOT", "/var/lib/backup-manager-local")) / "temporary"),
        "default_retention_days": "365",
        "trash_retention_days": "30",
        "maximum_upload_size": str(50 * 1024 * 1024),
    }
    for key, value in storage_defaults.items():
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
            (key, value),
        )
    root = conn.execute("SELECT value FROM settings WHERE key = 'storage_root'").fetchone()["value"]
    legacy_backup_dir = conn.execute("SELECT value FROM settings WHERE key = 'backup_directory'").fetchone()["value"]
    if legacy_backup_dir == "/var/backups/backup-manager":
        root_path = Path(root)
        for key, value in {
            "backup_directory": str(root_path / "backups"),
            "trash_directory": str(root_path / "trash"),
            "quarantine_directory": str(root_path / "quarantine"),
            "temporary_directory": str(root_path / "temporary"),
        }.items():
            conn.execute("UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?", (value, key))


def integrity_check() -> str:
    with connect() as conn:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
