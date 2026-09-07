from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ALLOWED_EXTENSIONS = {
    ".txt",
    ".cfg",
    ".dat",
    ".db",
    ".conf",
    ".config",
    ".backup",
    ".bin",
    ".bak",
    ".rsc",
    ".exp",
    ".xml",
    ".json",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
}
DEFAULT_STORAGE_ROOT = "/var/lib/backup-manager-local"
DEFAULT_MAX_UPLOAD_SIZE = 50 * 1024 * 1024
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class StorageConfig:
    storage_root: Path
    backup_directory: Path
    trash_directory: Path
    quarantine_directory: Path
    temporary_directory: Path
    default_retention_days: int
    trash_retention_days: int
    maximum_upload_size: int


def _setting(conn, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def load_config(conn) -> StorageConfig:
    env_root = os.environ.get("BACKUP_MANAGER_STORAGE_ROOT")
    root = Path(env_root or _setting(conn, "storage_root", DEFAULT_STORAGE_ROOT))
    if env_root:
        backup_directory = root / "backups"
        trash_directory = root / "trash"
        quarantine_directory = root / "quarantine"
        temporary_directory = root / "temporary"
    else:
        backup_directory = Path(_setting(conn, "backup_directory", str(root / "backups")))
        trash_directory = Path(_setting(conn, "trash_directory", str(root / "trash")))
        quarantine_directory = Path(_setting(conn, "quarantine_directory", str(root / "quarantine")))
        temporary_directory = Path(_setting(conn, "temporary_directory", str(root / "temporary")))
    return StorageConfig(
        storage_root=root,
        backup_directory=backup_directory,
        trash_directory=trash_directory,
        quarantine_directory=quarantine_directory,
        temporary_directory=temporary_directory,
        default_retention_days=int(_setting(conn, "default_retention_days", "365") or 365),
        trash_retention_days=int(_setting(conn, "trash_retention_days", "30") or 30),
        maximum_upload_size=int(_setting(conn, "maximum_upload_size", str(DEFAULT_MAX_UPLOAD_SIZE)) or DEFAULT_MAX_UPLOAD_SIZE),
    )


def ensure_directories(config: StorageConfig) -> None:
    for path in (config.storage_root, config.backup_directory, config.trash_directory, config.quarantine_directory, config.temporary_directory):
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(0o750)
        except OSError:
            pass


def safe_filename(filename: str) -> str:
    name = filename.replace("\\", "/").split("/")[-1].strip().strip(".")
    name = SAFE_FILENAME_RE.sub("_", name)
    name = name[:180].strip("._-")
    if not name:
        raise ValueError("Nome de arquivo invalido.")
    if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError("Extensao nao permitida.")
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError("Nome de arquivo suspeito.")
    return name


def format_size(size: int | None) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def backup_relative_path(equipment_id: int, backup_uuid: str, received_at: datetime | None = None) -> str:
    current = received_at or datetime.now(timezone.utc)
    return str(Path("equipment") / f"{equipment_id:08d}" / f"{current.year:04d}" / f"{current.month:02d}" / f"{backup_uuid}.backup")


def trash_relative_path(backup_uuid: str) -> str:
    current = datetime.now(timezone.utc)
    return str(Path(f"{current.year:04d}") / f"{current.month:02d}" / f"{backup_uuid}.backup")


def resolve_inside(base: Path, relative_path: str | None) -> Path:
    if not relative_path:
        raise ValueError("Caminho ausente.")
    if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
        raise ValueError("Caminho invalido.")
    base_resolved = base.resolve()
    target = (base_resolved / relative_path).resolve()
    if base_resolved != target and base_resolved not in target.parents:
        raise ValueError("Caminho fora do storage permitido.")
    return target


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_upload_to_temporary(fileobj, temporary_directory: Path, maximum_size: int, upload_uuid: str) -> tuple[Path, int, str]:
    ensure_parent = temporary_directory / "uploads"
    ensure_parent.mkdir(parents=True, exist_ok=True)
    temp_path = ensure_parent / f"{upload_uuid}.part"
    digest = hashlib.sha256()
    total = 0
    with temp_path.open("xb") as output:
        while True:
            chunk = fileobj.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_size:
                output.close()
                temp_path.unlink(missing_ok=True)
                raise ValueError("Arquivo acima do limite permitido.")
            digest.update(chunk)
            output.write(chunk)
    if total <= 0:
        temp_path.unlink(missing_ok=True)
        raise ValueError("Arquivo vazio.")
    return temp_path, total, digest.hexdigest()


def write_bytes_to_temporary(content: bytes, temporary_directory: Path, maximum_size: int, backup_uuid: str) -> tuple[Path, int, str]:
    if not content:
        raise ValueError("Arquivo vazio.")
    if len(content) > maximum_size:
        raise ValueError("Arquivo acima do limite permitido.")
    ensure_parent = temporary_directory / "generated"
    ensure_parent.mkdir(parents=True, exist_ok=True)
    temp_path = ensure_parent / f"{backup_uuid}.part"
    digest = hashlib.sha256(content).hexdigest()
    with temp_path.open("xb") as output:
        output.write(content)
    return temp_path, len(content), digest


def atomic_move(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)


def trash_expiration(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def filesystem_usage(config: StorageConfig) -> shutil._ntuple_diskusage:
    ensure_directories(config)
    return shutil.disk_usage(config.storage_root)


def check_storage(conn, include_hash: bool = False) -> dict[str, object]:
    problems: list[str] = []
    config = load_config(conn)
    for label, directory in (
        ("storage_root", config.storage_root),
        ("backup_directory", config.backup_directory),
        ("trash_directory", config.trash_directory),
        ("quarantine_directory", config.quarantine_directory),
        ("temporary_directory", config.temporary_directory),
    ):
        if not directory.is_dir():
            problems.append(f"{label}: diretorio ausente")
        elif directory.stat().st_mode & 0o002:
            problems.append(f"{label}: permissao insegura para outros")
    registered = {}
    rows = conn.execute("SELECT uuid, relative_path, trash_relative_path, file_size, sha256, backup_status FROM backups").fetchall()
    for row in rows:
        if row["backup_status"] in {"deleted", "failed", "receiving", "validating"}:
            continue
        relative = row["trash_relative_path"] if row["backup_status"] == "trashed" else row["relative_path"]
        base = config.trash_directory if row["backup_status"] == "trashed" else config.backup_directory
        try:
            path = resolve_inside(base, relative)
        except ValueError:
            problems.append(f"{row['uuid']}: caminho invalido")
            continue
        registered[str(path.resolve())] = row
        if not path.is_file():
            problems.append(f"{row['uuid']}: arquivo registrado ausente")
            continue
        if path.stat().st_size != row["file_size"]:
            problems.append(f"{row['uuid']}: tamanho diferente")
        if include_hash and sha256_file(path) != row["sha256"]:
            problems.append(f"{row['uuid']}: hash diferente")
    for base in (config.backup_directory, config.trash_directory):
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.relative_to(base).parts[:1] == ("rejected",):
                continue
            if path.is_file() and str(path.resolve()) not in registered:
                problems.append(f"arquivo fisico sem registro: {path.relative_to(base)}")
    return {"config": config, "problems": problems}


def storage_stats(conn) -> dict[str, object]:
    config = load_config(conn)
    available = conn.execute("SELECT COUNT(*), COALESCE(SUM(file_size), 0) FROM backups WHERE backup_status = 'available'").fetchone()
    trash = conn.execute("SELECT COUNT(*), COALESCE(SUM(file_size), 0) FROM backups WHERE backup_status = 'trashed'").fetchone()
    missing = 0
    registered = set()
    for row in conn.execute("SELECT relative_path, trash_relative_path, backup_status FROM backups WHERE backup_status IN ('available','quarantined','trashed')").fetchall():
        relative = row["trash_relative_path"] if row["backup_status"] == "trashed" else row["relative_path"]
        base = config.trash_directory if row["backup_status"] == "trashed" else config.backup_directory
        try:
            path = resolve_inside(base, relative)
        except ValueError:
            missing += 1
            continue
        registered.add(str(path.resolve()))
        if not path.is_file():
            missing += 1
    orphan = 0
    for base in (config.backup_directory, config.trash_directory):
        if base.is_dir():
            for path in base.rglob("*"):
                if path.relative_to(base).parts[:1] == ("rejected",):
                    continue
                if path.is_file() and str(path.resolve()) not in registered:
                    orphan += 1
    disk = filesystem_usage(config)
    return {
        "available_count": available[0],
        "available_size": available[1],
        "trash_count": trash[0],
        "trash_size": trash[1],
        "missing": missing,
        "orphan": orphan,
        "disk_total": disk.total,
        "disk_free": disk.free,
    }
