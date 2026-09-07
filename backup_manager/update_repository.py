"""Consulta e download explícito de atualizações, com TLS e mitigação de SSRF."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import sqlite3
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .updater import DEFAULT_PUBLIC_KEY, DEFAULT_UPDATE_ROOT, MAX_PACKAGE_BYTES, PRODUCT, UpdateError, create_operation, record_validation, validate_package
from .version import __version__

MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_SIGNATURE_BYTES = 1024
MAX_REDIRECTS = 3
CONNECT_TIMEOUT = 10
INDEX_FIELDS = {"product", "generated_at", "channels"}
CHANNEL_FIELDS = {"latest", "releases"}
RELEASE_FIELDS = {"version", "channel", "build_id", "published_at", "minimum_version", "maximum_version", "package_url", "package_sha256", "package_size", "signature_url", "release_notes", "mandatory", "deprecated", "security_update"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?$")


class RepositoryError(RuntimeError):
    def __init__(self, code: str, message: str = "Operação remota recusada.") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, order=True)
class SemVer:
    major: int
    minor: int
    patch: int
    stable: int
    prerelease: tuple[tuple[int, Any], ...]

    @classmethod
    def parse(cls, value: str) -> "SemVer":
        match = VERSION.fullmatch(value) if isinstance(value, str) else None
        if not match:
            raise RepositoryError("INVALID_VERSION")
        identifiers = ()
        stable = 1
        if match.group(4):
            stable = 0
            parts = re.split(r"[.-]", match.group(4))
            if any(not part for part in parts):
                raise RepositoryError("INVALID_VERSION")
            identifiers = tuple((0, int(p)) if p.isdigit() else (1, p.lower()) for p in parts)
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), stable, identifiers)


def canonical(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else default


def _audit(conn: sqlite3.Connection, action: str, entity_id: str = "", details: str = "", user_id: int | None = None) -> None:
    conn.execute("INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address) VALUES(?,?,'update_repository',?,?, '')", (user_id, action, entity_id, details[:1000]))


def sanitize_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    return urllib.parse.urlunsplit(("https", host + port, parsed.path, "", ""))


def validate_url(value: str, *, allowed_hosts: set[str] | None = None, lab_mode: bool = False,
                 resolver: Callable[..., Any] = socket.getaddrinfo) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise RepositoryError("URL_INVALID") from exc
    if parsed.scheme.lower() != "https":
        raise RepositoryError("HTTPS_REQUIRED")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise RepositoryError("URL_INVALID")
    if port not in (None, 443):
        raise RepositoryError("PORT_BLOCKED")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise RepositoryError("HOST_BLOCKED")
    if allowed_hosts and host not in allowed_hosts:
        raise RepositoryError("HOST_NOT_ALLOWED")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not lab_mode:
        raise RepositoryError("IP_LITERAL_BLOCKED")
    try:
        addresses = {ipaddress.ip_address(item[4][0]) for item in resolver(host, 443, type=socket.SOCK_STREAM)}
    except (OSError, ValueError) as exc:
        raise RepositoryError("DNS_FAILED") from exc
    if not addresses:
        raise RepositoryError("DNS_FAILED")
    if not lab_mode and any(not ip.is_global for ip in addresses):
        raise RepositoryError("PRIVATE_ADDRESS_BLOCKED")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class SecureHTTPClient:
    def __init__(self, *, resolver=socket.getaddrinfo) -> None:
        self.resolver = resolver
        self.opener = urllib.request.build_opener(_NoRedirect(), urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def open(self, url: str, *, allowed_hosts: set[str], lab_mode: bool, headers: dict[str, str] | None = None):
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            validate_url(current, allowed_hosts=allowed_hosts, lab_mode=lab_mode, resolver=self.resolver)
            request = urllib.request.Request(current, headers={"User-Agent": "backup-manager-local-update/1", **(headers or {})})
            try:
                return self.opener.open(request, timeout=CONNECT_TIMEOUT)
            except urllib.error.HTTPError as exc:
                if exc.code not in {301, 302, 303, 307, 308}:
                    raise RepositoryError("HTTP_ERROR") from exc
                location = exc.headers.get("Location", "")
                current = urllib.parse.urljoin(current, location)
        raise RepositoryError("TOO_MANY_REDIRECTS")

    def read(self, url: str, *, limit: int, content_types: set[str], allowed_hosts: set[str], lab_mode: bool) -> bytes:
        with self.open(url, allowed_hosts=allowed_hosts, lab_mode=lab_mode) as response:
            content_type = response.headers.get_content_type().lower()
            if content_type not in content_types:
                raise RepositoryError("CONTENT_TYPE_INVALID")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > limit:
                raise RepositoryError("RESPONSE_TOO_LARGE")
            data = response.read(limit + 1)
            if len(data) > limit:
                raise RepositoryError("RESPONSE_TOO_LARGE")
            return data


def load_public_key(path: str | Path) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(Path(path).read_bytes())
    except (OSError, ValueError) as exc:
        raise RepositoryError("PUBLIC_KEY_INVALID") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise RepositoryError("PUBLIC_KEY_INVALID")
    return key


def _signature(raw: bytes) -> bytes:
    if len(raw) == 64:
        return raw
    stripped = raw.strip()
    try:
        decoded = base64.b64decode(stripped, validate=True)
    except ValueError as exc:
        raise RepositoryError("SIGNATURE_INVALID") from exc
    if len(decoded) != 64:
        raise RepositoryError("SIGNATURE_INVALID")
    return decoded


def validate_index(raw: bytes, signature: bytes, public_key: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise RepositoryError("INDEX_JSON_INVALID") from exc
    if not isinstance(data, dict) or set(data) != INDEX_FIELDS:
        raise RepositoryError("INDEX_SCHEMA")
    try:
        load_public_key(public_key).verify(_signature(signature), canonical(data))
    except InvalidSignature as exc:
        raise RepositoryError("SIGNATURE_INVALID") from exc
    if data["product"] != PRODUCT or not isinstance(data["generated_at"], str):
        raise RepositoryError("PRODUCT_MISMATCH" if data.get("product") != PRODUCT else "INDEX_SCHEMA")
    channels = data["channels"]
    if not isinstance(channels, dict) or set(channels) != {"stable", "rc"}:
        raise RepositoryError("INDEX_SCHEMA")
    for name, channel in channels.items():
        if not isinstance(channel, dict) or set(channel) != CHANNEL_FIELDS or not isinstance(channel["latest"], str) or not isinstance(channel["releases"], list):
            raise RepositoryError("INDEX_SCHEMA")
        for release in channel["releases"]:
            if not isinstance(release, dict) or set(release) != RELEASE_FIELDS:
                raise RepositoryError("INDEX_SCHEMA")
            if release["channel"] != name or not all(isinstance(release[x], str) for x in RELEASE_FIELDS - {"package_size", "mandatory", "deprecated", "security_update"}):
                raise RepositoryError("INDEX_SCHEMA")
            for url_field in ("package_url", "signature_url"):
                parsed_url = urllib.parse.urlsplit(release[url_field])
                if (parsed_url.scheme.lower() != "https" or not parsed_url.hostname or
                        parsed_url.username is not None or parsed_url.password is not None or
                        parsed_url.fragment):
                    raise RepositoryError("URL_INVALID")
            if not isinstance(release["package_size"], int) or not 0 < release["package_size"] <= MAX_PACKAGE_BYTES or not all(isinstance(release[x], bool) for x in ("mandatory", "deprecated", "security_update")) or not HEX64.fullmatch(release["package_sha256"]):
                raise RepositoryError("INDEX_SCHEMA")
            SemVer.parse(release["version"])
    return data


def select_release(index: dict[str, Any], channel: str, installed_version: str = __version__) -> dict[str, Any] | None:
    if channel not in {"stable", "rc"}:
        raise RepositoryError("CHANNEL_INVALID")
    current = SemVer.parse(installed_version)
    releases = list(index["channels"]["stable"]["releases"])
    if channel == "rc":
        releases += index["channels"]["rc"]["releases"]
    eligible = []
    for release in releases:
        target = SemVer.parse(release["version"])
        if release["deprecated"] or target <= current:
            continue
        if release["minimum_version"] and current < SemVer.parse(release["minimum_version"]):
            continue
        if release["maximum_version"] and current > SemVer.parse(release["maximum_version"]):
            continue
        eligible.append(release)
    return max(eligible, key=lambda item: SemVer.parse(item["version"]), default=None)


def repository_config(conn: sqlite3.Connection) -> tuple[str, str, set[str], bool]:
    if _setting(conn, "update_repository_enabled", "0") != "1":
        raise RepositoryError("REPOSITORY_DISABLED")
    url = _setting(conn, "update_repository_url")
    channel = _setting(conn, "update_channel", "stable")
    allowed = {x.strip().rstrip(".").lower() for x in _setting(conn, "update_repository_allowed_hosts").split(",") if x.strip()}
    lab = _setting(conn, "update_repository_lab_mode", "0") == "1"
    if channel not in {"stable", "rc"}:
        raise RepositoryError("CHANNEL_INVALID")
    if not url:
        raise RepositoryError("URL_INVALID")
    # Aceita tanto a raiz do repositório quanto a forma explícita
    # ``.../updates.json`` usada em configurações legadas.
    validate_url(url, allowed_hosts=allowed, lab_mode=lab)
    return url, channel, allowed, lab


def _index_urls(base: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(base)
    if parsed.path.rstrip("/").endswith("/updates.json") or parsed.path.endswith("updates.json"):
        index_url = base
    else:
        index_url = urllib.parse.urljoin(base.rstrip("/") + "/", "updates.json")
    return index_url, index_url + ".sig"


def check_updates(conn: sqlite3.Connection, *, client: SecureHTTPClient | None = None, public_key: str | Path = DEFAULT_PUBLIC_KEY, installed_version: str = __version__, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any] | None:
    _audit(conn, "update.check_started")
    conn.commit()
    try:
        base, channel, allowed, lab = repository_config(conn)
        index_url, signature_url = _index_urls(base)
        http = client or SecureHTTPClient()
        last: RepositoryError | None = None
        for attempt in range(3):
            try:
                raw = http.read(index_url, limit=MAX_INDEX_BYTES, content_types={"application/json"}, allowed_hosts=allowed, lab_mode=lab)
                sig = http.read(signature_url, limit=MAX_SIGNATURE_BYTES, content_types={"application/octet-stream", "text/plain"}, allowed_hosts=allowed, lab_mode=lab)
                break
            except RepositoryError as exc:
                last = exc
                if attempt < 2: sleep(0.25 * (2 ** attempt))
        else:
            raise last or RepositoryError("CHECK_FAILED")
        index = validate_index(raw, sig, public_key)
        release = select_release(index, channel, installed_version)
        version = release["version"] if release else ""
        build = release["build_id"] if release else ""
        conn.execute("""UPDATE settings SET value=CASE key WHEN 'update_last_check_at' THEN CURRENT_TIMESTAMP WHEN 'update_last_check_status' THEN 'success' WHEN 'update_last_check_error' THEN '' WHEN 'update_available_version' THEN ? WHEN 'update_available_build_id' THEN ? WHEN 'update_available_release_json' THEN ? END,updated_at=CURRENT_TIMESTAMP WHERE key IN ('update_last_check_at','update_last_check_status','update_last_check_error','update_available_version','update_available_build_id','update_available_release_json')""", (version, build, json.dumps(release or {}, ensure_ascii=False, separators=(",", ":"))))
        _audit(conn, "update.check_success", details=f"available={int(bool(release))}")
        if release: _audit(conn, "update.available", version, f"version={version} security={int(release['security_update'])}")
        return release
    except RepositoryError as exc:
        conn.execute("UPDATE settings SET value=CASE key WHEN 'update_last_check_at' THEN CURRENT_TIMESTAMP WHEN 'update_last_check_status' THEN 'failed' WHEN 'update_last_check_error' THEN ? END,updated_at=CURRENT_TIMESTAMP WHERE key IN ('update_last_check_at','update_last_check_status','update_last_check_error')", (exc.code,))
        _audit(conn, "update.check_failed", details=f"code={exc.code}")
        raise


def queue_download(conn: sqlite3.Connection, *, user_id: int | None = None) -> str:
    try: release = json.loads(_setting(conn, "update_available_release_json", "{}"))
    except ValueError as exc: raise RepositoryError("NO_RELEASE_SELECTED") from exc
    if not release or set(release) != RELEASE_FIELDS:
        raise RepositoryError("NO_RELEASE_SELECTED")
    existing = conn.execute("SELECT uuid FROM update_downloads WHERE version=? AND build_id=? AND status IN ('queued','downloading','validating','ready')", (release["version"], release["build_id"])).fetchone()
    if existing: return str(existing[0])
    download_uuid = str(uuid.uuid4())
    conn.execute("""INSERT INTO update_downloads(uuid,version,channel,build_id,package_url_sanitized,expected_sha256,expected_size,status,bytes_total,created_by_user_id) VALUES(?,?,?,?,?,?,?,'queued',?,?)""", (download_uuid, release["version"], release["channel"], release["build_id"], sanitize_url(release["package_url"]), release["package_sha256"], release["package_size"], release["package_size"], user_id))
    _audit(conn, "update.download_queued", download_uuid, f"version={release['version']}", user_id)
    return download_uuid


def cancel_download(conn: sqlite3.Connection, download_uuid: str, *, user_id: int | None = None) -> bool:
    changed = conn.execute("UPDATE update_downloads SET status='cancelled',finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE uuid=? AND status IN ('queued','downloading')", (download_uuid,)).rowcount
    if changed: _audit(conn, "update.download_cancelled", download_uuid, user_id=user_id)
    return bool(changed)


def run_download_once(conn: sqlite3.Connection, *, client: SecureHTTPClient | None = None, public_key: str | Path = DEFAULT_PUBLIC_KEY, update_root: str | Path = DEFAULT_UPDATE_ROOT) -> str | None:
    row = conn.execute("SELECT * FROM update_downloads WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
    if not row: return None
    download_uuid = row["uuid"]
    release = json.loads(_setting(conn, "update_available_release_json", "{}"))
    if not release or release.get("version") != row["version"] or release.get("build_id") != row["build_id"]:
        conn.execute("UPDATE update_downloads SET status='failed',error_code='RELEASE_CHANGED',error_message='Release selecionada mudou.',finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE uuid=?", (download_uuid,)); return download_uuid
    _, _, allowed, lab = repository_config(conn)
    root = Path(update_root); temporary_dir = root / "temporary"; incoming = root / "incoming"
    temporary_dir.mkdir(parents=True, exist_ok=True); incoming.mkdir(parents=True, exist_ok=True)
    os.chmod(temporary_dir, 0o700); os.chmod(incoming, 0o700)
    partial = temporary_dir / f"{download_uuid}.partial"; target = incoming / f"{download_uuid}.bmu"
    http = client or SecureHTTPClient()
    conn.execute("UPDATE update_downloads SET status='downloading',started_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE uuid=?", (download_uuid,)); _audit(conn, "update.download_started", download_uuid)
    conn.commit()
    try:
        signature = http.read(release["signature_url"], limit=MAX_SIGNATURE_BYTES, content_types={"application/octet-stream", "text/plain"}, allowed_hosts=allowed, lab_mode=lab)
        digest = hashlib.sha256(); downloaded = 0
        with http.open(release["package_url"], allowed_hosts=allowed, lab_mode=lab) as response, partial.open("xb") as output:
            content_type = response.headers.get_content_type().lower()
            if content_type not in {"application/octet-stream", "application/gzip", "application/x-gzip"}: raise RepositoryError("CONTENT_TYPE_INVALID")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk: break
                downloaded += len(chunk)
                if downloaded > row["expected_size"] or downloaded > MAX_PACKAGE_BYTES: raise RepositoryError("PACKAGE_SIZE")
                digest.update(chunk); output.write(chunk)
                current = conn.execute("SELECT status FROM update_downloads WHERE uuid=?", (download_uuid,)).fetchone()[0]
                if current == "cancelled": raise RepositoryError("CANCELLED")
                conn.execute("UPDATE update_downloads SET bytes_downloaded=?,updated_at=CURRENT_TIMESTAMP WHERE uuid=?", (downloaded, download_uuid)); conn.commit()
        actual = digest.hexdigest()
        if downloaded != row["expected_size"]: raise RepositoryError("SIZE_MISMATCH")
        if actual != row["expected_sha256"]: raise RepositoryError("HASH_MISMATCH")
        try: load_public_key(public_key).verify(_signature(signature), actual.encode("ascii"))
        except InvalidSignature as exc: raise RepositoryError("SIGNATURE_INVALID") from exc
        conn.execute("UPDATE update_downloads SET status='validating',updated_at=CURRENT_TIMESTAMP WHERE uuid=?", (download_uuid,)); conn.commit()
        result = validate_package(partial, public_key)
        if result.manifest["version"] != row["version"] or result.manifest["build_id"] != row["build_id"]: raise RepositoryError("PACKAGE_RELEASE_MISMATCH")
        os.replace(partial, target)
        operation = create_operation(conn, target.name, row["created_by_user_id"])
        record_validation(conn, operation, result)
        conn.execute("UPDATE update_downloads SET status='ready',bytes_downloaded=?,finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE uuid=?", (downloaded, download_uuid))
        _audit(conn, "update.download_success", download_uuid, f"version={row['version']}"); _audit(conn, "update.package_ready", operation, f"download={download_uuid}")
    except (RepositoryError, UpdateError, OSError, urllib.error.URLError) as exc:
        partial.unlink(missing_ok=True)
        code = getattr(exc, "code", "DOWNLOAD_FAILED")
        if code != "CANCELLED":
            conn.execute("UPDATE update_downloads SET status='failed',error_code=?,error_message='Download ou validação recusado.',finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE uuid=? AND status!='cancelled'", (str(code), download_uuid)); _audit(conn, "update.download_failed", download_uuid, f"code={code}")
    return download_uuid
