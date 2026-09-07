"""Motor de atualização local assinada.

O formato .bmu é um tar.gz com manifest.json, checksums.txt, signature e os
arquivos declarados sob payload/, migrations/ e units/. Nenhum comando livre é
aceito. Todas as funções recebem caminhos confinados e são utilizáveis em testes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import uuid as uuidlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .version import __version__

PRODUCT = "backup-manager-local"
DEFAULT_UPDATE_ROOT = Path("/var/lib/backup-manager-local/updates")
DEFAULT_BACKUP_ROOT = Path("/var/backups/backup-manager-local/updates")
DEFAULT_PUBLIC_KEY = Path("/etc/backup-manager-local/update-public-key.pem")
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 10_000
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024 * 1024
ALLOWED_TOP = {"manifest.json", "checksums.txt", "signature"}
ALLOWED_PREFIXES = ("payload/", "migrations/", "units/")
MANIFEST_FIELDS = {
    "product", "version", "minimum_version", "maximum_version", "channel",
    "build_id", "created_at", "payload_sha256", "required_disk_bytes",
    "requires_restart", "services_to_restart", "migrations", "files",
    "pre_checks", "post_checks", "release_notes",
}
ALLOWED_SERVICES = {
    "backup-manager-local.service", "backup-manager-worker.service",
    "backup-manager-lifecycle.service", "backup-manager-lifecycle.timer",
    "backup-manager-observability.service", "backup-manager-observability.timer",
    "backup-manager-ftp-importer.service", "backup-manager-ftp-importer.timer",
    "backup-manager-cloud-sync.service", "backup-manager-cloud-sync.timer",
    "backup-manager-update.service",
}
ALLOWED_CHECKS = {"integrity-check", "storage-check", "jobs-stats", "ftp-config-check", "notification-check", "cloud-stats", "version", "login", "units", "assets"}
FORBIDDEN_PARTS = {"data", "storage", "ftp-incoming", "puredb", "nginx", "letsencrypt", "ufw", ".git", "secret.key"}
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")


class UpdateError(RuntimeError):
    def __init__(self, code: str, message: str = "Pacote de atualização recusado.") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidationResult:
    package_sha256: str
    manifest: dict[str, Any]
    plan: dict[str, Any]


def _canonical(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _version(value: str) -> tuple[int, int, int, tuple[str, ...]]:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise UpdateError("INVALID_VERSION")
    base, _, suffix = value.partition("-")
    return (*map(int, base.split(".")), tuple(suffix.split(".")) if suffix else ("~",))


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not name.startswith("/") and ".." not in path.parts and "" not in path.parts and "\\" not in name


def _managed_destination(value: str) -> bool:
    if not _safe_name(value):
        return False
    parts = PurePosixPath(value).parts
    return parts[0] in {"backup_manager", "migrations", "deploy", "scripts", "static", "templates"} and not any(p.lower() in FORBIDDEN_PARTS for p in parts)


def _regular_package(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise UpdateError("PACKAGE_UNREADABLE") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise UpdateError("PACKAGE_NOT_REGULAR")
    if info.st_size <= 0 or info.st_size > MAX_PACKAGE_BYTES:
        raise UpdateError("PACKAGE_SIZE")
    return info


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo, limit: int = 4 * 1024 * 1024) -> bytes:
    if member.size > limit:
        raise UpdateError("METADATA_TOO_LARGE")
    handle = archive.extractfile(member)
    if handle is None:
        raise UpdateError("PACKAGE_MEMBER_INVALID")
    return handle.read(limit + 1)


def _inspect(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members = archive.getmembers()
    if len(members) > MAX_MEMBERS:
        raise UpdateError("PACKAGE_LIMIT")
    total = 0
    result: dict[str, tarfile.TarInfo] = {}
    for item in members:
        name = item.name.rstrip("/")
        if not _safe_name(name):
            raise UpdateError("PATH_TRAVERSAL")
        if item.issym() or item.islnk() or item.isdev() or item.isfifo():
            raise UpdateError("UNSAFE_MEMBER")
        if not (name in ALLOWED_TOP or name.startswith(ALLOWED_PREFIXES)):
            raise UpdateError("UNDECLARED_FILE")
        if item.size > MAX_MEMBER_BYTES:
            raise UpdateError("PACKAGE_LIMIT")
        total += item.size
        if total > MAX_EXPANDED_BYTES:
            raise UpdateError("PACKAGE_LIMIT")
        if item.isfile():
            if name in result:
                raise UpdateError("DUPLICATE_MEMBER")
            result[name] = item
    return result


def validate_package(package: str | Path, public_key: str | Path = DEFAULT_PUBLIC_KEY,
                     installed_version: str = __version__, disk_path: str | Path | None = None) -> ValidationResult:
    path = Path(package)
    _regular_package(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        archive = tarfile.open(path, "r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise UpdateError("PACKAGE_FORMAT") from exc
    with archive:
        members = _inspect(archive)
        for required in ALLOWED_TOP:
            if required not in members:
                raise UpdateError("MISSING_METADATA")
        try:
            manifest = json.loads(_read_member(archive, members["manifest.json"]).decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise UpdateError("MANIFEST_INVALID") from exc
        if not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS:
            raise UpdateError("MANIFEST_SCHEMA")
        key_path = Path(public_key)
        try:
            key_info = key_path.lstat()
            if stat.S_ISLNK(key_info.st_mode) or not stat.S_ISREG(key_info.st_mode):
                raise UpdateError("PUBLIC_KEY_INVALID")
            key = serialization.load_pem_public_key(key_path.read_bytes())
            if not isinstance(key, Ed25519PublicKey):
                raise UpdateError("PUBLIC_KEY_INVALID")
            checksums_raw = _read_member(archive, members["checksums.txt"])
            key.verify(_read_member(archive, members["signature"], 1024), _canonical(manifest) + b"\n" + checksums_raw)
        except UpdateError:
            raise
        except FileNotFoundError as exc:
            raise UpdateError("PUBLIC_KEY_MISSING") from exc
        except (InvalidSignature, ValueError, OSError) as exc:
            raise UpdateError("SIGNATURE_INVALID") from exc
        if manifest["product"] != PRODUCT:
            raise UpdateError("PRODUCT_MISMATCH")
        current, target = _version(installed_version), _version(manifest["version"])
        if target <= current:
            raise UpdateError("DOWNGRADE_BLOCKED")
        if manifest["minimum_version"] and current < _version(manifest["minimum_version"]):
            raise UpdateError("VERSION_INCOMPATIBLE")
        if manifest["maximum_version"] and current > _version(manifest["maximum_version"]):
            raise UpdateError("VERSION_INCOMPATIBLE")
        files = manifest["files"]
        if not isinstance(files, list) or not all(isinstance(x, dict) and set(x) == {"path", "sha256", "size", "action"} for x in files):
            raise UpdateError("MANIFEST_SCHEMA")
        declared = set()
        aggregate = hashlib.sha256()
        for entry in sorted(files, key=lambda x: x["path"]):
            rel = entry["path"]
            if entry["action"] not in {"add", "replace", "remove"} or not _managed_destination(rel):
                raise UpdateError("FILE_FORBIDDEN")
            member_name = f"payload/{rel}"
            if entry["action"] == "remove":
                if entry["sha256"] or entry["size"] != 0 or member_name in members:
                    raise UpdateError("MANIFEST_SCHEMA")
                continue
            declared.add(member_name)
            member = members.get(member_name)
            if not member or member.size != entry["size"]:
                raise UpdateError("FILE_MISMATCH")
            content = _read_member(archive, member, MAX_MEMBER_BYTES)
            actual = hashlib.sha256(content).hexdigest()
            if actual != entry["sha256"]:
                raise UpdateError("HASH_MISMATCH")
            aggregate.update(rel.encode() + b"\0" + actual.encode() + b"\n")
        payload_members = {n for n in members if n.startswith("payload/")}
        if payload_members != declared or aggregate.hexdigest() != manifest["payload_sha256"]:
            raise UpdateError("UNDECLARED_FILE" if payload_members != declared else "HASH_MISMATCH")
        migrations = manifest["migrations"]
        if not isinstance(migrations, list) or any(not isinstance(x, str) or not re.fullmatch(r"[0-9]{3}_[A-Za-z0-9_.-]+\.sql", x) for x in migrations):
            raise UpdateError("MANIFEST_SCHEMA")
        if {n.removeprefix("migrations/") for n in members if n.startswith("migrations/")} != set(migrations):
            raise UpdateError("UNDECLARED_FILE")
        checksum_lines = checksums_raw.decode("utf-8").splitlines()
        expected_checksums: dict[str, str] = {}
        for line in checksum_lines:
            match = re.fullmatch(r"([0-9a-f]{64})  ([^\s]+)", line)
            if not match or not _safe_name(match.group(2)) or match.group(2) in expected_checksums:
                raise UpdateError("CHECKSUMS_INVALID")
            expected_checksums[match.group(2)] = match.group(1)
        content_names = {n for n in members if n.startswith(ALLOWED_PREFIXES)}
        if set(expected_checksums) != content_names:
            raise UpdateError("UNDECLARED_FILE")
        for name, expected in expected_checksums.items():
            if hashlib.sha256(_read_member(archive, members[name], MAX_MEMBER_BYTES)).hexdigest() != expected:
                raise UpdateError("HASH_MISMATCH")
        services = manifest["services_to_restart"]
        if not isinstance(services, list) or not set(services) <= ALLOWED_SERVICES:
            raise UpdateError("SERVICE_FORBIDDEN")
        for field in ("pre_checks", "post_checks"):
            if not isinstance(manifest[field], list) or not set(manifest[field]) <= ALLOWED_CHECKS:
                raise UpdateError("CHECK_FORBIDDEN")
        required = manifest["required_disk_bytes"]
        if not isinstance(required, int) or required < 0:
            raise UpdateError("MANIFEST_SCHEMA")
        free = shutil.disk_usage(Path(disk_path) if disk_path else path.parent).free
        if free < required:
            raise UpdateError("DISK_INSUFFICIENT")
        plan = {
            "from_version": installed_version, "to_version": manifest["version"],
            "added": [x["path"] for x in files if x["action"] == "add"],
            "replaced": [x["path"] for x in files if x["action"] == "replace"],
            "removed": [x["path"] for x in files if x["action"] == "remove"],
            "migrations": migrations, "services": services, "required_disk_bytes": required,
            "pre_checks": manifest["pre_checks"], "post_checks": manifest["post_checks"],
            "release_notes": str(manifest["release_notes"])[:4000], "backup_required": True,
        }
        return ValidationResult(digest, manifest, plan)


def safe_extract(package: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(package, "r:gz") as archive:
        members = _inspect(archive)
        for name, item in members.items():
            target = destination / PurePosixPath(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(item)
            if source is None:
                raise UpdateError("PACKAGE_MEMBER_INVALID")
            with target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            target.chmod(0o600)


def create_operation(conn: sqlite3.Connection, package_name: str, user_id: int | None = None) -> str:
    operation = str(uuidlib.uuid4())
    conn.execute("INSERT INTO update_operations(uuid,package_name,from_version,status,requested_by_user_id) VALUES(?,?,?,?,?)",
                 (operation, Path(package_name).name[:255], __version__, "uploaded", user_id))
    return operation


def list_operations(conn: sqlite3.Connection, *, operation: str = ""):
    if operation:
        return conn.execute(
            """SELECT uuid,from_version,to_version,status,requested_at,finished_at,error_code,
                      manifest_json_sanitized FROM update_operations WHERE uuid=?""",
            (operation,),
        ).fetchall()
    return conn.execute(
        """SELECT uuid,from_version,to_version,status,requested_at,finished_at,error_code
           FROM update_operations ORDER BY id DESC LIMIT 100"""
    ).fetchall()


def ready_operation(conn: sqlite3.Connection, operation: str):
    return conn.execute(
        "SELECT to_version,status FROM update_operations WHERE uuid=?", (operation,)
    ).fetchone()


def record_validation(conn: sqlite3.Connection, operation: str, result: ValidationResult) -> None:
    sanitized = dict(result.manifest)
    sanitized["release_notes"] = str(sanitized.get("release_notes", ""))[:4000]
    conn.execute("""UPDATE update_operations SET package_sha256=?,to_version=?,status='ready',validated_at=CURRENT_TIMESTAMP,
                 manifest_json_sanitized=?,updated_at=CURRENT_TIMESTAMP WHERE uuid=? AND status IN ('uploaded','validating')""",
                 (result.package_sha256, result.manifest["version"], json.dumps(sanitized, ensure_ascii=False), operation))


def create_backup(db_path: Path, app_root: Path, backup_root: Path, operation: str,
                  managed_files: list[str]) -> Path:
    target = backup_root / operation
    target.mkdir(parents=True, exist_ok=False)
    (target / "application").mkdir()
    manifest: dict[str, str] = {}
    for rel in managed_files:
        if not _managed_destination(rel):
            raise UpdateError("FILE_FORBIDDEN")
        source = app_root / rel
        if source.is_file() and not source.is_symlink():
            out = target / "application" / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, out)
            manifest[rel] = hashlib.sha256(source.read_bytes()).hexdigest()
    backup_db = target / "database.sqlite3"
    source_conn = sqlite3.connect(db_path)
    dest_conn = sqlite3.connect(backup_db)
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close(); source_conn.close()
    (target / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return target


def restore_backup(db_path: Path, app_root: Path, backup: Path) -> None:
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    for rel, digest in manifest.items():
        source = backup / "application" / rel
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise UpdateError("BACKUP_INVALID")
        target = app_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.rollback")
        shutil.copy2(source, temporary); os.replace(temporary, target)
    temporary_db = db_path.with_name(f".{db_path.name}.rollback")
    shutil.copy2(backup / "database.sqlite3", temporary_db); os.replace(temporary_db, db_path)


def apply_staged(staging: Path, app_root: Path, manifest: dict[str, Any]) -> None:
    for entry in manifest["files"]:
        target = (app_root / entry["path"]).resolve()
        if not str(target).startswith(str(app_root.resolve()) + os.sep):
            raise UpdateError("PATH_TRAVERSAL")
        if entry["action"] == "remove":
            if target.exists() and target.is_file() and not target.is_symlink():
                target.unlink()
            continue
        source = staging / "payload" / entry["path"]
        if not source.is_file() or source.is_symlink():
            raise UpdateError("STAGING_INVALID")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.update")
        shutil.copy2(source, temporary); temporary.chmod(0o644); os.replace(temporary, target)


def run_migrations(db_path: Path, staging: Path, migrations: list[str]) -> None:
    from .db import _apply_notification_queue_repair

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        for name in migrations:
            if Path(name).stem in applied:
                continue
            if Path(name).stem == "020_fix_notification_queue_destinations":
                _apply_notification_queue_repair(conn)
            else:
                conn.executescript((staging / "migrations" / name).read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations(version) VALUES(?)", (Path(name).stem,))
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchone():
            raise UpdateError("DATABASE_CHECK_FAILED")
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def root_required() -> None:
    if os.geteuid() != 0:
        raise UpdateError("ROOT_REQUIRED", "Esta ação exige root.")


def systemctl(action: str, services: list[str]) -> None:
    if action not in {"stop", "start", "restart"} or not set(services) <= ALLOWED_SERVICES:
        raise UpdateError("SERVICE_FORBIDDEN")
    for service in services:
        subprocess.run(["systemctl", action, service], check=True, timeout=120)
