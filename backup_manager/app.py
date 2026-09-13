from __future__ import annotations

import html
import hashlib
import hmac
import ipaddress
import io
import json
import logging
import mimetypes
import os
import re
import sqlite3
import time
import uuid
from functools import lru_cache
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from wsgiref.simple_server import WSGIRequestHandler, make_server
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import maxminddb
except ImportError:
    maxminddb = None

from .db import DATA_DIR, SchemaCompatibilityError, check_schema_version, connect, require_schema_current, schema_problems

logger = logging.getLogger("backup_manager.web")
from .settings_center import (
    certificate_summary,
    ensure_roles,
    export_configuration,
    https_checks,
    import_configuration,
    role_description,
    run_https_helper,
    save_branding,
    system_summary,
    validate_email,
    validate_general,
)
from .reports import (
    audit_report,
    backup_report,
    choices as report_choices,
    csv_export,
    daily_chart,
    equipment_gap_summary,
    equipment_report,
    export_dataset,
    failure_report,
    ftp_report,
    telegram_report,
    overview as report_overview,
    pdf_export,
    storage_report,
    xlsx_export,
)
from .jobs import (
    JOB_METHODS,
    JOB_STATUSES,
    RUN_STATUSES,
    SCHEDULE_TYPES,
    TRIGGER_TYPES,
    create_job,
    queue_run,
    update_job,
)
from .jobs_views import job_runs_page_context, jobs_page_context, run_detail_context
from .security import SecretKeyError, decrypt_secret, hash_password, new_token, verify_password
from .olt_backup import OLTBackupError, OLTBackupRequest, build_plan
from .olt_runtime import trigger_olt_backup
from .mikrotik_ftp_credentials import rotate_credential as rotate_mikrotik_credential
from .mikrotik_ftp_scripts import (
    generate_install_script as generate_mikrotik_install_script,
    generate_removal_script as generate_mikrotik_removal_script,
    generate_test_script as generate_mikrotik_test_script,
)
from .mikrotik_ftp_service import (
    FINAL_TEST_STATES,
    create_integration as create_mikrotik_integration,
    create_test as create_mikrotik_test,
    equipment_snapshot as mikrotik_equipment_snapshot,
    expire_test as expire_mikrotik_test,
    integration_config as mikrotik_integration_config,
    install_integration_via_ssh,
    sanitize_error as sanitize_mikrotik_error,
    validate_time as validate_mikrotik_time,
)
from .equipment_compatibility import (
    compatibility_error,
    is_strict_mikrotik,
    require_mikrotik_integration,
)
from .ssh import (
    ERROR_MESSAGES,
    LEGACY_DRIVER_LABELS,
    SSH_DRIVER_LABELS,
    SSH_DRIVER_GROUPS,
    SSH_DRIVERS,
    SSHBackupError,
    run_routeros_ftp_test,
    run_routeros_managed_backup,
    create_credential,
    install_routeros_script,
    is_ftp_push_olt_driver,
    remove_routeros_backup_manager,
    probe_connection,
    test_connection,
    update_credential,
    validate_custom_command,
)
from .storage import (
    atomic_move,
    backup_relative_path,
    ensure_directories,
    format_size,
    load_config,
    resolve_inside,
    safe_filename,
    trash_expiration,
    trash_relative_path,
    write_upload_to_temporary,
)
from .ftp import FTPAccountError, run_helper, validate_source_network, validate_upload_subdirectory
from .ftp_provisioning import FTPProvisioningService
from .lifecycle import (
    CONFIRMATION as LIFECYCLE_CONFIRMATION,
    EMPTY_TRASH_CONFIRMATION,
    PURGE_CONFIRMATION,
    empty_trash as lifecycle_empty_trash,
    execute as execute_lifecycle,
    global_policy,
    health_check as lifecycle_health_check,
    restore as lifecycle_restore,
    purge_backup as lifecycle_purge_backup,
    save_policy,
    simulate as simulate_lifecycle,
    stats as lifecycle_stats,
)
from .observability import dashboard_snapshot
from .operation_states import canonical_operation_state
from .equipment_lifecycle import archive_equipment
from .equipment_dependencies import EQUIPMENT_DEPENDENCY_LABELS, equipment_dependencies
from .equipment_purge import purge_equipment
from .backup_details import backup_detail_context
from .equipment_detail import equipment_detail_context
from .equipment_views import equipment_page_context
from .cloud_views import cloud_page_context
from .ftp_accounts import account_detail as ftp_account_detail, list_accounts as ftp_list_accounts
from .dashboard_views import dashboard_page_content
from .equipment_page_views import equipment_page_content
from .equipment_detail_panels import mikrotik_equipment_panel
from .equipment_detail_views import equipment_detail_content
from .backup_import_views import handle_backup_import_logic
from .mikrotik_ftp_create_views import handle_mikrotik_ftp_create_logic
from .backups_views import backups_page_context, backups_page_content
from .updates_views import updates_page_context
from .settings_views import settings_tab_content as settings_tab_content_view
from .equipment_run_service import handle_equipment_run_now_logic
from .ssh_test_service import handle_equipment_ssh_test_logic
from .report_views import (
    duration_label,
    hidden,
    report_filter_values,
    report_filter_form,
    report_header,
    report_icon,
    report_pager,
    report_query,
)
from .presentation import render_template, static_url, static_version
from .notifications import (channel as notification_channel, configure_telegram, notification_next_send,
                            notification_page_context, queue_basic_test, telegram_diagnostic)
from .notifications_page_views import notifications_page_content
from .telegram_backup_views import telegram_backup_page_content
from .lifecycle_views import lifecycle_page_content
from .cloud_sync import (cancel_item as cloud_cancel_item, enable_automatic_backup as enable_cloud_automatic_backup,
    enqueue_backup as cloud_enqueue_backup, enqueue_new_backup, retry_item as cloud_retry_item)
from .telegram_backup import enqueue_backup as telegram_enqueue_backup, enqueue_new_backup as enqueue_new_telegram_backup
from .telegram_backup import (cancel_item as telegram_cancel_item, create_destination as telegram_create_destination,
    queue_test_file as telegram_queue_test_file, retry_item as telegram_retry_item, sanitize_chat_id)
from .telegram_summaries import schedule_summary_test
from .telegram_formatting import status_label
from .version import __build_id__, __channel__, __version__
from .audit_catalog import definition as audit_definition, resolve_entity_name, safe_technical_details
from .updater import DEFAULT_PUBLIC_KEY, DEFAULT_UPDATE_ROOT, UpdateError, create_operation, record_validation, validate_package
from .update_repository import RepositoryError, cancel_download, check_updates, queue_download, sanitize_url
from .rclone_backend import (PROVIDERS as RCLONE_PROVIDERS, RcloneError, begin_remote as begin_rclone_remote,
    cancel_remote as cancel_rclone_remote,
    create_drive_remote as create_rclone_drive_remote,
    continue_remote as continue_rclone_remote, list_remotes as list_rclone_remotes,
    normalize_path as normalize_rclone_path, normalize_remote,
    test_remote as test_rclone_remote)
from .google_rclone_oauth import (GoogleOAuthError, begin as begin_google_rclone_oauth,
    complete as complete_google_rclone_oauth, target_for_state as google_rclone_target_for_state)

ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT_DIR / "static"
SESSION_COOKIE = "bm_session"
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,62}$")
IP_RE = re.compile(r"^[0-9A-Fa-f:.]{3,45}$")
ADDRESS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,252}$")
BACKUP_STATUSES = ("available", "trashed", "quarantined", "failed", "deleted")
SOURCE_METHODS = ("manual", "ssh", "ftp", "sftp", "tftp", "api", "system")
DAY_LABELS = [("0", "Seg"), ("1", "Ter"), ("2", "Qua"), ("3", "Qui"), ("4", "Sex"), ("5", "Sab"), ("6", "Dom")]
DISPLAY_TIMEZONE: ContextVar[str] = ContextVar("display_timezone", default="America/Sao_Paulo")
DISPLAY_DATE_FORMAT: ContextVar[str] = ContextVar("display_date_format", default="ymd")
DISPLAY_TIME_FORMAT: ContextVar[str] = ContextVar("display_time_format", default="24h")
REQUEST_IP: ContextVar[str] = ContextVar("request_ip", default="")
REQUEST_CSRF: ContextVar[str] = ContextVar("request_csrf", default="")
GOOGLE_RCLONE_CALLBACK_PATH = "/cloud/rclone/google/callback"
CSRF_EXEMPT_POST_PATHS = {"/login", "/setup"}


@dataclass
class UploadedFile:
    filename: str
    file: io.BytesIO


class Response:
    def __init__(
        self,
        body: str | bytes = "",
        status: HTTPStatus = HTTPStatus.OK,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = headers or []


class ProductionRequestHandler(WSGIRequestHandler):
    """Evita duplicar no journal os access logs já mantidos pelo Nginx."""

    def log_request(self, code: str | int = "-", size: str | int = "-") -> None:
        try:
            status = int(code)
        except (TypeError, ValueError):
            status = 500
        if status >= 400:
            super().log_request(code, size)


def redirect(location: str) -> Response:
    return Response("", HTTPStatus.FOUND, [("Location", location)])


def json_response(data: dict, status: HTTPStatus = HTTPStatus.OK) -> Response:
    return Response(
        json.dumps(data, separators=(",", ":")),
        status,
        [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")],
    )


def redirect_with_message(location: str, kind: str, text: str) -> Response:
    query = urlencode({"notice": kind, "message": text})
    separator = "&" if "?" in location else "?"
    return redirect(f"{location}{separator}{query}")


def wants_json(environ: dict) -> bool:
    return "application/json" in environ.get("HTTP_ACCEPT", "").lower()


def operation_response(environ: dict, conn, equipment_id: int, payload: dict,
                       status: HTTPStatus = HTTPStatus.OK) -> Response:
    """Return the operation contract to AJAX, with a durable flash fallback."""
    logger.info("mikrotik operation response equipment_id=%s operation_id=%s status=%s ok=%s",
                equipment_id, payload.get("operation_id"), payload.get("status"), payload.get("ok"))
    if wants_json(environ):
        return json_response(payload, status)
    kind = "success" if payload.get("ok") else "error"
    if payload.get("status") in {"needs_repair", "waiting_upload", "running", "pending"}:
        kind = "info"
    queue_flash(conn, environ, kind, payload["message"])
    return redirect(f"/equipment/{equipment_id}")


def equipment_redirect(equipment_id: int, kind: str = "", text: str = "") -> Response:
    location = f"/equipment/{equipment_id}"
    return redirect_with_message(location, kind, text) if kind and text else redirect(location)


def queue_flash(conn, environ: dict, kind: str, text: str) -> None:
    token = cookie_token(environ)
    if not token or kind not in {"error", "success", "info"} or not text:
        return
    conn.execute(
        """
        INSERT INTO flash_messages(session_token, kind, text)
        SELECT token, ?, ? FROM sessions WHERE token = ?
        """,
        (kind, text[:500], token),
    )


def pop_flash(environ: dict) -> tuple[str, str]:
    token = cookie_token(environ)
    if not token:
        return "", ""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, kind, text
            FROM flash_messages
            WHERE session_token = ?
            ORDER BY id
            LIMIT 1
            """,
            (token,),
        ).fetchone()
        if not row:
            return "", ""
        conn.execute("DELETE FROM flash_messages WHERE id = ?", (row["id"],))
        return row["kind"], row["text"]


def e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


@lru_cache(maxsize=2048)
def ip_geo_summary(address: str) -> tuple[str, str, str]:
    """Retorna bandeira, país e ASN usando somente as bases GeoLite2 locais."""
    try:
        parsed = ipaddress.ip_address(address)
        if not parsed.is_global or maxminddb is None:
            return "xx", "Rede local" if not parsed.is_global else "Não identificado", "-"
        with maxminddb.open_database("/usr/share/GeoIP/GeoLite2-Country.mmdb") as reader:
            country_record = reader.get(address) or {}
        with maxminddb.open_database("/usr/share/GeoIP/GeoLite2-ASN.mmdb") as reader:
            asn_record = reader.get(address) or {}
        country = country_record.get("country") or country_record.get("registered_country") or {}
        code = str(country.get("iso_code") or "").upper()
        names = country.get("names") or {}
        name = names.get("pt-BR") or names.get("pt") or names.get("en") or "Não identificado"
        flag = code.lower() if len(code) == 2 else "xx"
        number = asn_record.get("autonomous_system_number")
        organization = asn_record.get("autonomous_system_organization") or "Operadora não identificada"
        return flag, str(name), f"AS{number} · {organization}" if number else "-"
    except (ValueError, OSError, TypeError):
        return "xx", "Não identificado", "-"


def quantity(count: int, singular: str, plural: str | None = None) -> str:
    """Formata contagens exibidas na interface com pluralização consistente."""
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def short_identifier(value: object, limit: int = 12) -> str:
    text = str(value or "-")
    return text if len(text) <= limit else f"{text[:limit]}…"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_form(environ: dict) -> dict[str, str]:
    cached = environ.get("backup_manager.parsed_form")
    if isinstance(cached, dict):
        return cached
    length = int(environ.get("CONTENT_LENGTH") or 0)
    raw = environ["wsgi.input"].read(length).decode("utf-8")
    form = {key: values[-1] for key, values in parse_qs(raw).items()}
    environ["backup_manager.parsed_form"] = form
    return form


def parse_multipart(environ: dict) -> tuple[dict[str, str], dict[str, UploadedFile]]:
    cached = environ.get("backup_manager.parsed_multipart")
    if isinstance(cached, tuple):
        return cached
    content_type = environ.get("CONTENT_TYPE", "")
    match = re.search(r"boundary=(?P<boundary>[^;]+)", content_type)
    if not match:
        raise ValueError("Upload multipart inválido.")
    boundary = match.group("boundary").strip().strip('"').encode("utf-8")
    length = int(environ.get("CONTENT_LENGTH") or 0)
    body = environ["wsgi.input"].read(length)
    fields: dict[str, str] = {}
    files: dict[str, UploadedFile] = {}
    marker = b"--" + boundary
    for part in body.split(marker):
        part = part.lstrip(b"\r\n")
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].rstrip(b"\r\n")
        elif part.endswith(b"\r\n"):
            part = part[:-2]
        if b"\r\n\r\n" not in part:
            continue
        raw_headers, payload = part.split(b"\r\n\r\n", 1)
        headers = raw_headers.decode("utf-8", errors="replace").split("\r\n")
        disposition = next((h for h in headers if h.lower().startswith("content-disposition:")), "")
        name_match = re.search(r'name="([^"]+)"', disposition)
        if not name_match:
            continue
        name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]*)"', disposition)
        if filename_match:
            files[name] = UploadedFile(filename_match.group(1), io.BytesIO(payload))
        else:
            fields[name] = payload.decode("utf-8", errors="replace")
    parsed = (fields, files)
    environ["backup_manager.parsed_multipart"] = parsed
    environ["backup_manager.parsed_form"] = fields
    return parsed


def cookie_token(environ: dict) -> str | None:
    cookie = SimpleCookie(environ.get("HTTP_COOKIE", ""))
    morsel = cookie.get(SESSION_COOKIE)
    return morsel.value if morsel else None


def session_csrf(environ: dict) -> str:
    token=cookie_token(environ) or ""
    return hashlib.sha256(("session-csrf:"+token).encode()).hexdigest() if token else ""


def valid_session_csrf(environ: dict,value: str) -> bool:
    expected=session_csrf(environ)
    return bool(expected and hmac.compare_digest(expected,value or ""))


def request_csrf_value(environ: dict) -> str:
    header = str(environ.get("HTTP_X_CSRF_TOKEN", "")).strip()
    if header:
        return header
    content_type = environ.get("CONTENT_TYPE", "").lower()
    try:
        if content_type.startswith("multipart/form-data"):
            fields, _ = parse_multipart(environ)
            return fields.get("csrf_token", "")
        return parse_form(environ).get("csrf_token", "")
    except (OSError, ValueError):
        return ""


def csrf_protect(environ: dict) -> Response | None:
    if environ.get("REQUEST_METHOD") != "POST":
        return None
    if environ.get("PATH_INFO", "") in CSRF_EXEMPT_POST_PATHS:
        return None
    if valid_session_csrf(environ, request_csrf_value(environ)):
        return None
    return Response("Token CSRF inválido.", HTTPStatus.FORBIDDEN)


def set_session_cookie(token: str) -> tuple[str, str]:
    return (
        "Set-Cookie",
        f"{SESSION_COOKIE}={token}; Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age=28800",
    )


def clear_session_cookie() -> tuple[str, str]:
    return (
        "Set-Cookie",
        f"{SESSION_COOKIE}=; Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age=0",
    )


def audit(conn, user_id: int | None, action: str, entity: str, entity_id: object = "", details: str = "", ip: str = "") -> int:
    cursor = conn.execute(
        """
        INSERT INTO audit_log(user_id, action, entity, entity_id, details, ip_address)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, action, entity, str(entity_id), details, ip),
    )
    return cursor.lastrowid


def client_ip(environ: dict) -> str:
    """Resolve the client address, trusting proxy headers only from loopback."""
    remote = str(environ.get("REMOTE_ADDR", "")).strip()
    try:
        remote_address = ipaddress.ip_address(remote)
    except ValueError:
        return ""
    if remote_address.is_loopback:
        forwarded = str(environ.get("HTTP_X_REAL_IP", "")).strip()
        try:
            return str(ipaddress.ip_address(forwarded)) if forwarded else str(remote_address)
        except ValueError:
            return str(remote_address)
    return str(remote_address)


def current_user(environ: dict):
    token = cookie_token(environ)
    if not token:
        return None
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < datetime('now')")
        return conn.execute(
            """
            SELECT users.*, roles.slug AS role_slug, roles.name AS role_name, roles.can_admin, roles.can_download
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            JOIN roles ON roles.id = users.role_id
            WHERE sessions.token = ? AND users.is_active = 1
            """,
            (token,),
        ).fetchone()


def require_user(environ: dict):
    user = current_user(environ)
    if not user:
        return None, redirect("/login")
    if user["must_change_password"] and environ.get("PATH_INFO") != "/change-password":
        return None, redirect("/change-password")
    return user, None


def require_admin(environ: dict):
    user, response = require_user(environ)
    if response:
        return None, response
    if not user["can_admin"]:
        return None, render("Acesso negado", "<p>Seu perfil permite somente leitura e download.</p>", user, HTTPStatus.FORBIDDEN)
    return user, None


def can_operate(user) -> bool:
    return bool(user and (user["can_admin"] or user["role_slug"] == "operator"))


def require_operator(environ: dict):
    user, response = require_user(environ)
    if response:
        return None, response
    if not can_operate(user):
        return None, render("Acesso negado", "<p>Seu perfil permite somente consulta e download.</p>", user, HTTPStatus.FORBIDDEN)
    return user, None


def require_download(environ: dict):
    user, response = require_user(environ)
    if response:
        return None, response
    if not user["can_download"] and not user["can_admin"]:
        return None, render("Acesso negado", "<p>Seu perfil não permite download.</p>", user, HTTPStatus.FORBIDDEN)
    return user, None


def get_setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings(key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """,
        (key, value),
    )


def local_dt(value: str | None) -> str:
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        zone = ZoneInfo(DISPLAY_TIMEZONE.get())
        date_pattern = "%d/%m/%Y" if DISPLAY_DATE_FORMAT.get() == "dmy" else "%Y-%m-%d"
        time_pattern = "%I:%M:%S %p" if DISPLAY_TIME_FORMAT.get() == "12h" else "%H:%M:%S"
        return parsed.astimezone(zone).strftime(f"{date_pattern} {time_pattern}")
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return value


def relative_time(value: str | None, now: datetime | None = None) -> str:
    """Retorna idade curta para timestamps UTC válidos, sem mascarar valores incompletos."""
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        seconds = max(0, int(((now or now_utc()) - parsed.astimezone(timezone.utc)).total_seconds()))
        if seconds < 60:
            return "agora"
        if seconds < 3600:
            return f"há {seconds // 60} min"
        if seconds < 86400:
            return f"há {seconds // 3600} h"
        return f"há {seconds // 86400} d"
    except (TypeError, ValueError):
        return ""


def short_hash(value: str | None) -> str:
    return (value or "")[:12]


def backup_details_json(row) -> str:
    details = {
        "backup_uuid": row["uuid"],
        "equipment_id": row["equipment_id"],
        "size": row["file_size"],
        "method": row["source_method"],
        "status": row["backup_status"],
    }
    return json.dumps(details, separators=(",", ":"))


def hidden(name: str, value: object) -> str:
    return f'<input type="hidden" name="{e(name)}" value="{e(value)}">'


def setup_complete(conn) -> bool:
    return get_setting(conn, "setup_complete") == "1"


def _legacy_layout(title: str, content: str, user=None) -> str:
    nav = ""
    breadcrumbs = ""
    if user:
        operation_admin = ""
        services_admin = ""
        administration = ""
        if user["can_admin"]:
            operation_admin = """
            <a href="/equipment"><span class="nav-icon">▣</span><span>Equipamentos</span></a>
            """
            services_admin = """
            <a href="/lifecycle"><span class="nav-icon">♲</span><span>Retenção</span></a>
            """
            administration = """
            <a href="/settings"><span class="nav-icon">⚙</span><span>Configurações</span></a>
            <a href="/audit"><span class="nav-icon">≡</span><span>Logs e Auditoria</span></a>
            """
        nav = f"""
        <aside class="sidebar" id="main-sidebar">
          <div class="brand">
            <span class="brand-mark" aria-hidden="true">↥</span>
            <div><strong>Backup Manager</strong><small>Local</small></div>
          </div>
          <nav aria-label="Navegação principal">
            <span class="nav-group-title">Operação</span>
            <a href="/"><span class="nav-icon">⌂</span><span>Dashboard</span></a>
            <a href="/backups"><span class="nav-icon">▤</span><span>Backups</span></a>
            <a href="/jobs"><span class="nav-icon">◷</span><span>Agendamentos</span></a>
            {operation_admin}
            <span class="nav-group-title">Serviços</span>
            <a href="/ftp"><span class="nav-icon">⇅</span><span>Recebimento por FTP</span></a>
            <a href="/cloud"><span class="nav-icon">☁</span><span>Sincronização externa</span></a>
            <a href="/telegram-backup"><span class="nav-icon">✈</span><span>Cópia no Telegram</span></a>
            {services_admin}
            {f'<span class="nav-group-title">Administração</span>{administration}' if administration else ''}
          </nav>
          <footer class="sidebar-footer">
            <div class="sidebar-user"><span class="avatar" aria-hidden="true">{e((user['full_name'] or user['username'])[:1].upper())}</span><div><strong>{e(user['username'])}</strong><small>Backup Manager</small></div></div>
            <form class="sidebar-logout" method="post" action="/logout"><button class="ghost" type="submit"><span class="nav-icon">↪</span><span>Sair</span></button></form>
          </footer>
        </aside>
        """
        breadcrumbs = f'<nav class="breadcrumbs" aria-label="Breadcrumb"><a href="/">Início</a><span aria-hidden="true">/</span><span>{e(title)}</span></nav>'
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{e(title)} - Backup Manager Local</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  <div class="app-shell">
    {nav}
    <section class="workspace">
      {f'''<header class="topbar">
        <button class="menu-toggle" type="button" aria-label="Abrir menu" aria-controls="main-sidebar" aria-expanded="false">☰</button>
        <div class="environment"><span>Ambiente:</span><strong>Todos</strong></div>
        <div class="topbar-user"><span class="topbar-status" title="Sistema online"></span><div><strong>{e(user['full_name'])}</strong><small>{e(user['username'])}</small></div><span class="avatar" aria-hidden="true">{e((user['full_name'] or user['username'])[:1].upper())}</span></div>
      </header>''' if user else ''}
      <main class="content">
        {breadcrumbs}
        {content}
      </main>
    </section>
  </div>
  <script>
  (() => {{
    const sidebar = document.querySelector(".sidebar");
    const menuToggle = document.querySelector(".menu-toggle");
    const currentPath = window.location.pathname;
    document.querySelectorAll(".sidebar nav a").forEach((link) => {{
      const path = new URL(link.href).pathname;
      const active = path === "/" ? currentPath === "/" : currentPath === path || currentPath.startsWith(path + "/");
      if (active) link.classList.add("active");
    }});
    menuToggle?.addEventListener("click", () => {{
      const open = sidebar?.classList.toggle("is-open") || false;
      menuToggle.setAttribute("aria-expanded", String(open));
    }});
    const finalStates = new Set(["success", "failed", "timeout", "cancelled", "skipped"]);
    document.querySelectorAll(".notice[data-auto-dismiss]").forEach((notice) => {{
      const delay = Number(notice.dataset.autoDismiss || "6000");
      let remaining = delay;
      let started = Date.now();
      let timer = 0;
      const clear = () => window.clearTimeout(timer);
      const resume = () => {{
        clear();
        started = Date.now();
        timer = window.setTimeout(() => notice.remove(), remaining);
      }};
      const pause = () => {{
        clear();
        remaining = Math.max(0, remaining - (Date.now() - started));
      }};
      notice.addEventListener("mouseenter", pause);
      notice.addEventListener("mouseleave", resume);
      notice.addEventListener("focusin", pause);
      notice.addEventListener("focusout", resume);
      resume();
    }});
    const loadingMessages = {{
      "/ssh-test": "Testando conexão...",
      "/jobs/run-now": "Gerando backup...",
      "/backups/import": "Importando...",
      "/lifecycle/execute": "Limpando...",
      "/restore": "Restaurando...",
      "/settings/notifications/test": "Enviando notificação..."
    }};
    document.querySelectorAll('form[method="post"]').forEach((form) => {{
      form.addEventListener("submit", () => {{
        const button = form.querySelector('button[type="submit"], button:not([type])');
        if (!button || button.disabled) return;
        const action = form.getAttribute("action") || "";
        const matched = Object.entries(loadingMessages).find(([suffix]) => action.endsWith(suffix));
        if (!matched) return;
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        button.textContent = matched[1];
      }});
    }});
    document.querySelectorAll("[data-ssh-run-panel]").forEach((panel) => {{
      const url = panel.dataset.statusUrl;
      const statusEl = panel.querySelector("[data-run-status]");
      const triggerEl = panel.querySelector("[data-run-trigger]");
      const methodEl = panel.querySelector("[data-run-method]");
      const startedEl = panel.querySelector("[data-run-started]");
      const finishedEl = panel.querySelector("[data-run-finished]");
      const durationEl = panel.querySelector("[data-run-duration]");
      const errorEl = panel.querySelector("[data-run-error]");
      const messageEl = panel.querySelector("[data-run-message]");
      const backupInfo = panel.querySelector("[data-backup-info]");
      const backupActions = panel.querySelector("[data-backup-actions]");
      if (!url || !statusEl || !messageEl) return;
      const apply = (data) => {{
        statusEl.textContent = data.status || "-";
        statusEl.className = `badge status-${{data.status || "unknown"}}`;
        triggerEl.textContent = data.trigger_type || "-";
        methodEl.textContent = data.method || "-";
        startedEl.textContent = data.started_at || "-";
        finishedEl.textContent = data.finished_at || "-";
        durationEl.textContent = data.duration_ms === null || data.duration_ms === undefined ? "-" : `${{data.duration_ms}} ms`;
        errorEl.textContent = data.error_code || "-";
        messageEl.textContent = data.safe_message || "-";
        panel.dataset.currentStatus = data.status || "";
        if (backupInfo) {{
          if (data.created_backup_uuid) {{
            const size = data.created_backup_size ? ` (${{data.created_backup_size}})` : "";
            backupInfo.textContent = `${{data.created_backup_name || data.created_backup_uuid}}${{size}}`;
          }} else {{
            backupInfo.textContent = data.status === "failed" ? "Nenhum arquivo foi criado." : "-";
          }}
        }}
        if (backupActions) {{
          if (data.created_backup_uuid) {{
            backupActions.hidden = false;
            if (!backupActions.querySelector("[data-backup-href]")) {{
              const detail = document.createElement("a");
              detail.className = "button secondary";
              detail.dataset.backupHref = "1";
              detail.textContent = "Ver backup";
              const download = document.createElement("a");
              download.className = "button secondary";
              download.dataset.backupHref = "1";
              download.textContent = "Baixar";
              backupActions.append(detail, download);
            }}
            backupActions.querySelectorAll("[data-backup-href]").forEach((link, index) => {{
              link.href = index === 0 ? `/backups/${{data.created_backup_uuid}}` : `/backups/${{data.created_backup_uuid}}/download`;
            }});
          }} else {{
            backupActions.hidden = true;
          }}
        }}
        return finalStates.has(data.status);
      }};
      const poll = () => {{
        fetch(url, {{headers: {{"Accept": "application/json"}}}})
          .then((response) => response.ok ? response.json() : Promise.reject())
          .then((data) => {{
            if (!apply(data)) window.setTimeout(poll, 4000);
          }})
          .catch(() => window.setTimeout(poll, 6000));
      }};
      if (!finalStates.has(panel.dataset.currentStatus || "")) window.setTimeout(poll, 3000);
    }});
  }})();
  </script>
</body>
</html>"""


def layout(title: str, content: str, user=None) -> str:
    navigation = topbar = breadcrumbs = ""
    with connect() as conn:
        has_favicon = bool(get_setting(conn, "branding_favicon", ""))
        selected_avatar = get_setting(conn, f"profile_avatar_icon_{user['id']}", "initial") if user else "initial"
    if user:
        operation_admin = '<a href="/reports"><svg class="nav-icon" aria-hidden="true"><use href="#nav-reports"/></svg><span>Relatórios</span></a>'
        services_admin = administration = ""
        if can_operate(user):
            operation_admin += '<a href="/equipment"><svg class="nav-icon" aria-hidden="true"><use href="#nav-equipment"/></svg><span>Equipamentos</span></a>'
        if user["can_admin"]:
            services_admin = '<a href="/lifecycle"><svg class="nav-icon" aria-hidden="true"><use href="#nav-lifecycle"/></svg><span>Retenção de backups</span></a>'
            administration = ('<span class="nav-group-title">Administração</span>'
                              '<a href="/settings"><svg class="nav-icon" aria-hidden="true"><use href="#nav-settings"/></svg><span>Configurações</span></a>'
                              '<a href="/audit"><svg class="nav-icon" aria-hidden="true"><use href="#nav-audit"/></svg><span>Logs e Auditoria</span></a>')
        initial = e((user["full_name"] or user["username"])[:1].upper())
        navigation = render_template(
            "components/sidebar.html", operation_admin=operation_admin,
            services_navigation=(
                '<span class="nav-group-title">Serviços</span>'
                '<a href="/ftp"><svg class="nav-icon" aria-hidden="true"><use href="#nav-ftp"/></svg><span>FTP Server</span></a>'
                '<a href="/cloud"><svg class="nav-icon" aria-hidden="true"><use href="#nav-cloud"/></svg><span>Backup externo</span></a>'
                f'{services_admin}'
            ),
            administration=administration,
            username=e(user["username"]), initial=initial,
            avatar_content=avatar_content(selected_avatar, initial),
            sidebar_class="sidebar-main-reference",
            system_status=(
                '<section class="sidebar-system-status" aria-label="Status do sistema">'
                '<small>Sistema operacional</small>'
                '<strong><span aria-hidden="true"></span>Todos os serviços</strong>'
                '<em>Online</em></section>'
            ),
        )
        navigation = (navigation.replace("<span>Jobs</span>", "<span>Agendamentos</span>")
                      .replace("<span>FTP Server</span>", "<span>Recebimento por FTP</span>")
                      .replace("<span>Backup externo</span>", "<span>Sincronização externa</span>"))
        role_label = "Administrador" if user["can_admin"] else "Operador"
        full_name = user["full_name"] or user["username"]
        topbar = render_template(
            "components/topbar.html", full_name=e(full_name),
            username=e(user["username"]),
            role_label=role_label, client_ip=e(REQUEST_IP.get() or "-"),
            initial=initial, avatar_content=avatar_content(selected_avatar, initial),
            avatar_options=avatar_options(selected_avatar, initial),
            avatar_csrf=e(REQUEST_CSRF.get()),
        )
        breadcrumbs = f'<nav class="breadcrumbs" aria-label="Breadcrumb"><a href="/">Início</a><span aria-hidden="true">/</span><span>{e(title)}</span></nav>'
    page = render_template(
        "base.html", title=e(title), css_url=e(static_url("app.css")),
        js_url=e(static_url("app.js")), navigation=navigation, topbar=topbar,
        breadcrumbs=breadcrumbs, content=content,
        csrf_meta=(f'<meta name="csrf-token" content="{e(REQUEST_CSRF.get())}">' if user else ""),
        body_class="main-page-sidebar" if user else "",
    )
    page = page.replace("</head>", f'<link rel="stylesheet" href="{e(static_url("settings.css"))}">\n</head>', 1)
    if has_favicon:
        page = page.replace("</head>", '<link rel="icon" href="/settings/branding/favicon">\n</head>', 1)
    return page


def render(title: str, content: str, user=None, status: HTTPStatus = HTTPStatus.OK) -> Response:
    return Response(layout(title, content, user), status, [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Cache-Control", "no-store"),
    ])


def message(kind: str, text: str) -> str:
    dismiss_ms = {"success": "5000", "info": "6000", "error": "8000"}.get(kind, "")
    auto_dismiss = f' data-auto-dismiss="{dismiss_ms}"' if dismiss_ms else ""
    return (
        f'<div class="notice {e(kind)}" role="status" aria-live="polite"{auto_dismiss}>'
        f'<span>{e(text)}</span><button class="notice-close" type="button" aria-label="Fechar mensagem" onclick="this.parentElement.remove()">×</button>'
        f'</div>'
        if text
        else ""
    )


def login_page(error: str = "") -> Response:
    content = render_template("login.html", error_message=message("error", error) if error else "")
    return render("Login", content)


def handle_login(environ: dict) -> Response:
    form = parse_form(environ)
    username = form.get("username", "").strip()
    password = form.get("password", "")
    source_ip = client_ip(environ)
    with connect() as conn:
        recent_failures = conn.execute(
            """SELECT COUNT(*) FROM audit_log
               WHERE action='login_failed' AND created_at>=datetime('now','-15 minutes')
                 AND (entity_id=? OR ip_address=?)""",
            (username, source_ip),
        ).fetchone()[0]
        if recent_failures >= 8:
            audit(conn, None, "login_throttled", "user", username,
                  "Limite temporário de tentativas excedido", source_ip)
            blocked = login_page("Muitas tentativas de acesso. Aguarde 15 minutos e tente novamente.")
            blocked.status = HTTPStatus.TOO_MANY_REQUESTS
            blocked.headers.append(("Retry-After", "900"))
            return blocked
        user = conn.execute(
            """
            SELECT users.*, roles.slug AS role_slug, roles.name AS role_name
            FROM users JOIN roles ON roles.id = users.role_id
            WHERE username = ? AND is_active = 1
            """,
            (username,),
        ).fetchone()
        if not user or not verify_password(password, user["password_hash"]):
            audit(conn, None, "login_failed", "user", username, "Credenciais inválidas", source_ip)
            return login_page("Usuário ou senha inválidos.")
        token = new_token()
        expires = (now_utc() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO sessions(token, user_id, expires_at) VALUES (?, ?, ?)", (token, user["id"], expires))
        audit(conn, user["id"], "login", "user", user["id"], "Login realizado", source_ip)
    if user["must_change_password"]:
        target = "/change-password"
    else:
        with connect() as conn:
            target = get_setting(conn, "home_page", "/")
        if target not in {"/", "/backups", "/equipment", "/reports"}:
            target = "/"
    response = redirect(target)
    response.headers.append(set_session_cookie(token))
    return response


def handle_logout(environ: dict) -> Response:
    token = cookie_token(environ)
    with connect() as conn:
        if token:
            row = conn.execute("SELECT user_id FROM sessions WHERE token = ?", (token,)).fetchone()
            if row:
                audit(conn, row["user_id"], "logout", "user", row["user_id"], "Logout realizado", environ.get("REMOTE_ADDR", ""))
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    response = redirect("/login")
    response.headers.append(clear_session_cookie())
    return response


AVATAR_ICONS = {
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>',
    "server": '<rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 7h.01M7 17h.01"/>',
    "network": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 4 6 4 9s-1 6-4 9c-3-3-4-6-4-9s1-6 4-9Z"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19 15a2 2 0 0 0 .4 2l-2.8 2.8a2 2 0 0 0-2-.4A2 2 0 0 0 13 21H9a2 2 0 0 0-1.6-1.6 2 2 0 0 0-2 .4L2.6 17A2 2 0 0 0 3 15.1 2 2 0 0 0 1 13V9a2 2 0 0 0 2-1.6 2 2 0 0 0-.4-2L5.4 2.6A2 2 0 0 0 7.3 3 2 2 0 0 0 9 1h4a2 2 0 0 0 1.6 2 2 2 0 0 0 2-.4l2.8 2.8a2 2 0 0 0-.4 2A2 2 0 0 0 21 9v4a2 2 0 0 0-2 2Z"/>',
}


def avatar_content(icon_name: str, initial: str) -> str:
    if icon_name == "initial" or icon_name not in AVATAR_ICONS:
        return e(initial)
    return f'<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{AVATAR_ICONS[icon_name]}</svg>'


def avatar_options(selected: str, initial: str) -> str:
    choices = [("initial", "Inicial"), ("user", "Usuário"), ("shield", "Escudo"),
               ("server", "Servidor"), ("network", "Rede"), ("settings", "Ajustes")]
    return "".join(
        f'<label class="avatar-choice"><input type="radio" name="avatar_icon" value="{name}"'
        f'{" checked" if name == selected else ""}><span class="avatar-choice-preview">'
        f'{avatar_content(name, initial)}</span><small>{label}</small></label>'
        for name, label in choices
    )


def change_password_page(user, error: str = "") -> Response:
    forced = bool(user["must_change_password"])
    content = f"""<section class="first-access-page" data-password-change>
      <header class="first-access-hero">
        <span class="first-access-hero-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M7 10V7a5 5 0 0 1 10 0v3"/><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M12 14v3"/></svg></span>
        <div><span class="eyebrow">{'Primeiro acesso' if forced else 'Segurança da conta'}</span><h1>{'Defina sua nova senha de acesso' if forced else 'Alterar senha de acesso'}</h1>
        <p>{'Por segurança, altere a senha provisória para continuar utilizando o sistema.' if forced else 'Confirme sua senha atual e defina uma nova senha para sua conta.'}</p></div>
      </header>
      <div class="first-access-grid">
        <form method="post" action="/change-password" class="panel first-access-form">
          <header><span class="first-access-small-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M8 11V8a4 4 0 0 1 8 0v3"/><rect x="6" y="11" width="12" height="10" rx="2"/><path d="M12 15v2"/></svg></span><div><h2>Atualize sua senha</h2><p>Preencha os campos abaixo com atenção para garantir sua segurança.</p></div></header>
          {message("error", error)}
          <div class="first-access-fields">
            <label>Senha atual<span class="password-input"><input name="current_password" type="password" autocomplete="current-password" required><button type="button" data-password-toggle aria-label="Mostrar senha"><svg viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></svg></button></span></label>
            <label>Nova senha<span class="password-input"><input name="new_password" type="password" minlength="10" autocomplete="new-password" required data-new-password><button type="button" data-password-toggle aria-label="Mostrar senha"><svg viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></svg></button></span></label>
            <div class="password-strength"><span>Força da senha: <strong data-password-strength>Digite a nova senha</strong></span><i><b data-password-strength-bar></b></i></div>
            <label>Confirmar nova senha<span class="password-input"><input name="confirm_password" type="password" minlength="10" autocomplete="new-password" required data-password-confirm><button type="button" data-password-toggle aria-label="Mostrar senha"><svg viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></svg></button></span></label>
          </div>
          <section class="password-requirements"><strong>Sua senha deve conter:</strong><div>
            <span data-password-rule="length"><i>✓</i>Mínimo de<br>10 caracteres</span>
            <span data-password-rule="uppercase"><i>✓</i>1 letra<br>maiúscula</span>
            <span data-password-rule="number"><i>✓</i>1 número</span>
            <span data-password-rule="special"><i>✓</i>1 caractere<br>especial</span>
          </div></section>
          <button class="first-access-submit" type="submit"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M8 11V8a4 4 0 0 1 8 0v3"/><rect x="6" y="11" width="12" height="10" rx="2"/></svg> Salvar nova senha</button>
          <div class="first-access-exit"><span>ou</span>{'<button type="submit" form="first-access-logout"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M10 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h5M14 8l4 4-4 4M8 12h10"/></svg>Sair e fazer login depois</button>' if forced else '<a href="/">Cancelar e voltar ao Dashboard</a>'}</div>
        </form>
        <aside class="panel first-access-guidance">
          <header><div class="security-illustration" aria-hidden="true"><span><svg viewBox="0 0 72 82"><defs><linearGradient id="security-shield-gradient" x1="10" y1="5" x2="62" y2="76"><stop stop-color="#18ad70"/><stop offset="1" stop-color="#007c49"/></linearGradient></defs><path class="security-shield" d="M36 3 65 15v22c0 20-12 34-29 42C19 71 7 57 7 37V15Z"/><path class="security-lock" d="M26 39v-7a10 10 0 0 1 20 0v7"/><rect class="security-lock" x="22" y="39" width="28" height="23" rx="5"/><path class="security-lock" d="M36 47v8"/></svg></span><i>••••••</i><b>✓</b></div><div><h2>Mantenha sua conta<br>sempre segura</h2><p>Siga as boas práticas ao criar sua senha para proteger seus dados e o ambiente.</p></div></header>
          <ul>
            <li><span><svg viewBox="0 0 24 24"><path d="M12 2 20 6v6c0 5-3 8-8 10-5-2-8-5-8-10V6Z"/><path d="m9 12 2 2 4-5"/></svg></span><div><strong>Boas práticas</strong><p>Crie senhas longas e complexas. Quanto maior e mais diversa, melhor.</p></div></li>
            <li><span><svg viewBox="0 0 24 24"><path d="M20 6v5h-5"/><path d="M4 18v-5h5"/><path d="M18.5 9A7.5 7.5 0 0 0 6 6.5L4 9"/><path d="M5.5 15A7.5 7.5 0 0 0 18 17.5l2-2.5"/></svg></span><div><strong>Não reutilize senhas</strong><p>Evite usar senhas já utilizadas em outros serviços ou sistemas.</p></div></li>
            <li><span><svg viewBox="0 0 24 24"><path d="M20.5 11.5c0 4.1-3.8 7.5-8.5 7.5-1.2 0-2.4-.2-3.4-.6L3 21l1.7-4.5A7 7 0 0 1 3.5 12C3.5 7.9 7.3 4.5 12 4.5s8.5 3 8.5 7Z"/></svg></span><div><strong>Use uma frase forte</strong><p>Combine palavras para criar uma frase fácil de lembrar e difícil de adivinhar.</p></div></li>
            <li><span><svg viewBox="0 0 24 24"><circle cx="12" cy="7" r="4"/><path d="M4 22v-2a8 8 0 0 1 16 0v2Z"/></svg></span><div><strong>Evite dados pessoais</strong><p>Não utilize informações como nome, data de nascimento ou CPF.</p></div></li>
          </ul>
          <div class="first-access-warning"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/></svg><span>Sua senha provisória será invalidada após a alteração.</span></div>
        </aside>
      </div>
      <form id="first-access-logout" method="post" action="/logout"></form>
    </section>"""
    return render("Troca de senha", content, user)


def handle_change_password(environ: dict, user) -> Response:
    form = parse_form(environ)
    current = form.get("current_password", "")
    new = form.get("new_password", "")
    confirm = form.get("confirm_password", "")
    if not verify_password(current, user["password_hash"]):
        return change_password_page(user, "Senha atual inválida.")
    if len(new) < 10:
        return change_password_page(user, "A nova senha deve ter pelo menos 10 caracteres.")
    if not re.search(r"[A-ZÀ-Ý]", new) or not re.search(r"\d", new) or not re.search(r"[^A-Za-zÀ-ÿ0-9\s]", new):
        return change_password_page(user, "Use ao menos uma letra maiúscula, um número e um caractere especial.")
    if new != confirm:
        return change_password_page(user, "Confirmação de senha diferente.")
    with connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (hash_password(new), user["id"]),
        )
        audit(conn, user["id"], "password_changed", "user", user["id"], "Senha alterada", environ.get("REMOTE_ADDR", ""))
        setup_done = setup_complete(conn)
    return redirect("/" if setup_done else "/setup")


def handle_profile_avatar(environ: dict) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    form = parse_form(environ)
    if not valid_session_csrf(environ, form.get("csrf_token", "")):
        return render("Requisição recusada", "<p>Token de segurança inválido.</p>", user, HTTPStatus.FORBIDDEN)
    icon_name = form.get("avatar_icon", "initial")
    if icon_name not in {"initial", *AVATAR_ICONS}:
        return render("Ícone inválido", "<p>Selecione um ícone de avatar válido.</p>", user, HTTPStatus.BAD_REQUEST)
    with connect() as conn:
        set_setting(conn, f"profile_avatar_icon_{user['id']}", icon_name)
        audit(conn, user["id"], "profile.avatar_updated", "user", user["id"], json.dumps({"icon": icon_name}), environ.get("REMOTE_ADDR", ""))
    return redirect(environ.get("HTTP_REFERER", "/") if environ.get("HTTP_REFERER", "").startswith("/") else "/")


def dashboard(environ: dict) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    with connect() as conn:
        if not setup_complete(conn):
            return redirect("/setup")
        snapshot = dashboard_snapshot(conn)
        install_name = get_setting(conn, "installation_name", "Backup Manager Local")
        provider = get_setting(conn, "provider_name", "")
    content = dashboard_page_content(
        snapshot=snapshot,
        user=user,
        install_name=install_name,
        provider=provider,
        local_dt=local_dt,
        relative_time=relative_time,
        format_size=format_size,
        quantity=quantity,
        short_identifier=short_identifier,
        can_operate=can_operate,
    )
    return render("Centro de Operações", content, user)


def setup_page(environ: dict, error: str = "") -> Response:
    user, response = require_admin(environ)
    if response:
        return response
    with connect() as conn:
        values = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}
        pops = conn.execute("SELECT * FROM pops ORDER BY name").fetchall()
        environments = conn.execute("SELECT * FROM environments ORDER BY is_primary DESC, name").fetchall()
    pop_rows = "".join(f"<li>{e(p['name'])} <span>{e(p['city'])} {e(p['state'])}</span></li>" for p in pops)
    env_rows = "".join(f"<li>{e(env['name'])}{' - principal' if env['is_primary'] else ''}</li>" for env in environments)
    content = f"""
    <section class="setup-modern-page">
    <header class="page-head setup-modern-head"><div><span class="eyebrow">Assistente</span><h1>Configuração inicial</h1><p>Defina a identidade, o armazenamento e a estrutura principal desta instalação.</p></div><span class="setup-modern-state">Etapa única</span></header>
    {message("error", error)}
    <form method="post" action="/setup" class="wizard setup-modern-form">
      <section class="panel setup-modern-card"><header><span>1</span><div><h2>Instalação</h2><p>Informações exibidas na interface e caminhos usados pelos serviços.</p></div></header><div class="setup-modern-fields">
        <label>Nome da instalação<input name="installation_name" value="{e(values.get('installation_name', ''))}" required></label>
        <label>Nome do provedor<input name="provider_name" value="{e(values.get('provider_name', ''))}" required></label>
        <label>Timezone<input name="timezone" value="{e(values.get('timezone', 'America/Sao_Paulo'))}" required></label>
        <label>Diretório principal de armazenamento<input name="storage_root" value="{e(values.get('storage_root', '/var/lib/backup-manager-local'))}" required></label>
        <label class="setup-modern-wide">URL pública HTTPS<input name="public_base_url" value="{e(values.get('public_base_url', ''))}" placeholder="https://backup.exemplo.com.br"></label></div>
      </section>
      <section class="panel setup-modern-card"><header><span>2</span><div><h2>Ambiente principal</h2><p>Organize os equipamentos a partir do ambiente padrão.</p></div></header><div class="setup-modern-fields">
        <label>Nome do ambiente<input name="primary_environment" value="{e(values.get('primary_environment', 'Producao'))}" required></label>
      </div></section>
      <section class="panel setup-modern-card"><header><span>3</span><div><h2>POPs / localidades</h2><p>Cadastre os locais que serão associados aos equipamentos.</p></div></header>
        <p class="muted">Informe uma localidade por linha no formato Nome, Cidade, UF.</p>
        <textarea name="pops" rows="5">{e(values.get('setup_pops_text', 'POP Principal, Sao Paulo, SP'))}</textarea>
      </section>
      <section class="panel setup-modern-card setup-modern-summary"><header><span>4</span><div><h2>Resumo cadastrado</h2><p>Estrutura atualmente registrada nesta instalação.</p></div></header>
        <div class="summary"><div><strong>Ambientes</strong><ul>{env_rows}</ul></div><div><strong>POPs</strong><ul>{pop_rows}</ul></div></div>
      </section>
      <footer class="setup-modern-actions"><span>Revise os dados antes de salvar.</span><button type="submit">Salvar configuração</button></footer>
    </form></section>
    """
    return render("Configurações", content, user)


def handle_setup(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response:
        return response
    form = parse_form(environ)
    required = ["installation_name", "provider_name", "timezone", "storage_root", "primary_environment"]
    if any(not form.get(field, "").strip() for field in required):
        return setup_page(environ, "Preencha todos os campos obrigatórios.")
    with connect() as conn:
        for key in required:
            set_setting(conn, key, form[key].strip())
        public_base_url=form.get("public_base_url","").strip().rstrip("/")
        set_setting(conn,"public_base_url",public_base_url)
        storage_root = Path(form["storage_root"].strip())
        set_setting(conn, "backup_directory", str(storage_root / "backups"))
        set_setting(conn, "trash_directory", str(storage_root / "trash"))
        set_setting(conn, "quarantine_directory", str(storage_root / "quarantine"))
        set_setting(conn, "temporary_directory", str(storage_root / "temporary"))
        set_setting(conn, "setup_pops_text", form.get("pops", "").strip())
        set_setting(conn, "setup_complete", "1")
        env_name = form["primary_environment"].strip()
        conn.execute("UPDATE environments SET is_primary = 0")
        conn.execute(
            """
            INSERT INTO environments(name, is_primary) VALUES (?, 1)
            ON CONFLICT(name) DO UPDATE SET is_primary = 1
            """,
            (env_name,),
        )
        for line in form.get("pops", "").splitlines():
            parts = [part.strip() for part in line.split(",")]
            if not parts or not parts[0]:
                continue
            city = parts[1] if len(parts) > 1 else ""
            state = parts[2] if len(parts) > 2 else ""
            conn.execute(
                """
                INSERT INTO pops(name, city, state) VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET city = excluded.city, state = excluded.state
                """,
                (parts[0], city, state),
            )
        audit(conn, user["id"], "setup_saved", "settings", "", json.dumps({key: form[key].strip() for key in required}), environ.get("REMOTE_ADDR", ""))
    return redirect("/")


SETTINGS_TABS = (
    ("general", "Geral"), ("users", "Usuários"), ("access", "Acesso e HTTPS"),
    ("backup", "Backup e Retenção"), ("drive", "rclone"),
    ("notifications", "Notificações"), ("updates", "Atualizações"),
    ("system", "Sistema"),
)


def settings_tabs(current: str) -> str:
    icons = {
        "general": '<path d="M4 6h16M7 12h10M9 18h6"/>',
        "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.8M16 3.2a4 4 0 0 1 0 7.6"/>',
        "access": '<rect x="3" y="11" width="18" height="10" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
        "backup": '<path d="M21 8v13H3V8M1 3h22v5H1zM10 12h4"/>',
        "drive": '<path d="M7 18h11a4 4 0 0 0 .5-8 7 7 0 0 0-13-1A4.5 4.5 0 0 0 7 18Z"/>',
        "notifications": '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
        "updates": '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
        "system": '<rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 7h.01M7 17h.01"/>',
        "configuration": '<path d="M6 2h9l4 4v16H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z"/><path d="M14 2v5h5M9 13h6M9 17h6"/>',
    }
    return '<nav class="settings-tabs" aria-label="Seções de configurações">' + "".join(
        f'<a href="/settings?tab={key}" class="settings-tab {"active" if key == current else ""}"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{icons[key]}</svg><span>{e(label)}</span></a>'
        for key, label in SETTINGS_TABS
    ) + "</nav>"


def settings_page(environ: dict, error: str = "", success: str = "") -> Response:
    user, response = require_admin(environ)
    if response:
        return response
    query = parse_qs(environ.get("QUERY_STRING", ""))
    tab = query.get("tab", ["general"])[-1]
    if tab not in {key for key, _ in SETTINGS_TABS}:
        tab = "general"
    with connect() as conn:
        ensure_roles(conn)
        values = {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM settings")}
        body = settings_tab_content_view(conn, tab, values, local_dt=local_dt, csrf_token=e(session_csrf(environ)), version=__version__, build_id=__build_id__)
    notice_kind = query.get("notice", [""])[-1]
    notice_text = query.get("message", [""])[-1]
    notices = message("error", error) + message("success", success)
    if notice_kind in {"error", "success", "info"} and notice_text:
        notices += message(notice_kind, notice_text[:500])
    modern_tabs = {"general", "users", "access", "backup", "drive", "notifications", "updates"}
    content_class = f"settings-content settings-modern-content settings-view-{tab}" if tab in modern_tabs else "settings-content"
    content = (f'<header class="page-head settings-page-head"><div><span class="eyebrow">Administração</span><h1>Configurações</h1>'
               f'<p>Gerencie o sistema, segurança, armazenamento e integrações.</p></div></header>'
               f'{settings_tabs(tab)}{notices}<section class="{content_class}">{body}</section>')
    return render("Configurações", content, user)


def _valid_settings_csrf(environ: dict, value: str) -> bool:
    return valid_session_csrf(environ, value)


def _settings_response(environ: dict, tab: str, kind: str, text: str) -> Response:
    return redirect_with_message(f"/settings?tab={tab}", kind, text)


def handle_settings_general(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response:
        return response
    try:
        fields, files = parse_multipart(environ)
        if not _valid_settings_csrf(environ, fields.get("csrf_token", "")):
            return render("Requisição recusada", "<p>Token de segurança inválido.</p>", user, HTTPStatus.FORBIDDEN)
        values = validate_general(fields)
        with connect() as conn:
            for key, value in values.items():
                set_setting(conn, key, value)
            for field, kind in (("logo", "logo"), ("favicon", "favicon")):
                uploaded = files.get(field)
                if uploaded and uploaded.filename:
                    path = save_branding(DATA_DIR, uploaded.filename, uploaded.file.read(), kind)
                    set_setting(conn, f"branding_{kind}", path)
            audit(conn, user["id"], "settings.general_updated", "settings", "general", json.dumps({"keys": sorted(values)}), environ.get("REMOTE_ADDR", ""))
    except ValueError as exc:
        return _settings_response(environ, "general", "error", str(exc))
    return _settings_response(environ, "general", "success", "Configurações gerais salvas.")


def handle_settings_users(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response:
        return response
    form = parse_form(environ)
    if not _valid_settings_csrf(environ, form.get("csrf_token", "")):
        return render("Requisição recusada", "<p>Token de segurança inválido.</p>", user, HTTPStatus.FORBIDDEN)
    action = form.get("action", "")
    role = form.get("role", "")
    password = form.get("new_password", "")
    if role not in {"admin", "operator", "read_download"}:
        return _settings_response(environ, "users", "error", "Perfil inválido.")
    minimum_password_length = 6 if action == "create" else 10
    if password and len(password) < minimum_password_length:
        return _settings_response(environ, "users", "error", f"A senha deve ter pelo menos {minimum_password_length} caracteres.")
    with connect() as conn:
        ensure_roles(conn)
        role_id = conn.execute("SELECT id FROM roles WHERE slug=?", (role,)).fetchone()["id"]
        if action == "create":
            username = form.get("username", "").strip()
            full_name = form.get("full_name", "").strip()
            if not re.fullmatch(r"[A-Za-z0-9._-]{3,40}", username) or not full_name or not password:
                return _settings_response(environ, "users", "error", "Preencha os dados do novo usuário corretamente.")
            try:
                conn.execute("INSERT INTO users(username,full_name,password_hash,role_id,must_change_password) VALUES(?,?,?,?,1)",
                             (username, full_name[:120], hash_password(password), role_id))
            except Exception:
                return _settings_response(environ, "users", "error", "Não foi possível criar o usuário; verifique se o nome já existe.")
            audit(conn, user["id"], "user.created", "user", username, json.dumps({"role": role}), environ.get("REMOTE_ADDR", ""))
        elif action == "update":
            user_id = int(form.get("user_id") or 0)
            target = conn.execute("SELECT id,username FROM users WHERE id=?", (user_id,)).fetchone()
            if not target:
                return _settings_response(environ, "users", "error", "Usuário não encontrado.")
            active = 1 if form.get("is_active") == "1" else 0
            if user_id == user["id"] and (not active or role != "admin"):
                return _settings_response(environ, "users", "error", "O administrador atual não pode remover o próprio acesso.")
            conn.execute("UPDATE users SET full_name=?,role_id=?,is_active=? WHERE id=?",
                         (form.get("full_name", "").strip()[:120] or target["username"], role_id, active, user_id))
            if password:
                conn.execute("UPDATE users SET password_hash=?,must_change_password=1 WHERE id=?", (hash_password(password), user_id))
                conn.execute("DELETE FROM sessions WHERE user_id=? AND token!=?", (user_id, cookie_token(environ) or ""))
            audit(conn, user["id"], "user.updated", "user", user_id, json.dumps({"role": role, "active": active, "password_reset": bool(password)}), environ.get("REMOTE_ADDR", ""))
        else:
            return _settings_response(environ, "users", "error", "Ação de usuário inválida.")
    return _settings_response(environ, "users", "success", "Usuário atualizado com segurança.")


def handle_settings_https(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response:
        return response
    form = parse_form(environ)
    if not _valid_settings_csrf(environ, form.get("csrf_token", "")):
        return render("Requisição recusada", "<p>Token de segurança inválido.</p>", user, HTTPStatus.FORBIDDEN)
    action, domain, email = form.get("action", ""), form.get("domain", ""), form.get("email", "")
    try:
        if action == "test":
            result = https_checks(domain)
            checks = {
                "DNS": result["resolves"], "porta 80": result["port_80"], "porta 443": result["port_443"],
                "certificado": result["certificate_valid"], "servidor HTTPS": result["nginx_configured"],
            }
            with connect() as conn:
                audit(conn, user["id"], "https.tested", "settings", result["domain"],
                      json.dumps({key: result[key] for key in ("resolves", "port_80", "port_443", "certificate_valid", "nginx_configured")}),
                      environ.get("REMOTE_ADDR", ""))
            failures = [label for label, ok in checks.items() if not ok]
            if not failures:
                return _settings_response(environ, "access", "success", "Teste concluído. O domínio e o HTTPS estão funcionando corretamente. Nenhuma alteração foi salva.")
            return _settings_response(environ, "access", "info", "Teste concluído. Verifique: " + ", ".join(failures) + ". Nenhuma alteração foi salva.")
        if action == "validate":
            email = validate_email(email)
            result = https_checks(domain)
            ok = bool(result["resolves"] and result["port_80"])
            with connect() as conn:
                set_setting(conn, "https_last_check", json.dumps({key: result[key] for key in ("resolves", "port_80", "port_443", "certificate_valid", "nginx_configured")}))
                set_setting(conn, "https_status", "ready" if ok else "attention")
                if ok:
                    set_setting(conn, "https_domain", result["domain"])
                    set_setting(conn, "https_email", email.strip())
                audit(conn, user["id"], "https.validated", "settings", result["domain"], json.dumps({"ready": ok}), environ.get("REMOTE_ADDR", ""))
            return _settings_response(environ, "access", "success" if ok else "info", "Alterações salvas com sucesso." if ok else "O domínio não foi salvo. Revise o DNS e a porta 80 antes de tentar novamente.")
        ok, detail = run_https_helper(action, domain, email, confirmed=form.get("confirm") == "1")
        with connect() as conn:
            set_setting(conn, "https_status", "active" if ok else "failed")
            if ok:
                set_setting(conn, "https_domain", domain.strip().lower())
                set_setting(conn, "https_email", email.strip())
                set_setting(conn, "public_base_url", f"https://{domain.strip().lower()}")
                set_setting(conn, "https_auto_renew", "Ativa")
                if action == "renew" and detail.startswith("Certificado renovado"):
                    set_setting(conn, "https_last_renewal", now_utc().isoformat())
            audit(conn, user["id"], f"https.{action}", "settings", domain, json.dumps({"success": ok}), environ.get("REMOTE_ADDR", ""))
        return _settings_response(environ, "access", "success" if ok else "error", detail)
    except ValueError as exc:
        return _settings_response(environ, "access", "error", str(exc))


def settings_configuration_export(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response:
        return response
    with connect() as conn:
        payload = export_configuration(conn)
        audit(conn, user["id"], "configuration.exported", "settings", "configuration", json.dumps({"bytes": len(payload)}), environ.get("REMOTE_ADDR", ""))
    filename = f'backup-manager-config-{now_utc().strftime("%Y%m%d-%H%M%S")}.json'
    return Response(payload, HTTPStatus.OK, [("Content-Type", "application/json; charset=utf-8"),
        ("Content-Disposition", f'attachment; filename="{filename}"'), ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff")])


def settings_configuration_import(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response:
        return response
    try:
        fields, files = parse_multipart(environ)
        if not _valid_settings_csrf(environ, fields.get("csrf_token", "")):
            return render("Requisição recusada", "<p>Token de segurança inválido.</p>", user, HTTPStatus.FORBIDDEN)
        if fields.get("confirmation", "").strip() != "IMPORTAR CONFIGURACAO":
            raise ValueError("Digite IMPORTAR CONFIGURACAO para confirmar a migração.")
        uploaded = files.get("configuration")
        if not uploaded or not uploaded.filename.lower().endswith(".json"):
            raise ValueError("Selecione um arquivo JSON de configuração.")
        payload = uploaded.file.read(2 * 1024 * 1024 + 1)
        with connect() as conn:
            counts = import_configuration(conn, payload)
            audit(conn, user["id"], "configuration.imported", "settings", "configuration", json.dumps(counts, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
    except ValueError as exc:
        return _settings_response(environ, "configuration", "error", str(exc))
    return _settings_response(environ, "configuration", "success", "Configuração importada. Revise equipamentos e configure novamente credenciais, tokens e certificados.")


def branding_file(kind: str) -> Response:
    if kind not in {"logo", "favicon"}:
        return Response("Not found", HTTPStatus.NOT_FOUND)
    with connect() as conn:
        configured = get_setting(conn, f"branding_{kind}", "")
    target = Path(configured).resolve() if configured else None
    root = (DATA_DIR / "branding").resolve()
    if not target or not str(target).startswith(str(root)) or not target.is_file():
        return Response("Not found", HTTPStatus.NOT_FOUND)
    return Response(target.read_bytes(), HTTPStatus.OK, [("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream"),
        ("Cache-Control", "public, max-age=3600"), ("X-Content-Type-Options", "nosniff")])


def option_rows(rows, selected_id: str = "") -> str:
    return "".join(
        f'<option value="{row["id"]}" {"selected" if str(row["id"]) == selected_id else ""}>{e(row["name"])}</option>'
        for row in rows
    )


def valid_address(value: str) -> bool:
    return bool(IP_RE.match(value) or ADDRESS_RE.match(value))


def driver_options(selected: str = "") -> str:
    current = selected if selected in SSH_DRIVERS else "generic_ssh"
    groups: list[str] = []
    if selected in LEGACY_DRIVER_LABELS:
        groups.append(
            f'<optgroup label="Método legado — revisão necessária"><option value="{e(selected)}" selected>'
            f'{e(LEGACY_DRIVER_LABELS[selected])}</option></optgroup>'
        )
        current = selected
    for group, keys in SSH_DRIVER_GROUPS:
        options = "".join(
            f'<option value="{key}" {"selected" if current == key else ""}>'
            f'{"Telnet/FTP" if key == "fiberhome_olt_telnet_ftp" else "Telnet" if key == "vsol_olt_telnet_cli" else "SSH"} — {e(SSH_DRIVER_LABELS[key])}</option>'
            for key in keys if key in SSH_DRIVERS
        )
        if options:
            groups.append(f'<optgroup label="{e(group)}">{options}</optgroup>')
    return "".join(groups)


def parse_port(value: str) -> int:
    try:
        port = int(value or "22")
    except ValueError:
        raise ValueError("Porta SSH inválida.")
    if port < 1 or port > 65535:
        raise ValueError("Porta SSH inválida.")
    return port


def parse_active(value: str | None) -> int:
    return 1 if value in ("1", "on", "true", "yes") else 0


def ssh_password_strength_error(password: str) -> str:
    if len(password) < 5:
        return "A senha SSH deve ter pelo menos 5 caracteres. Recomendamos 10 ou mais."
    return ""


def validate_equipment_form(form: dict[str, str]) -> tuple[dict[str, object], str]:
    hostname = form.get("hostname", "").strip()
    ip_address = form.get("ip_address", "").strip()
    name = hostname
    driver = form.get("ssh_backup_driver", "generic_ssh").strip() or "generic_ssh"
    if not HOST_RE.match(hostname):
        return {}, "Hostname inválido."
    try:
        if ipaddress.ip_address(ip_address).version != 4:
            raise ValueError
    except ValueError:
        return {}, "Informe um endereço IPv4 válido, por exemplo 192.168.0.10."
    if driver not in SSH_DRIVERS:
        return {}, "Driver SSH inválido."
    try:
        ssh_port = parse_port(form.get("ssh_port", "22"))
    except ValueError as exc:
        return {}, str(exc)
    try:
        vendor_id = int(form.get("vendor_id") or 0) or None
    except ValueError:
        return {}, "Fabricante inválido."
    return {
        "name": name[:120],
        "hostname": hostname,
        "ip_address": ip_address,
        "vendor_id": vendor_id,
        "group_id": int(form.get("group_id") or 0) or None,
        "pop_id": int(form.get("pop_id") or 0) or None,
        "environment_id": int(form.get("environment_id") or 0) or None,
        "is_active": parse_active(form.get("is_active")) if form.get("preserve_equipment_state") == "1" else 1,
        "ssh_backup_driver": driver,
        "ssh_port": ssh_port,
        "ssh_custom_command": form.get("ssh_custom_command", "").strip()[:200] if form.get("preserve_custom_command") == "1" else "",
        "notes": form.get("notes", "").strip()[:500],
    }, ""


def validate_vendor_driver(conn, vendor_id: int | None, driver: str) -> tuple[object | None, str]:
    if vendor_id is None:
        if driver != "generic_ssh":
            return None, "Selecione o fabricante compatível com o método de conexão."
        return None, ""
    vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
    if not vendor:
        return None, "Fabricante inválido."
    return vendor, compatibility_error(vendor["name"], driver)


def equipment_form(action: str, vendors, groups, pops, environments, item=None) -> str:
    selected = lambda key, default="": str(item[key] if item and item[key] is not None else default)
    title = "Editar equipamento" if item else "Novo equipamento"
    submit = "Salvar equipamento" if item else "Cadastrar equipamento"
    return f"""
    <form method="post" action="{action}" class="panel">
      <h2>{title}</h2>
      <label>Nome do dispositivo (hostname)<input name="hostname" value="{e(selected('hostname'))}" placeholder="exemplo: roteador-core-01" required></label>
      <label>Endereço IPv4<input name="ip_address" value="{e(selected('ip_address'))}" placeholder="192.168.0.10" inputmode="decimal" required></label>
      <label>Ambiente<select name="environment_id">{option_rows(environments, selected('environment_id'))}</select></label>
      <label>Grupo<select name="group_id">{option_rows(groups, selected('group_id'))}</select></label>
      <label>Fabricante<select name="vendor_id">{option_rows(vendors, selected('vendor_id'))}</select></label>
      <label>POP/localidade<select name="pop_id">{option_rows(pops, selected('pop_id'))}</select></label>
      <label>Método de conexão<select name="ssh_backup_driver" data-connection-method required>{driver_options(selected('ssh_backup_driver'))}</select></label>
      <label>Porta de acesso padrão<input name="ssh_port" data-access-port value="{e(selected('ssh_port', '22'))}" inputmode="numeric"></label>
      {f'<input type="hidden" name="is_active" value="{e(selected("is_active", "1"))}"><input type="hidden" name="preserve_equipment_state" value="1"><input type="hidden" name="ssh_custom_command" value="{e(selected("ssh_custom_command"))}"><input type="hidden" name="preserve_custom_command" value="1">' if item else ''}
      <label>Observações<textarea name="notes" rows="3">{e(selected('notes'))}</textarea></label>
      <button type="submit">{submit}</button>
    </form>
    """


def equipment_page(environ: dict, error: str = "") -> Response:
    user, response = require_user(environ)
    if response:
        return response
    with connect() as conn:
        context = equipment_page_context(conn)
    query = parse_qs(environ.get("QUERY_STRING", ""))
    content = equipment_page_content(
        user=user,
        error=error,
        equipment=context["equipment"],
        vendors=context["vendors"],
        groups=context["groups"],
        pops=context["pops"],
        environments=context["environments"],
        dependency_map=context["dependency_map"],
        equipment_form=equipment_form,
        message=message,
        dependency_summary=dependency_summary,
        can_operate=can_operate,
        is_mikrotik_equipment=is_strict_mikrotik,
        new_mode=bool(query.get("new")),
        driver_options=driver_options,
    )
    return render("Equipamentos", content, user)


def handle_equipment_create(environ: dict) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    form = parse_form(environ)
    values, error = validate_equipment_form(form)
    if error:
        return equipment_page(environ, error)
    ssh_username = form.get("ssh_username", "").strip()
    ssh_password = form.get("ssh_password", "")
    if ssh_username or ssh_password:
        if not ssh_username or not ssh_password:
            return equipment_page(environ, "Informe o usuário e a senha SSH.")
        if ssh_password != form.get("ssh_confirm_password", ""):
            return equipment_page(environ, "A confirmação da senha SSH é diferente.")
        password_error = ssh_password_strength_error(ssh_password)
        if password_error:
            return equipment_page(environ, password_error)
    with connect() as conn:
        _, compatibility = validate_vendor_driver(conn, values["vendor_id"], values["ssh_backup_driver"])
        if compatibility:
            return equipment_page(environ, compatibility)
        try:
            cur = conn.execute(
                """
                INSERT INTO equipment(name, hostname, ip_address, vendor_id, group_id, pop_id, environment_id,
                                      is_active, ssh_backup_driver, ssh_port, ssh_custom_command, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["name"],
                    values["hostname"],
                    values["ip_address"],
                    values["vendor_id"],
                    values["group_id"],
                    values["pop_id"],
                    values["environment_id"],
                    values["is_active"],
                    values["ssh_backup_driver"],
                    values["ssh_port"],
                    values["ssh_custom_command"],
                    values["notes"],
                ),
            )
            if ssh_username:
                create_credential(
                    conn, cur.lastrowid, user["id"],
                    form.get("credential_name", "SSH principal"),
                    ssh_username, ssh_password, values["ssh_port"],
                    "Criada durante o cadastro do equipamento.",
                )
        except Exception:
            return equipment_page(environ, "Não foi possível cadastrar o equipamento. Verifique se o hostname já existe.")
        audit(conn, user["id"], "created", "equipment", cur.lastrowid, json.dumps({"hostname": values["hostname"], "driver": values["ssh_backup_driver"]}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
        persisted = conn.execute(
            """SELECT equipment.*,vendors.name AS vendor FROM equipment
               LEFT JOIN vendors ON vendors.id=equipment.vendor_id WHERE equipment.id=?""",
            (cur.lastrowid,),
        ).fetchone()
    equipment_id = cur.lastrowid
    driver = persisted["ssh_backup_driver"] or "generic_ssh"
    vendor = persisted["vendor"] or ""
    if is_strict_mikrotik(persisted, vendor):
        mikrotik_mode = form.get("mikrotik_backup_mode", "ftp")
        if mikrotik_mode not in {"ftp", "ssh"}:
            mikrotik_mode = "ftp"
        next_title = "Continuar configuração do MikroTik"
        next_description = ({
            "ftp": "Após validar o SSH, configure a conta FTP Push e instale o script no RouterOS.",
            "ssh": "Após validar o SSH, configure o agendamento do Backup Manager.",
        }[mikrotik_mode])
        next_action = f'''<form method="post" action="/equipment/{equipment_id}/ssh-test?onboarding={mikrotik_mode}">
          <button type="submit">Validar SSH e continuar →</button></form>'''
        note = "Você também poderá concluir a configuração pela página do equipamento."
    elif is_ftp_push_olt_driver(driver):
        next_title = f"Continuar configuração da {vendor or 'OLT'}"
        next_description = "Configure o acesso e o recebimento FTP compatíveis com o driver selecionado."
        next_action = f'<a class="button" href="/equipment/{equipment_id}#backup">Configurar backup da OLT →</a>'
        note = "Nenhuma automação será executada antes da configuração e validação do acesso."
    else:
        next_title = f"Continuar configuração {('do equipamento ' + vendor) if vendor else 'do equipamento'}"
        next_description = "Valide o acesso SSH e configure o agendamento do Backup Manager."
        next_action = f'''<form method="post" action="/equipment/{equipment_id}/ssh-test?onboarding=ssh">
          <button type="submit">Validar SSH e continuar →</button></form>'''
        note = "O backup será executado pelo driver selecionado, sem automações de outro fabricante."
    content = f'''<section class="equipment-created-page">
      <div class="equipment-created-hero"><span class="equipment-created-check" aria-hidden="true">✓</span><span class="eyebrow">Equipamento cadastrado</span>
      <h1>{e(values['hostname'])} foi cadastrado</h1><p>O equipamento já está disponível para gerenciamento e configuração de backups.</p></div>
      <article class="panel equipment-created-card"><div class="equipment-created-heading"><span aria-hidden="true">⇅</span><div><h2>{e(next_title)}</h2><p>{e(next_description)}</p></div></div>
      <dl class="equipment-created-summary"><div><dt>Equipamento</dt><dd>{e(values['hostname'])}</dd></div><div><dt>IP / Host</dt><dd>{e(values['ip_address'])}</dd></div><div><dt>Porta</dt><dd>{e(values['ssh_port'])}</dd></div><div><dt>Status</dt><dd><span class="badge equipment-online">Ativo</span></dd></div></dl>
      <div class="equipment-created-actions"><a class="button secondary" href="/equipment">Ir para equipamentos</a>{next_action}</div></article>
      <p class="equipment-created-note">{e(note)}</p>
    </section>'''
    return render("Próximo passo", content, user, HTTPStatus.CREATED)


def dependency_summary(dependencies: dict[str, object]) -> str:
    counts = dependencies["counts"]
    rows = "".join(f"<li>{count} {e(EQUIPMENT_DEPENDENCY_LABELS[key])}</li>" for key, count in counts.items() if count)
    return f'<ul class="dependency-summary">{rows}</ul>' if rows else '<p class="muted">Nenhuma dependência encontrada.</p>'


def handle_equipment_delete(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response:
        return response
    form = parse_form(environ)
    item_id = form.get("id", "")
    with connect() as conn:
        row = conn.execute("SELECT * FROM equipment WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return equipment_page(environ, "Equipamento não encontrado.")
        dependencies = equipment_dependencies(conn, row["id"])
        if form.get("confirm") != "1" or form.get("confirm_name", "").strip() != row["hostname"]:
            audit(conn, user["id"], "equipment.delete_blocked", "equipment", row["id"],
                  json.dumps({"reason": "confirmation_missing"}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
            conn.commit()
            return equipment_page(environ, "Confirme explicitamente a remoção do equipamento.")
        if dependencies["total"]:
            reason = "active_ftp_push" if dependencies["active_integrations"] else "dependencies"
            audit(conn, user["id"], "equipment.delete_blocked", "equipment", row["id"],
                  json.dumps({"reason": reason, "counts": dependencies["counts"]}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
            text = ("Existe uma integração FTP Push ativa. Desative-a antes de remover este equipamento." if reason == "active_ftp_push" else
                    "Este equipamento não pode ser removido fisicamente porque possui dependências históricas preservadas, listadas abaixo.")
            conn.commit()
            return equipment_page(environ, text)
        try:
            conn.execute("DELETE FROM equipment WHERE id = ?", (row["id"],))
            audit(conn, user["id"], "equipment.deleted", "equipment", row["id"],
                  json.dumps({"name": row["name"] or row["hostname"], "hostname": row["hostname"]}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
        except Exception as exc:
            occurrence = uuid.uuid4().hex[:12]
            error_code = "integrity_error" if isinstance(exc, sqlite3.IntegrityError) else "unexpected_error"
            logger.exception("Falha ao remover equipamento id=%s ocorrência=%s", row["id"], occurrence)
            conn.rollback()
            try:
                with connect() as audit_conn:
                    audit(audit_conn, user["id"], "equipment.delete_failed", "equipment", row["id"],
                          json.dumps({"occurrence": occurrence, "error_code": error_code}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
            except Exception:
                logger.exception("Falha ao auditar remoção de equipamento ocorrência=%s", occurrence)
            return equipment_page(environ, f"Não foi possível remover o equipamento. Ocorrência {occurrence}.")
    return redirect_with_message("/equipment", "success", "Equipamento removido com sucesso.")


def handle_equipment_deactivate(environ: dict, equipment_id: int) -> Response:
    user, response = require_operator(environ)
    if response: return response
    with connect() as conn:
        row = conn.execute("SELECT * FROM equipment WHERE id=?", (equipment_id,)).fetchone()
        if not row:
            return redirect_with_message("/equipment", "error", "Equipamento não encontrado.")
        conn.execute("UPDATE equipment SET is_active=0 WHERE id=?", (equipment_id,))
        audit(conn, user["id"], "equipment.deactivated", "equipment", equipment_id,
              json.dumps({"hostname": row["hostname"]}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
    return redirect_with_message("/equipment", "success", "Equipamento desativado. O histórico foi preservado.")


def handle_equipment_archive(environ: dict, equipment_id: int) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ)
    with connect() as conn:
        equipment = conn.execute("SELECT hostname FROM equipment WHERE id=?", (equipment_id,)).fetchone()
        if not equipment:
            return redirect_with_message("/equipment", "error", "Equipamento não encontrado.")
        if form.get("confirm_name", "").strip() != equipment["hostname"]:
            return redirect_with_message("/equipment", "error", "Digite o hostname para confirmar o arquivamento.")
        try:
            result = archive_equipment(conn, equipment_id, file_action=form.get("file_action", "preserve"),
                                       user_id=user["id"], ip_address=environ.get("REMOTE_ADDR", ""))
        except (OSError, ValueError) as exc:
            return redirect_with_message("/equipment", "error", str(exc))
    message_text = (f"Equipamento arquivado; {result['moved_backups']} backups foram movidos para a lixeira."
                    if result["file_action"] == "trash" else
                    "Equipamento arquivado; backups e histórico foram preservados.")
    return redirect_with_message("/equipment", "success", message_text)


def handle_equipment_purge(environ: dict, equipment_id: int) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ)
    with connect() as conn:
        equipment = conn.execute("SELECT hostname FROM equipment WHERE id=?", (equipment_id,)).fetchone()
        if not equipment:
            return redirect_with_message("/equipment", "error", "Equipamento não encontrado.")
        if form.get("confirm_name", "").strip() != equipment["hostname"] or form.get("confirmation", "").strip() != "EXCLUIR TUDO":
            return redirect_with_message("/equipment", "error", "Confirmação da exclusão definitiva inválida.")
        account_ids = [row[0] for row in conn.execute(
            "SELECT id FROM ftp_accounts WHERE equipment_id=? AND deleted_at IS NULL", (equipment_id,)
        )]
    provisioning = FTPProvisioningService(connect_factory=connect, helper=run_helper)
    for account_id in account_ids:
        disabled = provisioning.disable_account(account_id, delete=True, user_id=user["id"],
                                                  ip_address=environ.get("REMOTE_ADDR", ""))
        if not disabled.ok:
            return redirect_with_message(f"/equipment/{equipment_id}", "error",
                                         "A conta FTP não pôde ser removida do servidor; a exclusão definitiva foi cancelada.")
    with connect() as conn:
        try:
            report = purge_equipment(conn, equipment_id)
        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
            return redirect_with_message("/equipment", "error", str(exc))
    with connect() as audit_conn:
        audit(audit_conn, user["id"], "equipment.purged", "equipment", equipment_id,
              json.dumps({"hostname": report["hostname"], "counts": report["counts"], "files": report["files"],
                          "file_failures": len(report["file_failures"])}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
    suffix = f" {len(report['file_failures'])} arquivo(s) não puderam ser removidos do disco." if report["file_failures"] else ""
    return redirect_with_message("/equipment", "success", f"Equipamento e todos os vínculos foram removidos definitivamente.{suffix}")


def equipment_edit_page(environ: dict, equipment_id: int, error: str = "", success: str = "") -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    with connect() as conn:
        item = conn.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
        if not item:
            return render("Equipamento não encontrado", "<p>Equipamento não encontrado.</p>", user, HTTPStatus.NOT_FOUND)
        vendors = conn.execute("SELECT * FROM vendors ORDER BY name").fetchall()
        groups = conn.execute("SELECT * FROM equipment_groups ORDER BY name").fetchall()
        pops = conn.execute("SELECT * FROM pops ORDER BY name").fetchall()
        environments = conn.execute("SELECT * FROM environments ORDER BY name").fetchall()
    def selected(key: str, fallback: str = "") -> str:
        value = item[key]
        return fallback if value is None else str(value)

    content = f"""
    <div class="equipment-edit-page" data-equipment-edit>
      <nav class="breadcrumbs ftp-wizard-breadcrumbs"><a href="/">Início</a><span>/</span><a href="/equipment">Equipamentos</a><span>/</span><strong>Editar equipamento</strong></nav>
      <header class="ftp-wizard-head"><h1>Editar equipamento</h1><p>Altere os dados do equipamento e salve as modificações.</p></header>
      {message("error", error)}{message("success", success)}
      <form method="post" action="/equipment/{equipment_id}/edit" class="equipment-edit-form">
        <section class="panel ftp-wizard-panel equipment-edit-panel"><h2>▣ &nbsp; Informações do equipamento</h2><div class="equipment-form-grid">
          <label>Nome do equipamento <em>*</em><input name="hostname" value="{e(selected('hostname'))}" required><small>Nome amigável para identificação</small></label>
          <label>Hostname / IP <em>*</em><input name="ip_address" value="{e(selected('ip_address'))}" inputmode="decimal" required><small>IP ou hostname para conexão</small></label>
          <label>Ambiente<select name="environment_id">{option_rows(environments, selected('environment_id'))}</select><small>Ambiente operacional</small></label>
          <label>Fabricante / Vendor<select name="vendor_id">{option_rows(vendors, selected('vendor_id'))}</select><small>Fabricante do equipamento</small></label>
          <label>Grupo / Categoria<select name="group_id">{option_rows(groups, selected('group_id'))}</select><small>Grupo ou categoria do equipamento</small></label>
          <label>Site / Localidade<select name="pop_id">{option_rows(pops, selected('pop_id'))}</select><small>Local onde o equipamento está instalado</small></label>
          <label>Método de backup <em>*</em><select name="ssh_backup_driver" data-connection-method required>{driver_options(selected('ssh_backup_driver'))}</select><small>Driver usado para realizar o backup</small></label>
          <label>Porta de acesso<input name="ssh_port" data-access-port value="{e(selected('ssh_port', '22'))}" inputmode="numeric"><small>Porta SSH, Telnet ou do método selecionado</small></label>
          <label>Status do equipamento<select name="is_active"><option value="1" {'selected' if item['is_active'] else ''}>Ativo</option><option value="0" {'selected' if not item['is_active'] else ''}>Inativo</option></select><small>Equipamentos inativos não executam testes ou backups.</small></label>
          <label class="ftp-wide">Observações<textarea name="notes" rows="3">{e(selected('notes'))}</textarea><small>Informações adicionais sobre o equipamento</small></label>
          <input type="hidden" name="preserve_equipment_state" value="1"><input type="hidden" name="ssh_custom_command" value="{e(selected('ssh_custom_command'))}"><input type="hidden" name="preserve_custom_command" value="1">
        </div></section>
        <aside class="ftp-wizard-aside"><section class="panel"><h2>Resumo do equipamento</h2><dl><dt>Nome</dt><dd data-edit-summary="name">{e(selected('hostname'))}</dd><dt>IP / Host</dt><dd data-edit-summary="ip">{e(selected('ip_address'))}</dd><dt>Ambiente</dt><dd data-edit-summary="environment">{e(next((r['name'] for r in environments if str(r['id']) == selected('environment_id')), '-'))}</dd><dt>Fabricante</dt><dd data-edit-summary="vendor">{e(next((r['name'] for r in vendors if str(r['id']) == selected('vendor_id')), '-'))}</dd><dt>Método</dt><dd data-edit-summary="method">{e(selected('ssh_backup_driver'))}</dd><dt>Porta</dt><dd data-edit-summary="port">{e(selected('ssh_port', '22'))}</dd><dt>Status</dt><dd><span class="badge {'equipment-online' if item['is_active'] else 'equipment-offline'}">{'Ativo' if item['is_active'] else 'Inativo'}</span></dd></dl></section><section class="ftp-security-note"><span>♙</span><div><strong>Alteração segura</strong><p>Credenciais, backups e vínculos existentes serão preservados.</p></div></section></aside>
        <footer class="equipment-edit-actions"><a class="button secondary" href="/equipment/{equipment_id}">Cancelar</a><button type="submit">Salvar alterações</button></footer>
      </form>
    </div>
    """
    return render("Editar equipamento", content, user)


def handle_equipment_edit(environ: dict, equipment_id: int) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    form = parse_form(environ)
    values, error = validate_equipment_form(form)
    if error:
        return equipment_edit_page(environ, equipment_id, error)
    with connect() as conn:
        row = conn.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
        if not row:
            return equipment_edit_page(environ, equipment_id, "Equipamento inválido.")
        _, compatibility = validate_vendor_driver(conn, values["vendor_id"], values["ssh_backup_driver"])
        if compatibility:
            return equipment_edit_page(environ, equipment_id, compatibility)
        try:
            conn.execute(
                """
                UPDATE equipment
                SET name = ?, hostname = ?, ip_address = ?, vendor_id = ?, group_id = ?,
                    pop_id = ?, environment_id = ?, is_active = ?, ssh_backup_driver = ?,
                    ssh_port = ?, ssh_custom_command = ?, notes = ?
                WHERE id = ?
                """,
                (
                    values["name"],
                    values["hostname"],
                    values["ip_address"],
                    values["vendor_id"],
                    values["group_id"],
                    values["pop_id"],
                    values["environment_id"],
                    values["is_active"],
                    values["ssh_backup_driver"],
                    values["ssh_port"],
                    values["ssh_custom_command"],
                    values["notes"],
                    equipment_id,
                ),
            )
        except Exception:
            return equipment_edit_page(environ, equipment_id, "Não foi possível atualizar o equipamento. Verifique os dados informados.")
        audit(conn, user["id"], "equipment.updated", "equipment", equipment_id, json.dumps({"hostname": values["hostname"], "driver": values["ssh_backup_driver"], "active": values["is_active"]}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
    return redirect_with_message("/equipment", "success", "Equipamento atualizado com sucesso.")


def handle_credential_create(environ: dict, equipment_id: int) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    form = parse_form(environ)
    try:
        with connect() as conn:
            equipment = conn.execute("SELECT id, ssh_port FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
            if not equipment:
                return equipment_detail_page(environ, equipment_id, "Equipamento inválido.")
            port = parse_port(form.get("port") or str(equipment["ssh_port"] or 22))
            credential = create_credential(
                conn,
                equipment_id,
                user["id"],
                form.get("name", ""),
                form.get("username", ""),
                form.get("password", ""),
                port,
                form.get("notes", ""),
            )
            if not parse_active(form.get("is_active", "1")):
                conn.execute("UPDATE device_credentials SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (credential["id"],))
    except ValueError as exc:
        return equipment_detail_page(environ, equipment_id, str(exc))
    except Exception:
        return equipment_detail_page(environ, equipment_id, "Não foi possível salvar a credencial. Verifique os campos informados.")
    return equipment_detail_page(environ, equipment_id, success="Credencial salva com sucesso.")


def load_credential_for_admin(conn, credential_id: int):
    return conn.execute("SELECT * FROM device_credentials WHERE id = ? AND deleted_at IS NULL", (credential_id,)).fetchone()


def handle_credential_edit(environ: dict, credential_id: int) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    form = parse_form(environ)
    equipment_id = 0
    error = ""
    with connect() as conn:
        row = load_credential_for_admin(conn, credential_id)
        if not row:
            return redirect("/equipment")
        equipment_id = row["equipment_id"]
        try:
            updated = update_credential(
                conn,
                credential_id,
                user["id"],
                form.get("name", ""),
                form.get("username", ""),
                form.get("password", ""),
                parse_port(form.get("port", "22")),
                form.get("notes", ""),
            )
            new_state = parse_active(form.get("is_active", "0"))
            if new_state:
                conn.execute(
                    """
                    UPDATE device_credentials
                    SET is_active = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE equipment_id = ? AND credential_type = 'ssh' AND deleted_at IS NULL AND id != ?
                    """,
                    (row["equipment_id"], credential_id),
                )
            conn.execute("UPDATE device_credentials SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_state, updated["id"]))
        except ValueError as exc:
            error = str(exc)
        except Exception:
            error = "Não foi possível editar a credencial. Verifique os campos informados."
    if error:
        return equipment_detail_page(environ, equipment_id, error)
    return equipment_detail_page(environ, equipment_id, success="Credencial salva com sucesso.")


def handle_credential_toggle(environ: dict, credential_id: int) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    with connect() as conn:
        row = load_credential_for_admin(conn, credential_id)
        if not row:
            return redirect("/equipment")
        new_state = 0 if row["is_active"] else 1
        if new_state:
            conn.execute(
                """
                UPDATE device_credentials
                SET is_active = 0, updated_at = CURRENT_TIMESTAMP
                WHERE equipment_id = ? AND credential_type = 'ssh' AND deleted_at IS NULL
                """,
                (row["equipment_id"],),
            )
        conn.execute("UPDATE device_credentials SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_state, credential_id))
        audit(conn, user["id"], "credential.updated", "device_credential", row["uuid"], json.dumps({"equipment_id": row["equipment_id"], "active": new_state}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
    return redirect(f"/equipment/{row['equipment_id']}")


def handle_credential_delete(environ: dict, credential_id: int) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    with connect() as conn:
        row = load_credential_for_admin(conn, credential_id)
        if not row:
            return redirect("/equipment")
        conn.execute("UPDATE device_credentials SET deleted_at = CURRENT_TIMESTAMP, is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (credential_id,))
        audit(conn, user["id"], "credential.deleted", "device_credential", row["uuid"], json.dumps({"equipment_id": row["equipment_id"], "type": "ssh"}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
    return redirect(f"/equipment/{row['equipment_id']}")


def record_ssh_test_failure(conn, equipment_id: int, credential_id: int | None, user_id: int, code: str, message_text: str, ip: str) -> None:
    if credential_id is None:
        audit(conn, user_id, "credential.test_failed", "equipment", equipment_id, json.dumps({"equipment_id": equipment_id, "error_code": code}, separators=(",", ":")), ip)
        return
    row = conn.execute("SELECT uuid FROM device_credentials WHERE id = ? AND deleted_at IS NULL", (credential_id,)).fetchone()
    if not row:
        return
    conn.execute(
        """
        UPDATE device_credentials
        SET last_test_status = 'failed', last_test_at = CURRENT_TIMESTAMP,
            last_test_error = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (message_text, credential_id),
    )
    audit(conn, user_id, "credential.test_failed", "device_credential", row["uuid"], json.dumps({"equipment_id": equipment_id, "error_code": code}, separators=(",", ":")), ip)


def handle_credential_test(environ: dict, credential_id: int) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    equipment_id = 0
    error = ""
    error_code = ""
    with connect() as conn:
        row = load_credential_for_admin(conn, credential_id)
        if not row:
            return redirect("/equipment")
        equipment_id = row["equipment_id"]
        try:
            test_connection(conn, equipment_id, user["id"], credential_id)
        except SSHBackupError as exc:
            error = ERROR_MESSAGES.get(exc.code, str(exc))
            error_code = exc.code
            conn.rollback()
            record_ssh_test_failure(conn, equipment_id, credential_id, user["id"], error_code, error, environ.get("REMOTE_ADDR", ""))
    if error:
        return equipment_redirect(equipment_id, "error", error)
    return equipment_redirect(equipment_id, "success", "Conexão SSH testada com sucesso.")


def handle_equipment_ssh_test(environ: dict, equipment_id: int) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    return handle_equipment_ssh_test_logic(
        connect=connect,
        equipment_id=equipment_id,
        user=user,
        environ=environ,
        get_setting=get_setting,
        probe_connection=probe_connection,
        record_ssh_test_failure=record_ssh_test_failure,
        audit=audit,
        queue_flash=queue_flash,
        redirect=redirect,
        ERROR_MESSAGES=ERROR_MESSAGES,
        SSHBackupError=SSHBackupError,
    )


def backup_action_buttons(row, user) -> str:
    download = f'<a class="button small secondary" href="/backups/{e(row["uuid"])}/download">Baixar</a>'
    details = f'<a class="button small secondary" href="/backups/{e(row["uuid"])}">Detalhes</a>'
    if not user["can_admin"]:
        return f'<div class="actions">{details}{download}</div>'
    forms = [details]
    if row["backup_status"] == "available":
        forms.append(download)
        forms.append(
            f'<form method="post" action="/backups/{e(row["uuid"])}/trash" onsubmit="return confirm(\'Mover backup para a lixeira?\')"><button class="small danger" type="submit">Lixeira</button></form>'
        )
    elif row["backup_status"] == "trashed":
        forms.append(
            f'<form method="post" action="/backups/{e(row["uuid"])}/restore" onsubmit="return confirm(\'Restaurar backup da lixeira?\')"><button class="small secondary" type="submit">Restaurar</button></form>'
        )
    return f'<div class="actions">{"".join(forms)}</div>'


def backup_table(rows, user) -> str:
    body = "".join(
        f"""
        <tr>
          <td>{e(local_dt(row['received_at']))}</td>
          <td>{e(row['original_filename'])}</td>
          <td>{e(row['source_method'])}</td>
          <td>{e(row['backup_reason'])}</td>
          <td><span class="badge">{e(row['backup_status'])}</span></td>
          <td>{format_size(row['file_size'])}</td>
          <td><code>{e(short_hash(row['sha256']))}</code></td>
          <td>{row['download_count']}</td>
          <td>{backup_action_buttons(row, user)}</td>
        </tr>
        """
        for row in rows
    )
    if not body:
        body = '<tr><td colspan="9" class="muted">Nenhum backup encontrado.</td></tr>'
    return f"""
    <table>
      <thead><tr><th>Data e hora</th><th>Arquivo original</th><th>Método</th><th>Motivo</th><th>Status</th><th>Tamanho</th><th>Hash</th><th>Downloads</th><th>Ações</th></tr></thead>
      <tbody>{body}</tbody>
    </table>
    """


def schedule_label(row) -> str:
    if not row["schedule_enabled"] or row["schedule_type"] == "none":
        return "Execução manual"
    if row["schedule_type"] == "daily":
        return f"Diário às {row['schedule_time']}"
    if row["schedule_type"] == "weekly":
        days = {part.strip() for part in (row["schedule_days"] or "").split(",") if part.strip()}
        labels = ", ".join(label for value, label in DAY_LABELS if value in days) or "-"
        return f"Semanal {labels} {row['schedule_time']}"
    return row["schedule_type"]


def run_details_json(job, run) -> str:
    return json.dumps(
        {
            "equipment_id": run["equipment_id"],
            "job_uuid": job["uuid"] if job else "",
            "run_uuid": run["uuid"],
            "status": run["status"],
            "trigger": run["trigger_type"],
            "method": run["method"],
        },
        separators=(",", ":"),
    )


def job_action_buttons(row, user) -> str:
    details = f'<a href="/jobs/{e(row["uuid"])}/runs">Ver execuções</a>'
    if not can_operate(user):
        return f'<details class="equipment-job-actions jobs-table-actions"><summary>Gerenciar</summary><div>{details}</div></details>'
    buttons = [details]
    buttons.append(f'<form method="post" action="/jobs/{e(row["uuid"])}/run"><button type="submit">Executar agora</button></form>')
    if row["status"] == "paused" and row["schedule_enabled"] and row["schedule_type"] != "none":
        buttons.append(f'<form method="post" action="/jobs/{e(row["uuid"])}/resume"><button type="submit">Retomar agendamento</button></form>')
    elif row["status"] == "active" and row["schedule_enabled"] and row["schedule_type"] != "none":
        buttons.append(f'<form method="post" action="/jobs/{e(row["uuid"])}/pause"><button type="submit">Pausar agendamento</button></form>')
    buttons.append(f'<a href="/jobs/{e(row["uuid"])}/edit">Editar</a>')
    buttons.append(f'<form method="post" action="/jobs/{e(row["uuid"])}/delete" onsubmit="return confirm(\'Excluir este agendamento?\')"><button class="danger" type="submit">Excluir</button></form>')
    return f'<details class="equipment-job-actions jobs-table-actions"><summary>Gerenciar</summary><div>{"".join(buttons)}</div></details>'


def job_table(rows, user) -> str:
    method_labels = {"ssh": "Backup via SSH", "dry_run": "Simulação de teste", "manual": "Execução manual", "ftp": "Recebimento via FTP"}
    body = "".join(
        f"""
        <tr>
          <td><a class="inline-link" href="/equipment/{row['equipment_id']}">{e(row['hostname'])}</a></td>
          <td>{e(method_labels.get(row['method'], str(row['method']).upper()))}</td>
          <td>{e(schedule_label(row))}</td>
          <td><span class="badge status-{'success' if row['status'] == 'active' else 'warning'}">{'Ativo' if row['status'] == 'active' else 'Pausado' if row['status'] == 'paused' else 'Desativado' if row['status'] in {'disabled', 'deleted'} else e(row['status']).title()}</span></td>
          <td>{e(local_dt(row['last_run_at']))}</td>
          <td>{e(local_dt(row['next_run_at']))}</td>
          <td>{job_action_buttons(row, user)}</td>
        </tr>
        """
        for row in rows
    )
    if not body:
        body = '<tr data-technical-empty="Nenhum job encontrado"><td colspan="7" class="muted">Nenhum agendamento encontrado.</td></tr>'
    return f"<table><thead><tr><th>Equipamento</th><th>Método</th><th>Agendamento</th><th>Status</th><th>Última execução</th><th>Próxima execução</th><th>Ações</th></tr></thead><tbody>{body}</tbody></table>"


def run_table(rows) -> str:
    body = "".join(
        f"""
        <tr>
          <td>{e(local_dt(row['created_at']))}</td>
          <td><a class="inline-link" href="/equipment/{row['equipment_id']}">{e(row['hostname'])}</a></td>
          <td>{e(row['trigger_type'])}</td>
          <td>{e(row['method'])}</td>
          <td><span class="badge">{e(row['status'])}</span></td>
          <td>{e(str(row['duration_ms']) + ' ms' if row['duration_ms'] is not None else '-')}</td>
          <td>{e(row['error_code'] or '-')}</td>
          <td><a class="button small secondary" href="/job-runs/{e(row['uuid'])}">Detalhes</a></td>
        </tr>
        """
        for row in rows
    )
    if not body:
        body = '<tr><td colspan="8" class="muted">Nenhuma execução encontrada.</td></tr>'
    return f"<table><thead><tr><th>Data/hora</th><th>Equipamento</th><th>Origem</th><th>Método</th><th>Status</th><th>Duração</th><th>Erro</th><th>Ações</th></tr></thead><tbody>{body}</tbody></table>"


def job_form(equipment_id: int, job=None, timezone_name: str = "America/Sao_Paulo", olt_mode: bool = False, onboarding: bool = False) -> str:
    selected_method = job["method"] if job else ("ftp" if olt_mode else ("ssh" if onboarding else "dry_run"))
    selected_type = job["schedule_type"] if job else ("daily" if onboarding else "none")
    selected_days = set((job["schedule_days"] if job else "").split(","))
    method_labels = {"manual": "Execução manual", "ssh": "Backup do equipamento via SSH", "ftp": "Recebimento via FTP", "sftp": "Recebimento via SFTP", "tftp": "Recebimento via TFTP", "api": "Integração via API", "dry_run": "Simulação de teste — não cria arquivo"}
    type_labels = {"none": "Sem frequência — executar manualmente", "daily": "Todos os dias", "weekly": "Dias específicos da semana"}
    method_options = "".join(
        f'<option value="{method}" {"selected" if selected_method == method else ""} {"disabled" if method not in (("ftp",) if olt_mode else ("manual", "dry_run", "ssh")) else ""}>{method_labels.get(method, method.upper())}</option>'
        for method in JOB_METHODS if method != "api"
    )
    type_options = "".join(
        f'<option value="{item}" {"selected" if selected_type == item else ""}>{type_labels.get(item, item.title())}</option>'
        for item in SCHEDULE_TYPES
    )
    day_checks = "".join(
        f'<label class="check"><input type="checkbox" name="schedule_days" value="{value}" {"checked" if value in selected_days else ""}> {label}</label>'
        for value, label in DAY_LABELS
    )
    action = f"/jobs/{job['uuid']}/edit" if job else f"/equipment/{equipment_id}/jobs"
    title = "Editar agendamento" if job else "Criar agendamento"
    cancel_url = f"/equipment/{equipment_id}#backup"
    schedule_time_value = str(job["schedule_time"] if job else "02:00")[:5]
    selected_hour, selected_minute = (schedule_time_value.split(":", 1) + ["00"])[:2]
    hour_options = "".join(f'<option value="{value:02d}" {"selected" if f"{value:02d}" == selected_hour else ""}>{value:02d}</option>' for value in range(24))
    minute_options = "".join(f'<option value="{value:02d}" {"selected" if f"{value:02d}" == selected_minute else ""}>{value:02d}</option>' for value in range(60))
    return f"""
    <form method="post" action="{action}" class="panel job-modern-form" data-job-frequency-form>
      <header><div><span class="eyebrow">Backup automático</span><h2>{title}</h2><p>Defina como e quando o backup deste equipamento será executado.</p></div><span class="badge status-{'success' if job and job['status'] == 'active' else 'warning'}">{'Ativo' if job and job['status'] == 'active' else 'Nova configuração'}</span></header>
      <div class="job-modern-fields"><label>Método de backup<select name="method">{method_options}</select><small>Escolha o processo usado para gerar ou validar o backup.</small></label><label>Frequência<select name="schedule_type" data-job-frequency>{type_options}</select><small>Sem frequência: o backup só inicia quando alguém clicar em Executar agora.</small></label><div class="job-time-field" data-job-time-field><span>Horário da execução</span><div class="job-time-picker" data-job-time-picker><button type="button" data-job-time-open><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg><strong data-job-time-label>{e(schedule_time_value)}</strong></button><div class="job-time-popover" data-job-time-popover hidden><label>Hora<select data-job-hour>{hour_options}</select></label><span>:</span><label>Minuto<select data-job-minute>{minute_options}</select></label><button type="button" data-job-time-apply>Aplicar</button></div><input type="hidden" name="schedule_time" value="{e(schedule_time_value)}" data-job-time-value></div><small>Horário local: {e(job['timezone'] if job else timezone_name)}.</small></div><fieldset data-job-weekdays><legend>Dias de execução</legend><div class="job-modern-days">{day_checks}</div><small data-job-weekdays-help>Selecione os dias em que o backup será executado.</small></fieldset><label class="job-modern-notes">Observações (opcional)<textarea name="notes" rows="3" placeholder="Informações sobre este agendamento">{e(job['notes'] if job else '')}</textarea></label></div>
      <input type="hidden" name="timezone" value="{e(job['timezone'] if job else timezone_name)}">
      {f'<input type="hidden" name="onboarding" value="ssh">' if onboarding and not job else ''}
      <footer><a class="button secondary" href="{cancel_url}">Cancelar</a><button type="submit">Salvar agendamento</button></footer>
    </form>
    """


def mask_username(username: str) -> str:
    if len(username) <= 2:
        return "*" * len(username)
    return username[0] + ("*" * max(1, len(username) - 2)) + username[-1]


def credential_rows(credentials, user) -> str:
    rows = ""
    for cred in credentials:
        actions = '<span class="muted">Leitura</span>'
        if can_operate(user):
            active_action = "Desativar" if cred["is_active"] else "Ativar"
            actions = f"""
            <div class="actions">
              <form method="post" action="/credentials/{cred['id']}/toggle"><button class="small secondary" type="submit">{active_action}</button></form>
              <form method="post" action="/credentials/{cred['id']}/delete" onsubmit="return confirm('Excluir credencial?')"><button class="small danger" type="submit">Excluir</button></form>
            </div>
            """
        username = cred["username"] if can_operate(user) else mask_username(cred["username"])
        test_ok = cred["last_test_status"] == "success"
        rows += f"""<article class="equipment-credential-item"><div class="equipment-credential-identity"><span class="equipment-avatar"><svg class="equipment-ui-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM5 21a7 7 0 0 1 14 0"/></svg></span><div><strong>{e(cred['name'])}</strong><small>Usuário: {e(username)} &nbsp; · &nbsp; Porta: {e(cred['port'])}</small></div><span class="badge status-{'success' if cred['is_active'] else 'warning'}">{'Ativa' if cred['is_active'] else 'Inativa'}</span></div><dl><div><dt>Último teste</dt><dd><span class="badge status-{'success' if test_ok else 'warning'}">{e('Sucesso' if test_ok else cred['last_test_status'] or 'Não testado')}</span></dd></div><div><dt>Data</dt><dd>{e(local_dt(cred['last_test_at']))}</dd></div><div><dt>Resultado</dt><dd>{e(cred['last_test_error'] or ('Conexão validada' if test_ok else '-'))}</dd></div></dl><div class="equipment-credential-actions">{actions}</div></article>"""
    if not rows:
        rows = '<div class="equipment-credential-empty">Credencial SSH não configurada.</div>'
    return f'<div class="equipment-credential-list">{rows}</div>'


FINAL_RUN_STATUSES = {"success", "failed", "timeout", "cancelled", "skipped"}


SSH_RUN_ERROR_MESSAGES = {
    "SSH_CONNECTION_REFUSED": "A conexão SSH foi recusada pelo equipamento.",
    "SSH_CONNECTION_FAILED": "Não foi possível estabelecer conexão SSH com o equipamento.",
    "SSH_AUTH_FAILED": "Falha de autenticação SSH. Verifique o usuário e a senha.",
    "SSH_TIMEOUT": "Tempo limite ao tentar conectar ao equipamento.",
}


def ssh_run_message(row) -> str:
    if not row:
        return "-"
    status = row["status"]
    if status == "queued":
        return "Aguardando execução pelo worker."
    if status == "running":
        return "Backup SSH em execução."
    if status == "success":
        return "Backup SSH concluído com sucesso."
    if status == "failed":
        detail = SSH_RUN_ERROR_MESSAGES.get(row["error_code"], row["error_message"] or row["safe_log"] or "")
        return f"Falha ao executar backup SSH. {detail}".strip()
    return row["error_message"] or row["safe_log"] or "-"


def run_status_payload(row) -> dict:
    return {
        "uuid": row["uuid"],
        "status": row["status"],
        "trigger_type": row["trigger_type"],
        "method": row["method"],
        "started_at": local_dt(row["started_at"]) if row["started_at"] else "",
        "finished_at": local_dt(row["finished_at"]) if row["finished_at"] else "",
        "duration_ms": row["duration_ms"],
        "error_code": row["error_code"] or "",
        "safe_message": ssh_run_message(row),
        "created_backup_uuid": row["created_backup_uuid"] or "",
        "created_backup_name": row["created_backup_name"] or "",
        "created_backup_size": format_size(row["created_backup_size"]) if row["created_backup_size"] is not None else "",
    }


def ssh_run_panel(run, user, equipment_id: int) -> str:
    if not run:
        return """
        <article class="panel"><h2>Última execução SSH</h2>
          <p class="muted">Nenhuma execução SSH registrada.</p>
        </article>
        """
    payload = run_status_payload(run)
    backup_uuid = payload["created_backup_uuid"]
    backup_info = (
        f"{payload['created_backup_name']} ({payload['created_backup_size']})"
        if backup_uuid
        else ("Nenhum arquivo foi criado." if payload["status"] == "failed" else "-")
    )
    if backup_uuid:
        backup_actions = f"""
        <span data-backup-actions>
          <a class="button secondary" data-backup-href href="/backups/{e(backup_uuid)}">Ver backup</a>
          <a class="button secondary" data-backup-href href="/backups/{e(backup_uuid)}/download">Baixar</a>
        </span>
        """
    else:
        backup_actions = '<span data-backup-actions hidden></span>'
    retry = ""
    if can_operate(user):
        retry = f"""
        <form method="post" action="/equipment/{equipment_id}/jobs/run-now">{hidden('method', 'ssh')}<button class="secondary" type="submit">Tentar novamente</button></form>
        """
    return f"""
    <article class="panel" data-ssh-run-panel data-current-status="{e(payload['status'])}" data-status-url="/job-runs/{e(run['uuid'])}/status">
      <h2>Última execução SSH</h2>
      <dl class="details">
        <dt>Status atual</dt><dd><span class="badge status-{e(payload['status'])}" data-run-status>{e(payload['status'])}</span></dd>
        <dt>Origem</dt><dd data-run-trigger>{e(payload['trigger_type'])}</dd>
        <dt>Método</dt><dd data-run-method>{e(payload['method'])}</dd>
        <dt>Início</dt><dd data-run-started>{e(payload['started_at'] or '-')}</dd>
        <dt>Fim</dt><dd data-run-finished>{e(payload['finished_at'] or '-')}</dd>
        <dt>Duração</dt><dd data-run-duration>{e(str(payload['duration_ms']) + ' ms' if payload['duration_ms'] is not None else '-')}</dd>
        <dt>Código do erro</dt><dd data-run-error>{e(payload['error_code'] or '-')}</dd>
        <dt>Mensagem segura</dt><dd data-run-message>{e(payload['safe_message'])}</dd>
        <dt>Backup criado</dt><dd data-backup-info>{e(backup_info)}</dd>
      </dl>
      <div class="actions block-actions">
        <a class="button secondary" href="/job-runs/{e(run['uuid'])}">Detalhes da execução</a>
        {backup_actions}
        {retry if payload['status'] in FINAL_RUN_STATUSES and payload['status'] != 'success' else ''}
      </div>
    </article>
    """


def ssh_status_panel(item, credentials, last_ssh_backup, last_ssh_run, user) -> str:
    active = next((cred for cred in credentials if cred["is_active"]), None)
    driver = item["ssh_backup_driver"] or ""
    test = active["last_test_status"] if active else ""
    test_at = active["last_test_at"] if active else None
    test_error = active["last_test_error"] if active else ""
    port = active["port"] if active else item["ssh_port"]
    action = ""
    ftp_push_olt = is_ftp_push_olt_driver(driver)
    if can_operate(user) and not ftp_push_olt:
        action = f"""
        <div class="actions block-actions">
          <form method="post" action="/equipment/{item['id']}/ssh-test"><button class="secondary" type="submit">Testar conexão de acesso</button></form>
          <form method="post" action="/equipment/{item['id']}/jobs/run-now">{hidden('method', 'ssh')}<button type="submit">Executar backup SSH agora</button></form>
        </div>
        """
    mode_note = ('<p class="notice info">O SSH é usado para acessar a OLT; o arquivo de backup será recebido pelo FTP.</p>'
                 if ftp_push_olt else "")
    return f"""
    <article class="panel"><h2>{'Acesso da OLT' if ftp_push_olt else 'Backup SSH'}</h2>
      <dl class="details">
        <dt>Driver configurado</dt><dd>{e(SSH_DRIVER_LABELS.get(driver, 'Driver SSH não configurado.'))}</dd>
        <dt>Credencial ativa</dt><dd>{e(active['name'] if active else 'Credencial SSH não configurada.')}</dd>
        <dt>Porta efetiva</dt><dd>{e(port or '-')}</dd>
        <dt>Último teste</dt><dd>{e(test or '-')}</dd>
        <dt>Data do último teste</dt><dd>{e(local_dt(test_at))}</dd>
        <dt>Mensagem do último teste</dt><dd>{e(test_error or '-')}</dd>
        <dt>Último backup SSH</dt><dd>{e(local_dt(last_ssh_backup['received_at'] if last_ssh_backup else None))}</dd>
      </dl>
      {mode_note}{action}
    </article>
    """


def equipment_detail_page(environ: dict, equipment_id: int, error: str = "", success: str = "", info: str = "") -> Response:
    user, response = require_user(environ)
    if response:
        return response
    query = parse_qs(environ.get("QUERY_STRING", ""))
    onboarding = (query.get("onboarding") or [""])[-1]
    if onboarding not in {"ftp", "ftp-install", "ssh"}:
        onboarding = ""
    selected_run_uuid = (query.get("run") or [""])[-1]
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", selected_run_uuid or ""):
        selected_run_uuid = ""
    if not error and not success and not info:
        flash_kind, flash_text = pop_flash(environ)
        if flash_kind == "error":
            error = flash_text
        elif flash_kind == "success":
            success = flash_text
        elif flash_kind == "info":
            info = flash_text
    with connect() as conn:
        context = equipment_detail_context(conn, equipment_id, selected_run_uuid=selected_run_uuid)
    item = context["item"]
    if not item:
        return render("Equipamento não encontrado", "<p>Equipamento não encontrado.</p>", user, HTTPStatus.NOT_FOUND)
    content = equipment_detail_content(
        user=user,
        item=item,
        rows=context["rows"],
        jobs=context["jobs"],
        credentials=context["credentials"],
        last_ssh_backup=context["last_ssh_backup"],
        last_ssh_run=context["last_ssh_run"],
        config=context["config"],
        timezone_name=context["timezone_name"],
        artifacts_v2=context["artifacts_v2"],
        default_ftp_host=context["default_ftp_host"],
        default_ftp_port=context["default_ftp_port"],
        mikrotik_snapshot=context["mikrotik_snapshot"],
        csrf_token=session_csrf(environ),
        olt_operation=context["olt_operation"],
        olt_artifacts=context["olt_artifacts"],
        equipment_id=equipment_id,
        onboarding=onboarding,
        error=error,
        success=success,
        info=info,
        local_dt=local_dt,
        hidden=hidden,
        can_operate=can_operate,
        message=message,
        backup_table=backup_table,
        credential_rows=credential_rows,
        ssh_status_panel=ssh_status_panel,
        ssh_run_panel=ssh_run_panel,
        job_table=job_table,
        generate_mikrotik_removal_script=generate_mikrotik_removal_script,
        ftp_operation_payload=_ftp_operation_payload,
        job_form=job_form,
        is_mikrotik_equipment=is_strict_mikrotik,
        mask_username=mask_username,
        mikrotik_equipment_panel=mikrotik_equipment_panel,
        format_size=format_size,
    )
    return render(item["hostname"], content, user)


def handle_backup_import(environ: dict, equipment_id: int) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    content_length = int(environ.get("CONTENT_LENGTH") or 0)
    temp_path = None
    target = None
    with connect() as conn:
        equipment = conn.execute("SELECT id FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
        config = load_config(conn)
        ensure_directories(config)
        if not equipment:
            return equipment_detail_page(environ, equipment_id, "Equipamento inválido.")
        if content_length <= 0:
            return equipment_detail_page(environ, equipment_id, "Upload incompleto.")
        if content_length > config.maximum_upload_size + 16384:
            return equipment_detail_page(environ, equipment_id, "Arquivo acima do limite permitido.")
        fields, files = parse_multipart(environ)
        field = files.get("backup_file")
        if field is None or not field.filename:
            return equipment_detail_page(environ, equipment_id, "Arquivo não enviado.")
        return handle_backup_import_logic(
            conn=conn,
            user=user,
            equipment_id=equipment_id,
            field=field,
            fields=fields,
            config=config,
            environ=environ,
            safe_filename=safe_filename,
            now_utc=now_utc,
            write_upload_to_temporary=write_upload_to_temporary,
            backup_relative_path=backup_relative_path,
            resolve_inside=resolve_inside,
            atomic_move=atomic_move,
            enqueue_new_backup=enqueue_new_backup,
            enqueue_new_telegram_backup=enqueue_new_telegram_backup,
            audit=audit,
            backup_details_json=backup_details_json,
            detail_page=equipment_detail_page,
            success_response=lambda item_id: redirect(f"/equipment/{item_id}"),
        )
def backups_page(environ: dict) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    query = parse_qs(environ.get("QUERY_STRING", ""))
    filters = {key: values[-1].strip() for key, values in query.items() if values}
    with connect() as conn:
        ctx = backups_page_context(conn, filters)
    content = backups_page_content(
        user=user,
        filters=filters,
        ctx=ctx,
        local_dt=local_dt,
        option_rows=option_rows,
        e=e,
        format_size=format_size,
        backup_action_buttons=backup_action_buttons,
        report_icon=report_icon,
    )
    return render("Backups", content, user)


def load_backup(conn, backup_uuid: str):
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", backup_uuid or ""):
        return None
    return conn.execute(
        """
        SELECT backups.*, equipment.hostname
        FROM backups JOIN equipment ON equipment.id = backups.equipment_id
        WHERE backups.uuid = ?
        """,
        (backup_uuid,),
    ).fetchone()


def backup_detail_page(environ: dict, backup_uuid: str) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    with connect() as conn:
        snapshot = backup_detail_context(conn, backup_uuid)
    row = snapshot["row"]
    cloud_items = snapshot["cloud_items"]
    telegram_items = snapshot["telegram_items"]
    if not row:
        return render("Backup não encontrado", "<p>Backup não encontrado.</p>", user, HTTPStatus.NOT_FOUND)
    status_label = {"available": "Disponível", "trashed": "Na lixeira", "deleted": "Excluído"}.get(row["backup_status"], str(row["backup_status"]).replace("_", " ").title())
    status_class = "success" if row["backup_status"] == "available" else "warning"
    method_label = {"ssh": "SSH", "ftp": "FTP", "manual": "Importação manual"}.get(row["source_method"], str(row["source_method"]).upper())
    reason_label = {"manual": "Execução manual", "scheduled": "Execução agendada", "upload": "Arquivo recebido"}.get(row["backup_reason"], str(row["backup_reason"] or "-").replace("_", " ").title())
    notes = str(row["notes"] or "").strip()
    if notes.startswith("driver="):
        driver = notes.split("=", 1)[1]
        notes_label = f"Driver: {SSH_DRIVER_LABELS.get(driver, driver.replace('_', ' ').title())}"
    else:
        notes_label = notes or "Nenhuma observação registrada."
    cloud_history = "".join(
        f'''<div class="backup-detail-delivery"><div class="backup-detail-delivery-main"><span class="backup-detail-cloud-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M7 18h11a4 4 0 1 0-.4-8A7 7 0 0 0 4.8 9.4 4.5 4.5 0 0 0 7 18Z"/></svg></span><span><strong>{e(item['target_name'])}</strong><small>Tentativa {item['attempt']} · {e(local_dt(item['finished_at']))}</small></span></div><span class="badge status-{'success' if item['status'] == 'synced' else 'warning'}">{e(str(item['status']).replace('_', ' ').title())}</span></div>'''
        for item in cloud_items
    ) or '<div class="backup-detail-empty"><strong>Nenhuma sincronização registrada</strong><small>Este backup ainda não possui cópia em um destino externo.</small></div>'
    telegram_history = "".join(
        f'''<div class="backup-detail-delivery"><div><strong>{e(item['destination_name'])}</strong><small>Tentativa {item['attempt']} · {e(local_dt(item['sent_at']))}</small></div><span class="badge status-{'success' if item['status'] == 'sent' else 'warning'}">{e(str(item['status']).replace('_', ' ').title())}</span></div>'''
        for item in telegram_items
    ) or '<div class="backup-detail-empty"><strong>Nenhum envio registrado</strong><small>Este backup ainda não foi enviado ao Telegram.</small></div>'
    content = f"""
    <section class="backup-detail-page"><nav class="breadcrumbs"><a href="/">Início</a><span>/</span><a href="/equipment/{row['equipment_id']}#historico">Histórico de backups</a><span>/</span><strong>Detalhes</strong></nav>
    <header class="backup-detail-hero"><div><span class="eyebrow">Detalhes do backup</span><h1>{e(row['original_filename'])}</h1><p>Arquivo recebido em {e(local_dt(row['received_at']))}</p></div><div class="actions"><span class="badge status-{status_class}">{status_label}</span>{f'<a class="button" href="/backups/{e(backup_uuid)}/download">Baixar backup</a>' if row['backup_status'] == 'available' else ''}<a class="button secondary" href="/equipment/{row['equipment_id']}#historico">Voltar ao histórico</a></div></header>
    <section class="backup-detail-summary"><article><span class="backup-detail-summary-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4M7 8h.01M10 8h7"/></svg></span><div><span>Equipamento</span><strong>{e(row['hostname'])}</strong><small>Origem do arquivo</small></div></article><article><span class="backup-detail-summary-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m7 10 3 3-3 3m5 0h5"/></svg></span><div><span>Método</span><strong>{e(method_label)}</strong><small>{e(reason_label)}</small></div></article><article><span class="backup-detail-summary-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 2h9l4 4v16H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z"/><path d="M14 2v5h5M8 13h8M8 17h6"/></svg></span><div><span>Tamanho</span><strong>{format_size(row['file_size'])}</strong><small>{'Nenhum download' if not row['download_count'] else f'{row["download_count"]} download' if row['download_count'] == 1 else f'{row["download_count"]} downloads'}</small></div></article><article><span class="backup-detail-summary-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 19h14"/></svg></span><div><span>Último download</span><strong>{e(local_dt(row['last_downloaded_at']) if row['last_downloaded_at'] else 'Nunca baixado')}</strong><small>Histórico de acesso</small></div></article></section>
    <section class="backup-detail-grid"><article class="panel backup-detail-info"><div class="panel-heading"><h2>Informações do arquivo</h2><span class="badge status-{status_class}">{status_label}</span></div><dl><dt>Nome do arquivo</dt><dd>{e(row['original_filename'])}</dd><dt>Equipamento</dt><dd><a href="/equipment/{row['equipment_id']}">{e(row['hostname'])}</a></dd><dt>Método de recebimento</dt><dd>{e(method_label)}</dd><dt>Como foi iniciado</dt><dd>{e(reason_label)}</dd><dt>Data de recebimento</dt><dd>{e(local_dt(row['received_at']))}</dd><dt>Observação</dt><dd>{e(notes_label)}</dd></dl><details class="backup-detail-technical"><summary>Ver informações técnicas</summary><dl><dt>Identificador</dt><dd><code>{e(row['uuid'])}</code></dd><dt>SHA-256</dt><dd><code>{e(row['sha256'])}</code></dd></dl></details></article>
    <div class="backup-detail-integrations"><article class="panel"><div class="backup-detail-card-head"><div><h2><span class="backup-detail-cloud-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M7 18h11a4 4 0 1 0-.4-8A7 7 0 0 0 4.8 9.4 4.5 4.5 0 0 0 7 18Z"/></svg></span>Sincronização externa</h2><p>Cópias enviadas para armazenamento externo.</p></div>{f'<form method="post" action="/backups/{e(backup_uuid)}/cloud-enqueue"><button type="submit">Sincronizar agora</button></form>' if user['can_admin'] else ''}</div>{cloud_history}</article><article class="panel"><div class="backup-detail-card-head"><div><h2>Telegram</h2><p>Envios deste arquivo para destinos configurados.</p></div>{f'<form method="post" action="/backups/{e(backup_uuid)}/telegram-enqueue"><input type="hidden" name="csrf_token" value="{session_csrf(environ)}"><button type="submit">Enviar agora</button></form>' if can_operate(user) else ''}</div>{telegram_history}</article></div></section></section>
    """
    return render("Detalhes do backup", content, user)


def download_backup(environ: dict, backup_uuid: str) -> Response:
    user, response = require_download(environ)
    if response:
        return response
    with connect() as conn:
        row = load_backup(conn, backup_uuid)
        if not row or row["backup_status"] != "available":
            return render("Backup indisponivel", "<p>Backup indisponivel para download.</p>", user, HTTPStatus.NOT_FOUND)
        config = load_config(conn)
        try:
            path = resolve_inside(config.backup_directory, row["relative_path"])
        except ValueError:
            return render("Backup inválido", "<p>Caminho de armazenamento inválido.</p>", user, HTTPStatus.INTERNAL_SERVER_ERROR)
        if not path.is_file():
            return render("Backup ausente", "<p>Arquivo físico não encontrado.</p>", user, HTTPStatus.NOT_FOUND)
        conn.execute(
            "UPDATE backups SET download_count = download_count + 1, last_downloaded_at = CURRENT_TIMESTAMP WHERE uuid = ?",
            (backup_uuid,),
        )
        updated = load_backup(conn, backup_uuid)
        audit(conn, user["id"], "backup.downloaded", "backup", backup_uuid, backup_details_json(updated), environ.get("REMOTE_ADDR", ""))
        body = path.read_bytes()
    filename = safe_filename(row["original_filename"])
    return Response(
        body,
        HTTPStatus.OK,
        [
            ("Content-Type", "application/octet-stream"),
            ("Content-Disposition", f'attachment; filename="{filename}"'),
            ("X-Content-Type-Options", "nosniff"),
        ],
    )


def move_backup_to_trash(environ: dict, backup_uuid: str) -> Response:
    user, response = require_admin(environ)
    if response:
        return response
    with connect() as conn:
        row = load_backup(conn, backup_uuid)
        if not row or row["backup_status"] != "available":
            return redirect("/backups")
        config = load_config(conn)
        policy = global_policy(conn)
        source = resolve_inside(config.backup_directory, row["relative_path"])
        target_rel = trash_relative_path(backup_uuid)
        target = resolve_inside(config.trash_directory, target_rel)
        if not source.is_file():
            return redirect(f"/equipment/{row['equipment_id']}")
        atomic_move(source, target)
        conn.execute(
            """
            UPDATE backups
            SET backup_status = 'trashed', deleted_at = CURRENT_TIMESTAMP, deleted_by_user_id = ?,
                trash_relative_path = ?, trash_expires_at = ?
            WHERE uuid = ?
            """,
            (user["id"], target_rel, trash_expiration(policy.trash_retention_days), backup_uuid),
        )
        updated = load_backup(conn, backup_uuid)
        audit(conn, user["id"], "backup.moved_to_trash", "backup", backup_uuid, backup_details_json(updated), environ.get("REMOTE_ADDR", ""))
    return redirect(f"/equipment/{row['equipment_id']}")


def restore_backup(environ: dict, backup_uuid: str) -> Response:
    user, response = require_admin(environ)
    if response:
        return response
    with connect() as conn:
        row = load_backup(conn, backup_uuid)
        if not row or row["backup_status"] != "trashed":
            return redirect("/backups")
        try:
            lifecycle_restore(conn, backup_uuid, user_id=user["id"])
        except ValueError:
            return redirect(f"/equipment/{row['equipment_id']}")
    return redirect(f"/equipment/{row['equipment_id']}")


def _optional_int(form: dict[str, str], key: str, *, required: bool = False) -> int | None:
    value = form.get(key, "").strip()
    if not value and not required:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{key} inválido.") from exc


def lifecycle_page(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response:
        return response
    with connect() as conn:
        policy = global_policy(conn)
        equipment = conn.execute("""SELECT equipment.id,equipment.hostname,retention_policies.max_count,
                    retention_policies.max_age_days,retention_policies.is_enabled FROM equipment
                    LEFT JOIN retention_policies ON retention_policies.equipment_id=equipment.id
                    ORDER BY equipment.hostname""").fetchall()
        trashed = conn.execute("""SELECT backups.uuid,backups.original_filename,backups.file_size,backups.trash_expires_at,
                     equipment.hostname FROM backups JOIN equipment ON equipment.id=backups.equipment_id
                     WHERE backup_status='trashed' ORDER BY deleted_at DESC LIMIT 100""").fetchall()
        latest = conn.execute("SELECT * FROM lifecycle_runs ORDER BY id DESC LIMIT 1").fetchone()
        latest_items = conn.execute("""SELECT lifecycle_items.*,backups.uuid AS backup_uuid,backups.original_filename
                     FROM lifecycle_items LEFT JOIN backups ON backups.id=lifecycle_items.backup_id
                     WHERE run_id=? ORDER BY lifecycle_items.id LIMIT 250""", (latest["id"],)).fetchall() if latest else []
        usage = lifecycle_stats(conn)
    content = lifecycle_page_content(
        conn,
        policy=policy,
        equipment=equipment,
        trashed=trashed,
        latest=latest,
        latest_items=latest_items,
        usage=usage,
        local_dt=local_dt,
        format_size=format_size,
        purge_confirmation=PURGE_CONFIRMATION,
        lifecycle_confirmation=LIFECYCLE_CONFIRMATION,
        empty_trash_confirmation=EMPTY_TRASH_CONFIRMATION,
    )
    return render("Retenção", content, user)


def handle_lifecycle_policy(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ)
    equipment_id = _optional_int(form, "equipment_id")
    try:
        with connect() as conn:
            save_policy(conn, equipment_id=equipment_id,
                        max_count=_optional_int(form, "max_count", required=equipment_id is None),
                        max_age_days=_optional_int(form, "max_age_days", required=equipment_id is None),
                        trash_days=_optional_int(form, "trash_days", required=equipment_id is None),
                        rejected_days=_optional_int(form, "rejected_days", required=equipment_id is None))
            audit(conn, user["id"], "lifecycle.policy_updated", "retention_policy", equipment_id or "global", "", environ.get("REMOTE_ADDR", ""))
    except ValueError as exc:
        return redirect_with_message("/lifecycle", "error", str(exc))
    return redirect_with_message("/lifecycle", "success", "Política salva.")


def handle_lifecycle_simulate(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    with connect() as conn: report = simulate_lifecycle(conn, user_id=user["id"])
    return redirect_with_message("/lifecycle", "info", f"Simulação: {report['candidate_count']} backups, {format_size(report['candidate_bytes'])} estimados; nenhum arquivo alterado.")


def handle_lifecycle_execute(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    try:
        with connect() as conn: report = execute_lifecycle(conn, confirmation=parse_form(environ).get("confirmation", ""), user_id=user["id"])
    except ValueError as exc:
        return redirect_with_message("/lifecycle", "error", str(exc))
    return redirect_with_message("/lifecycle", "success", f"Limpeza: {report['moved_count']} backups movidos para a lixeira.")


def handle_lifecycle_health(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    with connect() as conn: report = lifecycle_health_check(conn, include_hash=True, user_id=user["id"])
    kind = "success" if report["ok"] else "error"
    return redirect_with_message("/lifecycle", kind, f"Integridade: {report['checked']} verificados, {len(report['problems'])} problemas.")


def handle_backup_purge(environ: dict, backup_uuid: str) -> Response:
    user, response = require_admin(environ)
    if response: return response
    try:
        with connect() as conn:
            lifecycle_purge_backup(conn, backup_uuid, confirmation=parse_form(environ).get("confirmation", ""), user_id=user["id"])
    except (OSError, ValueError) as exc:
        return redirect_with_message("/lifecycle", "error", str(exc))
    return redirect_with_message("/lifecycle", "success", "Arquivo apagado definitivamente; registro histórico preservado.")


def handle_empty_trash(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    try:
        with connect() as conn:
            report = lifecycle_empty_trash(conn, confirmation=parse_form(environ).get("confirmation", ""), user_id=user["id"])
    except ValueError as exc:
        return redirect_with_message("/lifecycle", "error", str(exc))
    kind = "error" if report["failures"] else "success"
    return redirect_with_message("/lifecycle", kind,
        f"Lixeira: {report['removed']} arquivos removidos definitivamente, {format_size(report['bytes'])}; {len(report['failures'])} falhas.")


def _notification_badge(value: str) -> str:
    labels = {"configured": "Configurado", "queued": "Aguardando envio", "pending": "Aguardando envio",
              "processing": "Em processamento", "sent": "Enviado", "failed": "Falhou", "retry": "Aguardando nova tentativa",
              "retry_wait": "Aguardando nova tentativa", "test_queued": "Aguardando envio", "not_verified": "Não verificado",
              "not_configured": "Não verificado", "verified": "Verificado", "success": "Enviado"}
    css = "success" if value in {"sent", "success", "verified"} else "failed" if value == "failed" else "queued" if value in {"queued", "pending", "processing", "retry", "retry_wait", "test_queued"} else "neutral"
    return f'<span class="badge status-{css}">{e(labels.get(value, value))}</span>'


def notifications_page(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    with connect() as conn:
        context = notification_page_context(conn)
    content = notifications_page_content(context=context, csrf=session_csrf(environ), local_dt=local_dt, _notification_badge=_notification_badge)
    return render("Notificações", content, user)


def handle_notifications_save(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ)
    if not _valid_settings_csrf(environ, form.get("csrf_token", "")):
        return render("Requisição recusada", "<p>Token CSRF inválido.</p>", user, HTTPStatus.FORBIDDEN)
    try:
        with connect() as conn:
            configure_telegram(conn, enabled=form.get("is_enabled") == "1", token=form.get("token", "").strip(),
                               chat_id=form.get("chat_id", "").strip(), cooldown_minutes=int(form.get("cooldown_minutes", "60")),
                               maintenance_enabled=form.get("maintenance_enabled") == "1", maintenance_start=form.get("maintenance_start", "00:00"),
                               maintenance_end=form.get("maintenance_end", "00:00"), timezone_name=form.get("timezone", "America/Sao_Paulo"),
                               daily_enabled=form.get("daily_enabled") == "1", daily_time=form.get("daily_time", "08:00"),
                               weekly_enabled=form.get("weekly_enabled") == "1", weekly_day=int(form.get("weekly_day", "0")),
                               weekly_time=form.get("weekly_time", "08:00"), user_id=user["id"],
                               executive_enabled=form.get("executive_enabled") == "1", executive_time=form.get("executive_time", "18:00"))
    except (ValueError, TypeError) as exc:
        return redirect_with_message("/settings/notifications", "error", str(exc))
    return redirect_with_message("/settings/notifications", "success", "Configurações salvas; o token não será exibido.")


def notifications_status(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response:
        return response
    with connect() as conn:
        row = conn.execute("""SELECT q.status,q.sent_at,q.updated_at,h.error_code,h.error_description
            FROM notification_queue q LEFT JOIN notification_history h ON h.queue_id=q.id
            WHERE q.event_type='telegram.basic_test' ORDER BY q.id DESC,h.id DESC LIMIT 1""").fetchone()
    if not row:
        return json_response({"status": "not_verified", "terminal": True, "message": "Nenhum teste recente."})
    status = row["status"]
    if status == "sent":
        return json_response({"status": status, "terminal": True,
                              "message": f"Mensagem enviada com sucesso às {local_dt(row['sent_at'])}."})
    if status == "failed":
        reason = row["error_description"] or row["error_code"] or "Falha não detalhada."
        return json_response({"status": status, "terminal": True, "message": f"Não foi possível enviar. Motivo: {reason}"})
    return json_response({"status": status, "terminal": False,
                          "message": "Mensagem adicionada à fila. O envio pode levar até 1 minuto."})


def handle_notifications_test(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ)
    if not _valid_settings_csrf(environ, form.get("csrf_token", "")):
        return render("Requisição recusada", "<p>Token CSRF inválido.</p>", user, HTTPStatus.FORBIDDEN)
    try:
        with connect() as conn:
            if schema_problems(conn):
                raise RuntimeError("notification_schema_outdated")
            queue_basic_test(conn, user_id=user["id"])
            conn.execute("UPDATE notification_channels SET last_test_at=CURRENT_TIMESTAMP,last_status='test_queued' WHERE channel_type='telegram'")
    except ValueError as exc:
        logger.exception("Falha operacional no teste básico do Telegram")
        _audit_notification_test_error(user["id"], environ, type(exc).__name__)
        return redirect_with_message("/settings?tab=notifications", "error", str(exc))
    except Exception as exc:
        logger.exception("Falha inesperada no teste básico do Telegram")
        _audit_notification_test_error(user["id"], environ, type(exc).__name__)
        message = ("É necessário concluir a atualização do banco antes de testar as notificações."
                   if isinstance(exc, sqlite3.OperationalError) or str(exc) == "notification_schema_outdated"
                   else "Não foi possível agendar a mensagem de teste. Tente novamente e consulte o journal se o problema persistir.")
        return redirect_with_message("/settings?tab=notifications", "error", message)
    return redirect_with_message("/settings/notifications", "info", "Mensagem de teste aguardando envio; ela será processada em segundo plano.")


def handle_summary_test(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ)
    if not _valid_settings_csrf(environ, form.get("csrf_token", "")):
        return render("Requisição recusada", "<p>Token CSRF inválido.</p>", user, HTTPStatus.FORBIDDEN)
    try:
        with connect() as conn:
            count = schedule_summary_test(conn, form.get("kind", ""))
            audit(conn, user["id"], "telegram.summary_test_queued", "notification_channel", "telegram",
                  json.dumps({"kind": form.get("kind", ""), "count": count}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
    except (ValueError, sqlite3.Error) as exc:
        logger.exception("Falha ao agendar resumo Telegram de teste")
        return redirect_with_message("/settings?tab=notifications", "error", str(exc) if isinstance(exc, ValueError) else "Não foi possível agendar o resumo de teste.")
    return redirect_with_message("/settings/notifications", "info", "Resumo de teste adicionado à fila. O envio pode levar até 1 minuto.")


def _audit_notification_test_error(user_id: int | None, environ: dict, error_type: str) -> None:
    try:
        with connect() as conn:
            audit(conn, user_id, "telegram.basic_test_failed", "notification_channel", "telegram",
                  json.dumps({"error_type": error_type}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
    except Exception:
        logger.exception("Não foi possível registrar auditoria da falha do teste Telegram")


def parse_days(form: dict[str, str]) -> str:
    values = parse_qs(form.get("_raw", ""))
    days = values.get("schedule_days", [])
    return ",".join(day for day in days if day in {value for value, _ in DAY_LABELS})


def parse_job_form(environ: dict) -> dict[str, str]:
    length = int(environ.get("CONTENT_LENGTH") or 0)
    raw = environ["wsgi.input"].read(length).decode("utf-8")
    values = parse_qs(raw)
    form = {key: vals[-1] for key, vals in values.items()}
    form["_raw"] = raw
    return form


def handle_job_create(environ: dict, equipment_id: int) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    form = parse_job_form(environ)
    schedule_type = form.get("schedule_type", "none")
    queued = None
    with connect() as conn:
        equipment = conn.execute("SELECT id,ssh_backup_driver FROM equipment WHERE id = ? AND is_active = 1", (equipment_id,)).fetchone()
        if not equipment:
            return equipment_detail_page(environ, equipment_id, "Equipamento inválido ou inativo.")
        try:
            method = "ftp" if equipment["ssh_backup_driver"] in {
                "huawei_olt_ssh_ftp", "fiberhome_olt_telnet_ftp", "zte_olt_ssh_ftp",
                "intelbras_gpon_ssh_ftp", "intelbras_epon_ssh_ftp", "vsol_olt_ssh_ftp",
                "vsol_olt_telnet_cli", "parks_olt_100_200_ssh_ftp", "parks_olt_300_400_ssh_ftp",
                "cdata_olt_ssh_ftp"
            } else form.get("method", "dry_run")
            job = create_job(
                conn,
                equipment_id,
                user["id"],
                "scheduled" if schedule_type != "none" else "manual",
                method,
                schedule_type != "none",
                schedule_type,
                form.get("schedule_time", ""),
                parse_days(form),
                form.get("timezone", get_setting(conn, "timezone", "America/Sao_Paulo")),
                form.get("notes", ""),
            )
            if form.get("onboarding") == "ssh" and form.get("method") == "ssh":
                queued = queue_run(conn, job["id"], "manual", user["id"])
        except ValueError as exc:
            return equipment_detail_page(environ, equipment_id, str(exc))
        except Exception:
            return equipment_detail_page(environ, equipment_id, "Não foi possível criar o job. Verifique a configuração informada.")
    if queued is not None:
        return redirect(f"/equipment/{equipment_id}?run={queued['uuid']}")
    return redirect(f"/equipment/{equipment_id}")


def load_job(conn, job_uuid: str):
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", job_uuid or ""):
        return None
    return conn.execute(
        """
        SELECT backup_jobs.*, equipment.hostname
        FROM backup_jobs JOIN equipment ON equipment.id = backup_jobs.equipment_id
        WHERE backup_jobs.uuid = ?
        """,
        (job_uuid,),
    ).fetchone()


def handle_equipment_run_now(environ: dict, equipment_id: int) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    return handle_equipment_run_now_logic(
        connect=connect,
        equipment_id=equipment_id,
        user=user,
        environ=environ,
        form=parse_form(environ),
        create_job=create_job,
        queue_run=queue_run,
        equipment_redirect=equipment_redirect,
        queue_flash=queue_flash,
        get_setting=get_setting,
        redirect=redirect,
    )


def handle_job_run(environ: dict, job_uuid: str) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    with connect() as conn:
        job = load_job(conn, job_uuid)
        if not job:
            return redirect("/jobs")
        try:
            queue_run(conn, job["id"], "manual", user["id"])
        except Exception:
            pass
    return redirect(f"/jobs/{job_uuid}/runs")


def handle_job_status(environ: dict, job_uuid: str, status: str, action: str) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    with connect() as conn:
        job = load_job(conn, job_uuid)
        if job:
            deleted_at = ", deleted_at = CURRENT_TIMESTAMP" if status == "deleted" else ""
            conn.execute(f"UPDATE backup_jobs SET status = ?, updated_at = CURRENT_TIMESTAMP{deleted_at} WHERE uuid = ?", (status, job_uuid))
            updated = load_job(conn, job_uuid)
            audit(conn, user["id"], action, "backup_job", job_uuid, json.dumps({"equipment_id": job["equipment_id"], "job_uuid": job_uuid, "status": updated["status"]}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
    return redirect("/jobs")


def job_edit_page(environ: dict, job_uuid: str, error: str = "") -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    with connect() as conn:
        job = load_job(conn, job_uuid)
        timezone_name = get_setting(conn, "timezone", "America/Sao_Paulo")
    if not job:
        return render("Agendamento não encontrado", "<p>Agendamento não encontrado.</p>", user, HTTPStatus.NOT_FOUND)
    content = f"""<section class="job-edit-page">
    <header class="page-head job-edit-head"><div><span class="eyebrow">Agendamentos</span><h1>Editar agendamento</h1><p>Equipamento: {e(job['hostname'])}</p></div><a class="button secondary" href="/equipment/{job['equipment_id']}#backup">Voltar ao equipamento</a></header>
    {message("error", error)}
    {job_form(job['equipment_id'], job, timezone_name)}
    </section>"""
    return render("Editar job", content, user)


def handle_job_edit(environ: dict, job_uuid: str) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    form = parse_job_form(environ)
    with connect() as conn:
        job = load_job(conn, job_uuid)
        if not job:
            return redirect("/jobs")
        try:
            driver = conn.execute("SELECT ssh_backup_driver FROM equipment WHERE id=?", (job["equipment_id"],)).fetchone()[0]
            method = "ftp" if driver in {
                "huawei_olt_ssh_ftp", "fiberhome_olt_telnet_ftp", "zte_olt_ssh_ftp",
                "intelbras_gpon_ssh_ftp", "intelbras_epon_ssh_ftp", "vsol_olt_ssh_ftp",
                "vsol_olt_telnet_cli", "parks_olt_100_200_ssh_ftp", "parks_olt_300_400_ssh_ftp",
                "cdata_olt_ssh_ftp"
            } else form.get("method", "dry_run")
            update_job(
                conn,
                job["id"],
                user["id"],
                method,
                form.get("schedule_type", "none") != "none",
                form.get("schedule_type", "none"),
                form.get("schedule_time", ""),
                parse_days(form),
                form.get("timezone", get_setting(conn, "timezone", "America/Sao_Paulo")),
                form.get("notes", ""),
            )
        except ValueError as exc:
            return job_edit_page(environ, job_uuid, str(exc))
        except Exception:
            return job_edit_page(environ, job_uuid, "Não foi possível atualizar o job. Verifique os dados informados.")
    return redirect(f"/equipment/{job['equipment_id']}#backup")


def jobs_page(environ: dict) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    query = parse_qs(environ.get("QUERY_STRING", ""))
    filters = {key: values[-1].strip() for key, values in query.items() if values}
    with connect() as conn:
        context = jobs_page_context(conn, filters)
    rows = context["rows"]
    equipment = context["equipment"]
    environments = context["environments"]
    groups = context["groups"]
    vendors = context["vendors"]
    pops = context["pops"]
    method_labels = {"ssh": "Backup via SSH", "dry_run": "Simulação de teste", "manual": "Execução manual", "ftp": "Recebimento via FTP", "sftp": "SFTP", "tftp": "TFTP"}
    method_opts = "".join(f'<option value="{item}" {"selected" if filters.get("method") == item else ""}>{method_labels.get(item, item.upper())}</option>' for item in JOB_METHODS if item != "api")
    status_opts = "".join(f'<option value="{item}" {"selected" if filters.get("status") == item else ""}>{"Ativo" if item == "active" else "Pausado" if item == "paused" else item.title()}</option>' for item in JOB_STATUSES)
    schedule_opts = "".join(f'<option value="{item}" {"selected" if filters.get("schedule_type") == item else ""}>{"Execução manual" if item == "none" else "Todos os dias" if item == "daily" else "Dias da semana"}</option>' for item in SCHEDULE_TYPES)
    active_count = sum(1 for row in rows if row["status"] == "active")
    automatic_count = sum(1 for row in rows if row["schedule_enabled"] and row["schedule_type"] != "none" and row["status"] == "active")
    content = f"""
    <section class="jobs-modern-page"><header class="page-head"><div><span class="eyebrow">Automação</span><h1>Agendamentos de backup</h1><p>Configure e acompanhe as rotinas automáticas dos equipamentos.</p></div><a class="button secondary job-runs-icon-button" href="/job-runs"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg><span>Ver execuções</span></a></header><section class="jobs-modern-summary"><article><span class="jobs-modern-summary-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 3v3m12-3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v14H4V6a1 1 0 0 1 1-1Z"/></svg></span><div><span>Agendamentos exibidos</span><strong>{len(rows)}</strong></div></article><article><span class="jobs-modern-summary-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/></svg></span><div><span>Ativos</span><strong>{active_count}</strong></div></article><article><span class="jobs-modern-summary-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 7h16M7 4v6m10-6v6M5 11h14v9H5Z"/><path d="m9 15 2 2 4-4"/></svg></span><div><span>Automáticos</span><strong>{automatic_count}</strong></div></article></section>
    <form method="get" action="/jobs" class="panel filters jobs-modern-filters">
      <div class="jobs-filter-main"><label>Equipamento<select name="equipment_id"><option value="">Todos os equipamentos</option>{option_rows(equipment, filters.get('equipment_id', ''))}</select></label><label>Método<select name="method"><option value="">Todos os métodos</option>{method_opts}</select></label><label>Status<select name="status"><option value="">Todos os status</option>{status_opts}</select></label><label>Frequência<select name="schedule_type"><option value="">Todas as frequências</option>{schedule_opts}</select></label><details class="jobs-more-filters"><summary>Mais filtros</summary><div><label>Ambiente<select name="environment_id"><option value="">Todos</option>{option_rows(environments, filters.get('environment_id', ''))}</select></label><label>Grupo<select name="group_id"><option value="">Todos</option>{option_rows(groups, filters.get('group_id', ''))}</select></label><label>Fabricante<select name="vendor_id"><option value="">Todos</option>{option_rows(vendors, filters.get('vendor_id', ''))}</select></label><label>POP<select name="pop_id"><option value="">Todos</option>{option_rows(pops, filters.get('pop_id', ''))}</select></label></div></details><button class="job-runs-icon-button" type="submit"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 5h16M7 12h10m-7 7h4"/></svg><span>Filtrar</span></button></div>
    </form>
    <section class="panel table-panel jobs-modern-table">{job_table(rows, user)}</section></section>
    """
    return render("Agendamentos", content, user)


def job_runs_page(environ: dict, job_uuid: str | None = None) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    query = parse_qs(environ.get("QUERY_STRING", ""))
    filters = {key: values[-1].strip() for key, values in query.items() if values}
    with connect() as conn:
        context = job_runs_page_context(conn, filters, job_uuid=job_uuid)
    rows = context["rows"]
    equipment = context["equipment"]
    status_labels = {"queued": "Na fila", "running": "Em execução", "success": "Sucesso", "failed": "Falha", "cancelled": "Cancelada"}
    trigger_labels = {"manual": "Execução manual", "scheduled": "Agendamento", "retry": "Nova tentativa", "system": "Sistema"}
    method_labels = {"ssh": "Backup via SSH", "dry_run": "Simulação de teste", "manual": "Execução manual", "ftp": "Recebimento via FTP"}
    status_opts = "".join(f'<option value="{item}" {"selected" if filters.get("status") == item else ""}>{status_labels.get(item, item.replace("_", " ").title())}</option>' for item in RUN_STATUSES)
    trigger_opts = "".join(f'<option value="{item}" {"selected" if filters.get("trigger") == item else ""}>{trigger_labels.get(item, item.replace("_", " ").title())}</option>' for item in TRIGGER_TYPES)
    run_rows = "".join(
        f'''<tr>{'' if job_uuid else f'<td><a href="/equipment/{row["equipment_id"]}">{e(row["hostname"])}</a></td>'}<td>{e(local_dt(row['created_at']))}</td><td>{e(trigger_labels.get(row['trigger_type'], str(row['trigger_type']).title()))}</td><td>{e('Backup via Telnet' if row['ssh_backup_driver']=='vsol_olt_telnet_cli' else method_labels.get(row['method'], str(row['method']).upper()))}</td><td><span class="badge status-{'success' if row['status'] == 'success' else 'error' if row['status'] == 'failed' else 'warning'}">{e(status_labels.get(row['status'], str(row['status']).title()))}</span></td><td>{e(str(row['duration_ms']) + ' ms' if row['duration_ms'] is not None else 'Não concluída')}</td><td>{e(row['error_code'] or 'Nenhum erro')}</td><td><a class="job-runs-detail-button" href="/job-runs/{e(row['uuid'])}" aria-label="Ver detalhes" title="Ver detalhes"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="3"/></svg></a></td></tr>'''
        for row in rows
    ) or f'<tr><td colspan="{7 if job_uuid else 8}" class="muted">Nenhuma execução encontrada para os filtros selecionados.</td></tr>'
    success_count = sum(1 for row in rows if row["status"] == "success")
    failed_count = sum(1 for row in rows if row["status"] == "failed")
    selected_equipment_id = rows[0]["equipment_id"] if job_uuid and rows else None
    selected_hostname = rows[0]["hostname"] if job_uuid and rows else ""
    back_url = f"/equipment/{selected_equipment_id}#backup" if selected_equipment_id else "/jobs"
    table_head = f'''<tr>{'' if job_uuid else '<th>Equipamento</th>'}<th>Data e hora</th><th>Origem</th><th>Método</th><th>Status</th><th>Duração</th><th>Resultado</th><th>Ação</th></tr>'''
    content = f"""
    <section class="job-runs-modern"><header class="page-head"><div><span class="eyebrow">Histórico de execuções</span><h1>Execuções de backup</h1><p>{f'Agendamento do equipamento {e(selected_hostname)}.' if selected_hostname else 'Consulte as execuções de backup registradas no sistema.'}</p></div><a class="button secondary job-runs-icon-button" href="{back_url}"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="m14 6-6 6 6 6"/></svg><span>{'Voltar ao equipamento' if selected_equipment_id else 'Voltar aos agendamentos'}</span></a></header>
    <section class="job-runs-summary"><article><span class="job-runs-summary-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg></span><div><span>Execuções exibidas</span><strong>{len(rows)}</strong></div></article><article class="success"><span class="job-runs-summary-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/></svg></span><div><span>Concluídas</span><strong>{success_count}</strong></div></article><article class="error"><span class="job-runs-summary-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 9v5m0 3h.01"/></svg></span><div><span>Com falha</span><strong>{failed_count}</strong></div></article></section>
    <form method="get" action="{'/jobs/' + job_uuid + '/runs' if job_uuid else '/job-runs'}" class="panel job-runs-filters">{'' if job_uuid else f'<label>Equipamento<select name="equipment_id"><option value="">Todos</option>{option_rows(equipment, filters.get("equipment_id", ""))}</select></label>'}<label>Status<select name="status"><option value="">Todos os status</option>{status_opts}</select></label><label>Origem<select name="trigger"><option value="">Todas as origens</option>{trigger_opts}</select></label><label>Data inicial<input type="date" name="start" value="{e(filters.get('start', ''))}"></label><label>Data final<input type="date" name="end" value="{e(filters.get('end', ''))}"></label><button class="job-runs-icon-button" type="submit"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 5h16M7 12h10m-7 7h4"/></svg><span>Filtrar</span></button></form>
    <section class="panel table-panel job-runs-table"><table><thead>{table_head}</thead><tbody>{run_rows}</tbody></table></section></section>
    """
    return render("Execuções", content, user)


def run_detail_page(environ: dict, run_uuid: str) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    with connect() as conn:
        context = run_detail_context(conn, run_uuid)
    row = context["row"]
    if not row:
        return render("Execução não encontrada", "<p>Execução não encontrada.</p>", user, HTTPStatus.NOT_FOUND)
    status_label = {"success": "Sucesso", "failed": "Falha", "running": "Em execução", "queued": "Na fila", "timeout": "Tempo esgotado", "cancelled": "Cancelada"}.get(row["status"], row["status"])
    status_class = "success" if row["status"] == "success" else "warning" if row["status"] in {"queued", "running"} else "error"
    origin_label = {"manual": "Execução manual", "scheduled": "Agendamento automático", "retry": "Nova tentativa", "system": "Sistema"}.get(row["trigger_type"], row["trigger_type"])
    method_label = ("Telnet" if row["ssh_backup_driver"] == "vsol_olt_telnet_cli" else
                    {"ssh": "SSH", "ftp": "FTP", "manual": "Importação manual", "dry_run": "Simulação"}.get(row["method"], row["method"].upper()))
    log_steps = [part.strip() for part in (row["safe_log"] or "").split(".") if part.strip()]
    log_html = "".join(f'<li><span>✓</span><p>{e(step)}.</p></li>' for step in log_steps) or '<li><span>–</span><p>Nenhum log seguro registrado.</p></li>'
    content = f"""<section class="run-detail-page"><nav class="breadcrumbs ftp-wizard-breadcrumbs"><a href="/">Início</a><span>/</span><a href="/equipment/{row['equipment_id']}#backup">{e(row['hostname'])}</a><span>/</span><a href="/jobs/{e(row['job_uuid'])}/runs">Execuções</a><span>/</span><strong>Detalhes</strong></nav>
      <header class="run-detail-hero"><div><span class="eyebrow">Execução de backup</span><h1>Detalhes da execução</h1><p>{e(row['hostname'])} · execução {e(row['uuid'])[:8]}</p></div><div class="actions"><span class="badge status-{status_class}">{e(status_label)}</span><a class="button secondary" href="/jobs/{e(row['job_uuid'])}/runs">Voltar às execuções</a></div></header>
      <section class="run-detail-summary"><article><span class="run-detail-summary-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/></svg></span><div><span>Status</span><strong>{e(status_label)}</strong><small>{e(origin_label)}</small></div></article><article><span class="run-detail-summary-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m7 10 3 3-3 3m5 0h5"/></svg></span><div><span>Método</span><strong>{e(method_label)}</strong><small>Agendamento {e(row['job_uuid'])[:8]}</small></div></article><article><span class="run-detail-summary-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg></span><div><span>Duração</span><strong>{e(str(row['duration_ms']) + ' ms' if row['duration_ms'] is not None else 'Não disponível')}</strong><small>{e(local_dt(row['started_at']))}</small></div></article><article><span class="run-detail-summary-icon"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 2h9l4 4v16H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z"/><path d="M14 2v5h5M8 13h8M8 17h6"/></svg></span><div><span>Resultado</span><strong>{'Concluído com sucesso' if row['status'] == 'success' else 'Requer atenção'}</strong><small>{e(row['error_code'] or 'Nenhum erro registrado')}</small></div></article></section>
      <section class="run-detail-grid"><article class="panel"><h2>Informações da execução</h2><dl class="run-detail-facts"><dt>Identificador da execução</dt><dd>{e(row['uuid'])}</dd><dt>Identificador do agendamento</dt><dd>{e(row['job_uuid'])}</dd><dt>Como foi iniciada</dt><dd>{e(origin_label)}</dd><dt>Método utilizado</dt><dd>{e(method_label)}</dd><dt>Início da execução</dt><dd>{e(local_dt(row['started_at']))}</dd><dt>Conclusão</dt><dd>{e(local_dt(row['finished_at']))}</dd><dt>Código do erro</dt><dd>{e(row['error_code'] or 'Nenhum erro registrado')}</dd><dt>Mensagem do resultado</dt><dd>{e(row['error_message'] or ('Backup concluído normalmente.' if row['status'] == 'success' else 'Nenhuma mensagem disponível.'))}</dd></dl></article>
      <article class="panel run-detail-log"><h2>Log seguro</h2><p>Etapas registradas durante a execução, sem exposição de dados sensíveis.</p><ol>{log_html}</ol></article></section></section>"""
    return render("Detalhes da execução", content, user)


def run_status_endpoint(environ: dict, run_uuid: str) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    with connect() as conn:
        row = conn.execute(
            """
            SELECT backup_job_runs.*, backups.uuid AS created_backup_uuid,
                   backups.original_filename AS created_backup_name,
                   backups.file_size AS created_backup_size
            FROM backup_job_runs
            LEFT JOIN backups ON backups.id = backup_job_runs.created_backup_id
            WHERE backup_job_runs.uuid = ?
            """,
            (run_uuid,),
        ).fetchone()
    if not row:
        return json_response({"error": "not_found"}, HTTPStatus.NOT_FOUND)
    return json_response(run_status_payload(row))


def _positive_int(value: str, label: str) -> int | None:
    if not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise FTPAccountError(f"{label} inválido.") from exc
    if parsed <= 0:
        raise FTPAccountError(f"{label} deve ser positivo.")
    return parsed


def _mikrotik_script_response(user, *, title: str, script: str, version: int) -> Response:
    content = f'''<section class="script-guide-page"><header class="page-head script-guide-head"><div><span class="eyebrow">MikroTik FTP Push</span><h1>{e(title)}</h1><p>Instale a configuração no terminal do equipamento e valide o envio separadamente.</p></div><span class="badge status-warning">RouterOS v{version}</span></header>
      <div class="notice warning">FTP não utiliza criptografia. Use esta integração somente em rede privada, VPN ou ambiente controlado.</div>
      <section class="script-guide-grid"><article class="panel script-guide-steps"><span class="eyebrow">Como executar</span><h2>Etapas no equipamento</h2><ol><li>Abra o terminal do MikroTik.</li><li>Copie o script ao lado.</li><li>Cole no terminal.</li><li>Aguarde a conclusão.</li><li>Volte à tela do equipamento.</li><li>Verifique a instalação.</li><li>Teste o envio FTP separadamente.</li></ol><p><strong>Execute o script no terminal do MikroTik e confira o resultado. Ele não executa o teste FTP.</strong></p></article>
      <article class="panel script-guide-code"><header><div><span class="eyebrow">Script gerado</span><h2>Comandos RouterOS</h2></div><button type="button" data-copy-target="mikrotik-generated-script">Copiar script</button></header><pre><code data-copy-source="mikrotik-generated-script">{e(script)}</code></pre><footer><button type="button" class="secondary" onclick="window.close()">Fechar aba</button><a class="button secondary" href="javascript:history.back()">Voltar</a></footer></article></section></section>'''
    return Response(layout(title, content, user), HTTPStatus.CREATED, [
        ("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store, no-cache, must-revalidate"), ("Pragma", "no-cache")
    ])


def handle_olt_manual_script(environ: dict, equipment_id: int) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    form = parse_form(environ)
    if not valid_session_csrf(environ, form.get("csrf_token", "")):
        return render("Requisição recusada", "<p>Token CSRF inválido.</p>", user, HTTPStatus.FORBIDDEN)
    action = form.get("action", "manual")
    if action not in {"manual", "install"}:
        action = "manual"
    password = ""
    try:
        with connect() as conn:
            equipment = conn.execute("SELECT * FROM equipment WHERE id=? AND is_active=1", (equipment_id,)).fetchone()
            account = conn.execute(
                """SELECT * FROM ftp_accounts WHERE equipment_id=? AND is_active=1
                   AND deleted_at IS NULL ORDER BY updated_at DESC,id DESC LIMIT 1""",
                (equipment_id,),
            ).fetchone()
            ftp_host = get_setting(conn, "ftp_public_ip") or get_setting(conn, "ftp_public_ipv6")
            ftp_port = int(account["control_port"] or get_setting(conn, "ftp_control_port", "21")) if account else 21
        if not equipment:
            raise OLTBackupError("Equipamento ausente ou inativo.")
        if not is_ftp_push_olt_driver(equipment["ssh_backup_driver"] or ""):
            raise OLTBackupError("O equipamento não utiliza um método OLT via FTP.")
        if not account:
            raise OLTBackupError("Crie e ative uma conta FTP vinculada ao equipamento.")
        reference = account["password_hash_or_secret_reference"] or ""
        if not reference.startswith("fernet:"):
            raise OLTBackupError("A senha desta conta FTP não é recuperável. Redefina a senha da conta.")
        password = decrypt_secret(reference.removeprefix("fernet:"))
        plan = build_plan(OLTBackupRequest(
            equipment["ssh_backup_driver"], equipment["hostname"], ftp_host, ftp_port, "/",
            account["username"], password, action,
        ))
        script = "\n".join(plan.commands)
        expected = ", ".join(plan.expected_files) or "configuração automática instalada"
        interactive = "<p><strong>Este modelo possui prompts interativos. Envie cada linha somente quando o prompt correspondente aparecer.</strong></p>" if plan.interactive else ""
        content = f'''<section class="script-guide-page"><header class="page-head script-guide-head"><div><span class="eyebrow">Módulo OLT</span><h1>Roteiro manual de backup</h1><p>Comandos preparados para {e(equipment['hostname'])}.</p></div><span class="badge status-warning">{e(plan.transport.upper())}</span></header>
          <div class="notice warning">O roteiro contém a senha FTP em texto visível. Use somente em ambiente controlado e feche esta aba após executar.</div>
          <section class="script-guide-grid"><article class="panel script-guide-steps"><span class="eyebrow">Resumo operacional</span><h2>Destino do backup</h2><dl class="details"><dt>Equipamento</dt><dd>{e(equipment['hostname'])}</dd>
          <dt>Método</dt><dd>{e(SSH_DRIVER_LABELS.get(equipment['ssh_backup_driver'], equipment['ssh_backup_driver']))}</dd>
          <dt>Transporte</dt><dd>{e(plan.transport.upper())}</dd><dt>Resultado esperado</dt><dd>{e(expected)}</dd></dl>{interactive}</article>
          <article class="panel script-guide-code"><header><div><span class="eyebrow">Roteiro gerado</span><h2>Comandos da OLT</h2></div><button type="button" data-copy-target="olt-generated-script">Copiar roteiro</button></header><pre><code data-copy-source="olt-generated-script">{e(script)}</code></pre><footer>
          <button type="button" class="secondary" onclick="window.close()">Fechar aba</button>
          <a class="button secondary" href="/equipment/{equipment_id}#backup">Voltar</a></footer></article></section></section>'''
        return Response(layout("Roteiro manual OLT", content, user), HTTPStatus.CREATED, [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Cache-Control", "no-store, no-cache, must-revalidate"),
            ("Pragma", "no-cache"),
        ])
    except (OLTBackupError, SecretKeyError, ValueError) as exc:
        return equipment_detail_page(environ, equipment_id, str(exc))
    finally:
        password = ""


def handle_olt_execute(environ: dict, equipment_id: int) -> Response:
    user, response = require_operator(environ)
    if response:
        return response
    form = parse_form(environ)
    if not valid_session_csrf(environ, form.get("csrf_token", "")):
        return render("Requisição recusada", "<p>Token CSRF inválido.</p>", user, HTTPStatus.FORBIDDEN)
    action = form.get("action", "manual") if form.get("action") in {"manual", "install"} else "manual"
    try:
        trigger_error = None
        with connect() as conn:
            equipment = conn.execute("SELECT * FROM equipment WHERE id=?", (equipment_id,)).fetchone()
            try:
                plan, _, result = trigger_olt_backup(conn, equipment_id, action=action)
            except Exception as exc:
                trigger_error = exc
        if trigger_error is not None:
            raise trigger_error
        expected = ", ".join(result.get("expected_files", plan.expected_files))
        with connect() as conn:
            audit(conn, user["id"], "olt.backup_triggered", "equipment", equipment_id,
                  json.dumps({"driver": equipment["ssh_backup_driver"], "transport": plan.transport,
                              "action": action, "expected_files": list(plan.expected_files)}, separators=(",", ":")),
                  environ.get("REMOTE_ADDR", ""))
        return redirect_with_message(
            f"/equipment/{equipment_id}#backup", "success",
            f"Comandos enviados à OLT. Arquivos esperados: {expected or 'configuração automática instalada'}.",
        )
    except (OLTBackupError, SSHBackupError, SecretKeyError, ValueError) as exc:
        return equipment_detail_page(environ, equipment_id, str(exc))


def handle_mikrotik_ftp_create(environ: dict, equipment_id: int) -> Response:
    user, response = require_operator(environ)
    if response: return response
    form = parse_form(environ)
    try:
        with connect() as conn:
            previous_account = conn.execute(
                "SELECT * FROM ftp_accounts WHERE equipment_id=? AND is_active=1 AND deleted_at IS NULL",
                (equipment_id,),
            ).fetchone()
            previous_account = dict(previous_account) if previous_account else None
            result = handle_mikrotik_ftp_create_logic(
                conn=conn,
                user=user,
                equipment_id=equipment_id,
                form=form,
                previous_account=previous_account,
                create_integration=create_mikrotik_integration,
                mikrotik_integration_config=mikrotik_integration_config,
                generate_install_script=generate_mikrotik_install_script,
                run_helper=run_helper,
                audit=audit,
                redirect_with_message=redirect_with_message,
                mikrotik_script_response=_mikrotik_script_response,
                equipment_detail_page=equipment_detail_page,
                environ=environ,
            )
            return result
    except (ValueError, FTPAccountError) as exc:
        return equipment_detail_page(environ, equipment_id, str(exc))


FTP_TIMELINE_STEPS = (
    "Teste iniciado", "Arquivo criado no MikroTik", "Comando FTP executado",
    "Arquivo recebido no servidor", "Arquivo validado", "Limpeza concluída", "Teste concluído",
)


def _mikrotik_ftp_test_error_code(message: str, fallback: str) -> str:
    lowered = (message or "").casefold()
    rules = (
        ("backup file unavailable", "backup_creation_failed"),
        ("backup file empty", "backup_validation_failed"),
        ("export file unavailable", "export_creation_failed"),
        ("export file empty", "export_validation_failed"),
        ("unsupported clock date format", "clock_format_failed"),
        ("invalid normalized date", "clock_format_failed"),
        ("invalid clock day", "clock_format_failed"),
        ("invalid clock year", "clock_format_failed"),
        ("unknown clock month", "clock_format_failed"),
        ("backup-manager already running", "script_busy"),
        ("falha na limpeza", "cleanup_failed"),
        ("cleanup failed", "cleanup_failed"),
        ("ftp upload", "ftp_upload_failed"),
        ("autenticação ftp recusada", "FTP_AUTH_FAILED"),
    )
    for marker, code in rules:
        if marker in lowered:
            return code
    return fallback


def _timeline(steps, completed: int, failed: int | None = None) -> list[dict]:
    terminal = failed is not None
    return [{"step": step, "status": "failed" if index == failed else
             ("completed" if index < completed else ("not_executed" if terminal else "pending"))}
            for index, step in enumerate(steps)]


def _ftp_operation_payload(test) -> dict:
    status = test["status"]
    progress = {"pending": 1, "running": 2, "waiting_upload": 3, "validating": 4,
                "validated": 7, "expired": 3, "cancelled": 1,
                "failed": 2}.get(status, 1)
    messages = {
        "pending": "Teste FTP iniciado.",
        "waiting_upload": "O MikroTik concluiu o envio. Aguardando o arquivo no servidor.",
        "validating": "Arquivo recebido no servidor. Validação em andamento.",
        "validated": "Envio FTP validado com sucesso.",
        "expired": "O MikroTik concluiu a tentativa de envio, mas o servidor não recebeu o arquivo dentro do prazo.",
        "cancelled": "Teste FTP cancelado.",
        "failed": "O MikroTik não conseguiu executar o envio FTP.",
    }
    terminal = status in FINAL_TEST_STATES
    failed_at = progress if terminal and status != "validated" else None
    timeline = _timeline(FTP_TIMELINE_STEPS, progress, failed_at)
    if status == "expired":
        timeline = _timeline(FTP_TIMELINE_STEPS, 3, 3)
        timeline[4]["status"] = "not_executed"
        timeline[5]["status"] = "completed"
        timeline[6]["status"] = "failed"
    elif status == "validated":
        timeline = _timeline(FTP_TIMELINE_STEPS, len(FTP_TIMELINE_STEPS))
    message = messages.get(status, test["error_message"] or "Estado do teste FTP atualizado.")
    if status == "failed" and test["error_message"] and test["error_code"] in {
        "backup_creation_failed", "backup_validation_failed", "export_creation_failed", "export_validation_failed",
        "clock_format_failed", "cleanup_failed", "ftp_upload_failed", "script_busy", "validation_failed",
        "ROUTEROS_SCRIPT_FAILED", "ROUTEROS_SYNTAX_ERROR", "FTP_AUTH_FAILED",
    }:
        message = test["error_message"]
    recommendations = {
        "FTP_AUTH_FAILED": "Gere uma nova senha FTP e atualize a configuração do equipamento.",
        "upload_timeout": "Verifique endereço, porta, firewall e acesso do equipamento ao servidor FTP.",
        "cleanup_failed": "Tente novamente; se persistir, remova os arquivos temporários pelo diagnóstico avançado.",
    }
    return {
        "ok": status == "validated" if terminal else True,
        "status": status,
        "canonical_status": canonical_operation_state("mikrotik_ftp_tests", status),
        "message": message,
        "error_code": test["error_code"], "operation_id": test["id"],
        "operation_uuid": test["uuid"], "terminal": terminal,
        "user_status": "Funcionando" if status == "validated" else ("Com problema" if terminal else "Testando"),
        "technical_detail": test["error_code"] or "",
        "action_recommendation": recommendations.get(test["error_code"], "Tente novamente ou consulte o diagnóstico técnico." if terminal and status != "validated" else ""),
        "retry_allowed": terminal and status in {"failed", "expired"},
        "timeline": timeline, "started_at": test["started_at"], "expires_at": test["expires_at"],
        "finished_at": test["completed_at"],
        "updated_at": test["updated_at"], "last_check_at": test["last_check_at"],
        "expected_filename": test["expected_filename"],
    }


def handle_mikrotik_ftp_test(environ: dict, integration_id: int) -> Response:
    user, response = require_operator(environ)
    if response: return response
    try:
        with connect() as conn:
            integration, equipment = require_mikrotik_integration(conn, integration_id)
            test = create_mikrotik_test(conn, integration_id)
            config = mikrotik_integration_config(conn, integration, include_secret=True)
            credential = conn.execute("""SELECT * FROM device_credentials WHERE equipment_id=? AND credential_type='ssh'
                AND is_active=1 AND deleted_at IS NULL ORDER BY updated_at DESC,id DESC LIMIT 1""", (integration["equipment_id"],)).fetchone()
            connect_timeout = int(get_setting(conn, "ssh_connect_timeout_seconds", "15"))
            command_timeout = int(get_setting(conn, "ssh_command_timeout_seconds", "60"))
            audit(conn, user["id"], "mikrotik_ftp.test_started", "mikrotik_ftp_test", test["uuid"], json.dumps({"integration_id": integration_id, "timeline": list(FTP_TIMELINE_STEPS)}), environ.get("REMOTE_ADDR", ""))
            conn.commit()
        if not equipment or not credential:
            raise ValueError("Instalação ou credencial SSH indisponível para executar o teste FTP.")
        run_routeros_ftp_test(dict(equipment), dict(credential), config,
                              connect_timeout, command_timeout)
        with connect() as conn:
            current = conn.execute("SELECT * FROM mikrotik_ftp_tests WHERE id=?", (test["id"],)).fetchone()
            return operation_response(environ, conn, integration["equipment_id"], _ftp_operation_payload(current), HTTPStatus.ACCEPTED)
    except (ValueError, SSHBackupError) as exc:
        with connect() as conn:
            if 'test' in locals():
                raw_message = ("Autenticação FTP recusada. Verifique o usuário e a senha FTP configurados."
                               if isinstance(exc, SSHBackupError) and exc.code == "FTP_AUTH_FAILED" else str(exc))
                message_text = sanitize_mikrotik_error(raw_message)
                error_code = _mikrotik_ftp_test_error_code(
                    message_text,
                    exc.code if isinstance(exc, SSHBackupError) else "validation_failed",
                )
                conn.execute("UPDATE mikrotik_ftp_tests SET status='failed',error_code=?,error_message=?,completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (error_code, message_text[:240], test["id"]))
                current = conn.execute("SELECT * FROM mikrotik_ftp_tests WHERE id=?", (test["id"],)).fetchone()
                return operation_response(environ, conn, integration["equipment_id"], _ftp_operation_payload(current), HTTPStatus.BAD_GATEWAY)
        payload = {"ok": False, "status": "failed", "message": str(exc),
                   "error_code": exc.code if isinstance(exc, SSHBackupError) else "validation_failed",
                   "operation_id": None, "terminal": True, "timeline": _timeline(FTP_TIMELINE_STEPS, 0, 0)}
        if wants_json(environ): return json_response(payload, HTTPStatus.BAD_REQUEST)
        return redirect_with_message("/equipment", "error", str(exc))


def mikrotik_ftp_test_status(environ: dict, test_uuid: str) -> Response:
    user, response = require_user(environ)
    if response: return response
    with connect() as conn:
        test = conn.execute("""SELECT t.*,i.equipment_id FROM mikrotik_ftp_tests t JOIN mikrotik_ftp_integrations i ON i.id=t.integration_id WHERE t.uuid=?""", (test_uuid,)).fetchone()
        if not test: return json_response({"ok": False, "status": "failed", "message": "Teste FTP não encontrado.", "error_code": "not_found", "terminal": True, "timeline": []}, HTTPStatus.NOT_FOUND)
        equipment_id = test["equipment_id"]
        test = expire_mikrotik_test(conn, test["id"])
    if wants_json(environ):
        payload = _ftp_operation_payload(test)
        logger.info("mikrotik polling equipment_id=%s operation_id=%s status=%s terminal=%s",
                    equipment_id, test["id"], payload["status"], payload["terminal"])
        return json_response(payload)
    return redirect_with_message(f"/equipment/{equipment_id}", "success" if test["status"] == "validated" else "info", f"Estado do teste FTP Push: {test['status']}.")


def handle_mikrotik_ftp_rotate(environ: dict, integration_id: int) -> Response:
    user, response = require_operator(environ)
    if response: return response
    try:
        with connect() as conn:
            require_mikrotik_integration(conn, integration_id)
            old, password, _ = rotate_mikrotik_credential(conn, integration_id)
            integration = conn.execute("SELECT * FROM mikrotik_ftp_integrations WHERE id=?", (integration_id,)).fetchone()
            account = conn.execute("SELECT * FROM ftp_accounts WHERE id=?", (integration["ftp_account_id"],)).fetchone()
            config = mikrotik_integration_config(conn, integration, include_secret=True)
            script = generate_mikrotik_install_script(config, include_secret=True)
            audit(conn, user["id"], "mikrotik_ftp.credential_rotated", "mikrotik_ftp_integration", integration["uuid"], "{}", environ.get("REMOTE_ADDR", ""))
    except (ValueError, SecretKeyError) as exc:
        return redirect_with_message("/equipment", "error", str(exc))
    ok, detail = run_helper("create-or-update-account", account["id"], password)
    with connect() as conn:
        conn.execute("UPDATE ftp_accounts SET sync_status=?,sync_error=? WHERE id=?", ("synced" if ok else "failed", "" if ok else detail, account["id"]))
    if "text/plain" in (environ.get("HTTP_ACCEPT") or "").lower():
        return Response(script, HTTPStatus.OK, [("Content-Type", "text/plain; charset=utf-8"),
            ("Cache-Control", "no-store"), ("Pragma", "no-cache")])
    return _mikrotik_script_response(user, title="Credencial rotacionada e novo script", script=script,
                                     version=integration["routeros_major_version"])


def handle_mikrotik_ftp_install_ssh(environ: dict, integration_id: int, *, repair: bool = False) -> Response:
    user, response = require_operator(environ)
    if response: return response
    with connect() as conn:
        result = install_integration_via_ssh(conn, integration_id, repair=repair, installer=install_routeros_script)
        if not result["found"]:
            payload = result["payload"]
            return json_response(payload, HTTPStatus.NOT_FOUND) if wants_json(environ) else redirect_with_message("/equipment", "error", payload["message"])
        action = "mikrotik_ftp.installation_checked" if result["payload"]["status"] == "installed_valid" else ("mikrotik_ftp.installation_repaired" if repair else "mikrotik_ftp.ssh_installed")
        if result["payload"]["status"] == "failed":
            action = "mikrotik_ftp.ssh_install_failed"
        operation_id = audit(
            conn, user["id"], action, "mikrotik_ftp_integration", result["integration_uuid"],
            json.dumps(result["details"], separators=(",", ":")), environ.get("REMOTE_ADDR", ""),
        )
        payload = dict(result["payload"])
        payload["operation_id"] = operation_id
        status = HTTPStatus.BAD_GATEWAY if payload["status"] == "failed" else HTTPStatus.OK
        return operation_response(environ, conn, result["equipment_id"], payload, status)


def handle_mikrotik_ftp_execute_ssh(environ: dict, integration_id: int) -> Response:
    user, response = require_operator(environ)
    if response: return response
    with connect() as conn:
        try:
            integration, equipment = require_mikrotik_integration(conn, integration_id)
        except ValueError as exc:
            return json_response({"ok": False, "status": "failed", "message": str(exc), "error_code": "equipment_inconsistent", "timeline": []}, HTTPStatus.BAD_REQUEST)
        credential = conn.execute("""SELECT * FROM device_credentials WHERE equipment_id=? AND credential_type='ssh'
            AND is_active=1 AND deleted_at IS NULL ORDER BY updated_at DESC,id DESC LIMIT 1""", (integration["equipment_id"],)).fetchone()
        try:
            if not equipment or not credential:
                raise SSHBackupError("SSH_CREDENTIAL_MISSING")
            config = mikrotik_integration_config(conn, integration, include_secret=True)
            connect_timeout = int(conn.execute("SELECT value FROM settings WHERE key='ssh_connect_timeout_seconds'").fetchone()[0] or 15)
            command_timeout = int(conn.execute("SELECT value FROM settings WHERE key='ssh_command_timeout_seconds'").fetchone()[0] or 60)
            baseline_upload_id = int(conn.execute(
                "SELECT COALESCE(MAX(id),0) FROM mikrotik_ftp_uploads WHERE integration_id=? AND is_test=0",
                (integration_id,),
            ).fetchone()[0])
            result = run_routeros_managed_backup(dict(equipment), dict(credential), config, connect_timeout, command_timeout)
            expected_base = result.get("expected_base", "")
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", expected_base):
                raise SSHBackupError("ROUTEROS_SCRIPT_FAILED", "A execução não forneceu um identificador seguro para confirmar os arquivos FTP.")
            payload = {"ok": True, "status": "waiting_upload", "message": "Envio concluído no MikroTik. Aguardando recebimento e validação no servidor FTP.",
                       "routeros": result["routeros_major"], "terminal": False,
                       "status_url": f"/mikrotik-ftp/{integration_id}/execute-status?after_id={baseline_upload_id}&base={urlencode({'v': expected_base})[2:]}",
                       "timeline": [{"step": "Conexão SSH", "status": "done"}, {"step": "Script validado", "status": "done"},
                                    {"step": "Backup executado no MikroTik", "status": "done"},
                                    {"step": "Arquivos recebidos e validados no servidor", "status": "pending"}]}
            audit(conn, user["id"], "mikrotik_ftp.backup_executed", "mikrotik_ftp_integration", integration["uuid"], json.dumps(payload, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
            return json_response(payload)
        except (ValueError, SecretKeyError, SSHBackupError) as exc:
            raw = str(exc)
            if isinstance(exc, SSHBackupError) and (not raw or raw == exc.code):
                raw = ERROR_MESSAGES.get(exc.code, "Falha na execução do backup no MikroTik.")
            message = sanitize_mikrotik_error(raw) or "O MikroTik não concluiu a execução do backup."
            code = exc.code if isinstance(exc, SSHBackupError) else "execution_failed"
            payload = {"ok": False, "status": "failed", "message": message, "error_code": code, "terminal": True,
                       "timeline": [{"step": "Execução completa no MikroTik", "status": "failed"}]}
            audit(conn, user["id"], "mikrotik_ftp.backup_execution_failed", "mikrotik_ftp_integration", integration["uuid"], json.dumps(payload, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
            return json_response(payload, HTTPStatus.BAD_GATEWAY)


def handle_mikrotik_ftp_execute_status(environ: dict, integration_id: int) -> Response:
    user, response = require_operator(environ)
    if response: return response
    query = parse_qs(environ.get("QUERY_STRING", ""))
    try:
        after_id = max(0, int((query.get("after_id") or ["0"])[0]))
    except ValueError:
        return json_response({"ok": False, "status": "failed", "message": "Referência da execução inválida.", "terminal": True}, HTTPStatus.BAD_REQUEST)
    expected_base = (query.get("base") or [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", expected_base):
        return json_response({"ok": False, "status": "failed", "message": "Identificador da execução inválido.", "terminal": True}, HTTPStatus.BAD_REQUEST)
    with connect() as conn:
        integration = conn.execute("SELECT * FROM mikrotik_ftp_integrations WHERE id=? AND is_active=1", (integration_id,)).fetchone()
        if not integration:
            return json_response({"ok": False, "status": "failed", "message": "Integração FTP Push inativa ou ausente.", "terminal": True}, HTTPStatus.NOT_FOUND)
        rows = conn.execute(
            """SELECT id,file_type,status,error_message,original_filename,size_bytes,received_at FROM mikrotik_ftp_uploads
               WHERE integration_id=? AND is_test=0 AND id>? AND original_filename IN (?,?) ORDER BY id""",
            (integration_id, after_id, f"{expected_base}.backup", f"{expected_base}.rsc"),
        ).fetchall()
        required = {"backup", "rsc"} if integration["backup_format"] == "both" else {integration["backup_format"]}
        successful = {row["file_type"] for row in rows if row["status"] == "success"}
        failed = next((row for row in rows if row["status"] in {"invalid_file", "duplicate"}), None)
        if required <= successful:
            latest = next((row for row in reversed(rows) if row["status"] == "success"), None)
            return json_response({"ok": True, "status": "validated", "message": "Backup recebido e validado com sucesso no servidor FTP.",
                                  "terminal": True, "received_types": sorted(successful),
                                  "latest_upload_filename": latest["original_filename"] if latest else "",
                                  "latest_upload_type": latest["file_type"] if latest else "",
                                  "latest_upload_size": latest["size_bytes"] if latest else 0,
                                  "latest_upload_received_at": latest["received_at"] if latest else ""})
        if failed:
            return json_response({"ok": False, "status": "failed", "message": sanitize_mikrotik_error(failed["error_message"] or "O servidor recebeu um arquivo inválido."),
                                  "terminal": True}, HTTPStatus.BAD_GATEWAY)
        return json_response({"ok": True, "status": "waiting_upload", "message": "Aguardando os arquivos chegarem e serem validados no servidor FTP.",
                              "terminal": False, "received_types": sorted(successful)})


def handle_mikrotik_ftp_script(environ: dict, integration_id: int) -> Response:
    user, response = require_operator(environ)
    if response: return response
    try:
        with connect() as conn:
            integration, _ = require_mikrotik_integration(conn, integration_id)
            config = mikrotik_integration_config(conn, integration, include_secret=True)
            if not config.ftp_password:
                raise ValueError("A senha FTP não está disponível. Rotacione a credencial para gerar o script.")
            script = generate_mikrotik_install_script(config, include_secret=True)
            audit(conn, user["id"], "mikrotik_ftp.script_generated", "mikrotik_ftp_integration", integration["uuid"],
                  json.dumps({"equipment_id": integration["equipment_id"], "routeros": integration["routeros_major_version"]}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
        if "text/plain" in (environ.get("HTTP_ACCEPT") or "").lower():
            return Response(script, HTTPStatus.OK, [("Content-Type", "text/plain; charset=utf-8"),
                ("Cache-Control", "no-store"), ("Pragma", "no-cache")])
        return _mikrotik_script_response(user, title="Instalação manual no MikroTik", script=script,
                                         version=integration["routeros_major_version"])
    except (ValueError, SecretKeyError) as exc:
        return redirect_with_message("/equipment", "error", str(exc))


def handle_mikrotik_ftp_deactivate(environ: dict, integration_id: int) -> Response:
    user, response = require_operator(environ)
    if response: return response
    try:
        with connect() as conn:
            require_mikrotik_integration(conn, integration_id)
    except ValueError as exc:
        return redirect_with_message("/equipment", "error", str(exc))
    result = FTPProvisioningService(connect_factory=connect, helper=run_helper).deactivate_integration(
        integration_id, user_id=user["id"], ip_address=environ.get("REMOTE_ADDR", ""))
    if not result.found:
        return redirect("/equipment")
    if not result.ok:
        return redirect_with_message(f"/equipment/{result.equipment_id}", "error", "Não foi possível remover a conta do Pure-FTPd; nenhuma desativação foi aplicada.")
    removal = generate_mikrotik_removal_script(result.routeros_major_version)
    return _mikrotik_script_response(user, title="Integração desativada · script de remoção", script=removal, version=result.routeros_major_version)


def handle_mikrotik_ftp_remove_ssh(environ: dict, integration_id: int) -> Response:
    user, response = require_operator(environ)
    if response: return response
    try:
        with connect() as conn:
            integration, equipment = require_mikrotik_integration(conn, integration_id)
            credential = conn.execute("""SELECT * FROM device_credentials WHERE equipment_id=? AND credential_type='ssh'
                AND is_active=1 AND deleted_at IS NULL ORDER BY updated_at DESC,id DESC LIMIT 1""", (integration["equipment_id"],)).fetchone()
            if not equipment or not credential:
                raise SSHBackupError("SSH_CREDENTIAL_MISSING")
            connect_timeout = int(get_setting(conn, "ssh_connect_timeout_seconds", "15") or 15)
            command_timeout = int(get_setting(conn, "ssh_command_timeout_seconds", "60") or 60)
            remote = remove_routeros_backup_manager(dict(equipment), dict(credential), connect_timeout, command_timeout)
            audit(conn, user["id"], "mikrotik_ftp.ssh_removed", "mikrotik_ftp_integration", integration["uuid"],
                  json.dumps({"routeros": remote["routeros_major"], "removed": True}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
        result = FTPProvisioningService(connect_factory=connect, helper=run_helper).deactivate_integration(
            integration_id, user_id=user["id"], ip_address=environ.get("REMOTE_ADDR", ""))
        if not result.ok:
            return redirect_with_message(f"/equipment/{integration['equipment_id']}", "error", "Os objetos foram removidos do MikroTik, mas a conta FTP local não pôde ser desativada.")
        return redirect_with_message(f"/equipment/{integration['equipment_id']}", "success", "Objetos removidos do MikroTik via SSH e integração local desativada.")
    except (SSHBackupError, ValueError) as exc:
        return redirect_with_message("/equipment", "error", str(exc))


def ftp_accounts_page(environ: dict, error: str = "") -> Response:
    user, response = require_user(environ)
    if response: return response
    query = parse_qs(environ.get("QUERY_STRING", ""))
    search = (query.get("q") or [""])[-1].strip()
    active = (query.get("active") or [""])[-1]
    lifecycle = (query.get("lifecycle") or ["current"])[-1]
    selected_equipment_id = (query.get("equipment_id") or [""])[-1]
    with connect() as conn:
        snapshot = ftp_list_accounts(conn, search=search, lifecycle=lifecycle, active=active)
        selected_equipment = (conn.execute("SELECT id,hostname FROM equipment WHERE id=?", (int(selected_equipment_id),)).fetchone()
                              if selected_equipment_id.isdigit() else None)
    rows = snapshot["rows"]
    equipment = snapshot["equipment"]
    body = "".join(f"""<tr><td><a class="ftp-list-account" href="/ftp/{r['id']}"><span class="ftp-list-icon">FTP</span><span><strong>{e(r['name'])}</strong><small>{e(r['username'])}</small></span></a></td>
      <td>{f'<a class="inline-link" href="/equipment/{r["equipment_id"]}">{e(r["hostname"])}</a>' if r['equipment_id'] else 'Independente'}<small class="ftp-list-sub">{e(r['environment'] or 'Sem ambiente')}</small></td>
      <td>{'Servidor de arquivos' if r['account_type'] == 'file_server' else 'Recebimento de backups'}</td><td>{e(local_dt(r['last_upload_at']))}<small class="ftp-list-sub">{e(r['last_upload_filename'] or 'Nenhum arquivo')}</small></td>
      <td><span class="badge status-{'success' if r['is_active'] and not r['deleted_at'] and r['sync_status'] == 'synced' else 'warning'}">{'Excluída' if r['deleted_at'] else ('Ativa' if r['is_active'] else 'Inativa')}</span><small class="ftp-list-sub">Sincronização: {e(r['sync_status'])}</small></td>
      <td><a class="button small secondary" href="/ftp/{r['id']}">{'Ver histórico' if r['deleted_at'] else 'Gerenciar conta'}</a></td></tr>""" for r in rows)
    create = ""
    if user["can_admin"]:
        options = "".join(f'<option value="{r["id"]}" {"selected" if str(r["id"]) == selected_equipment_id else ""}>{e(r["hostname"])}</option>' for r in equipment)
        contextual = selected_equipment is not None
        purpose_field = (f'<label>Finalidade<input value="Receber backup de um equipamento" disabled>'
                         f'<input type="hidden" name="account_type" value="backup" data-label="Receber backup de um equipamento"><small>Definida pelo equipamento de origem.</small></label>'
                         if contextual else
                         '<label>Finalidade <em>*</em><select name="account_type" id="ftp-account-type"><option value="backup">Receber backup de um equipamento</option><option value="file_server">Servidor de arquivos independente</option></select><small>Selecione a finalidade da conta FTP.</small></label>')
        equipment_field = (f'<label id="ftp-equipment-field">Equipamento<input value="{e(selected_equipment["hostname"])}" disabled>'
                           f'<input type="hidden" name="equipment_id" value="{selected_equipment["id"]}" data-label="{e(selected_equipment["hostname"])}"><small>Equipamento definido pela tela de origem.</small></label>'
                           if contextual else
                           f'<label id="ftp-equipment-field">Equipamento <em>*</em><select name="equipment_id"><option value="">Selecione um equipamento</option>{options}</select><small>A conta será vinculada a este equipamento.</small></label>')
        cancel_href = f'/equipment/{selected_equipment["id"]}' if contextual else '/ftp'
        create = f"""<div class="ftp-wizard-page" data-ftp-wizard>
          <nav class="breadcrumbs ftp-wizard-breadcrumbs" aria-label="Navegação estrutural"><a href="/">Início</a><span>/</span><a href="/ftp">Recebimento por FTP</a><span>/</span><strong>Nova conta FTP</strong></nav>
          <header class="ftp-wizard-head"><h1>Nova conta FTP</h1><p>Cadastre uma nova conta para receber arquivos ou backups via FTP.</p></header>
          <ol class="ftp-wizard-steps" aria-label="Etapas do cadastro">
            <li class="is-active" data-ftp-step-indicator="1"><span>1</span><div><strong>Dados da conta</strong><small>Informações básicas</small></div></li>
            <li data-ftp-step-indicator="2"><span>2</span><div><strong>Usuário e permissões</strong><small>Acesso ao servidor</small></div></li>
            <li data-ftp-step-indicator="3"><span>3</span><div><strong>Pasta e armazenamento</strong><small>Limites de recebimento</small></div></li>
            <li data-ftp-step-indicator="4"><span>4</span><div><strong>Revisão e conclusão</strong><small>Confirme os dados</small></div></li>
          </ol>
          <form method="post" action="/ftp" class="ftp-wizard-form" novalidate>
            <div class="ftp-wizard-main">
              <section class="panel ftp-wizard-panel" data-ftp-step="1"><h2>Dados da conta</h2><div class="ftp-form-grid">
                {purpose_field}
                {equipment_field}
                <label id="ftp-name-field" hidden>Nome da conta <em>*</em><input name="name" maxlength="120" placeholder="Ex.: Atualizações das OLTs"><small>Nome amigável para identificação.</small></label>
                <label class="ftp-wide">Descrição (opcional)<textarea name="notes" rows="3" placeholder="Ex.: Conta utilizada para recebimento de arquivos diários."></textarea><small>Informações adicionais sobre esta conta FTP.</small></label>
              </div></section>
              <section class="panel ftp-wizard-panel" data-ftp-step="2" hidden><h2>Usuário e permissões</h2><div class="ftp-form-grid">
                <label>Usuário FTP <em>*</em><input name="username" minlength="3" maxlength="32" pattern="[a-z0-9][a-z0-9_-]{{2,31}}" autocomplete="username" placeholder="Ex.: backup_olt01" required><small>Use letras minúsculas, números, _ ou -.</small></label>
                <label>Permissão<select name="permission_mode"><option value="upload_only">Somente envio (recomendado)</option><option value="read_write">Leitura e escrita</option></select><small>Limite o acesso ao necessário.</small></label>
                <label>Senha <em>*</em><input id="ftp-password" name="password" type="password" minlength="8" maxlength="32" autocomplete="new-password" required><small><span data-ftp-password-strength>Mínimo de 8 caracteres.</span> Para Huawei OLT, use somente letras, números e <code>._@%+!-</code>. <button type="button" class="ftp-text-button" data-ftp-generate>Gerar senha forte</button></small></label>
                <label>Confirmar senha <em>*</em><input id="ftp-password-confirm" name="confirm_password" type="password" minlength="8" maxlength="32" autocomplete="new-password" required><small><button type="button" class="ftp-text-button" data-ftp-password-toggle>Mostrar senhas</button></small></label>
                <label class="ftp-wide">Origem permitida (opcional)<input name="allowed_source" placeholder="IP ou rede autorizada"><small>Deixe vazio para aceitar conexões de qualquer origem.</small></label>
              </div></section>
              <section class="panel ftp-wizard-panel" data-ftp-step="3" hidden><h2>Pasta e armazenamento</h2><div class="ftp-form-grid">
                <label>Pasta de recebimento<input value="Criada automaticamente" disabled><small>Uma pasta isolada será criada para esta conta.</small></label>
                <label>Modo de conexão<input value="Passivo (PASV)" disabled><small>Compatível com redes protegidas por firewall.</small></label>
                <label class="ftp-wide">Pasta de salvamento (opcional)<input name="upload_subdirectory" maxlength="64" pattern="[A-Za-z0-9_-]+" placeholder="Ex.: backups"><small>Deixe vazio para salvar na raiz <strong>/</strong>. Se informada, a pasta será criada automaticamente, por exemplo <strong>/backups</strong>.</small></label>
                <label>Quota em bytes (opcional)<input name="quota_bytes" type="number" min="1" placeholder="Sem limite"><small>Limite total ocupado pela conta.</small></label>
                <label>Máximo de arquivos (opcional)<input name="max_files" type="number" min="1" placeholder="Sem limite"><small>Quantidade máxima armazenada.</small></label>
              </div><div class="ftp-security-note"><span>♙</span><div><strong>Segurança recomendada</strong><p>Use senhas fortes e limite as permissões apenas à pasta de recebimento.</p></div></div></section>
              <section class="panel ftp-wizard-panel" data-ftp-step="4" hidden><h2>Revisão e conclusão</h2><p>Confira os dados antes de concluir o cadastro.</p><dl class="ftp-final-review" data-ftp-final-review></dl><div class="ftp-security-note success"><span>✓</span><div><strong>Pronto para concluir</strong><p>A senha será exibida somente uma vez após a conclusão.</p></div></div></section>
              <div class="ftp-wizard-actions"><a class="button secondary" href="{cancel_href}">Cancelar</a><div><button type="button" class="secondary" data-ftp-previous hidden>← Voltar</button><button type="button" data-ftp-next>Próximo passo →</button><button type="submit" data-ftp-submit hidden>Concluir cadastro</button></div></div>
            </div>
            <aside class="ftp-wizard-aside"><section class="panel"><h2>Resumo da conta</h2><dl><dt>Finalidade</dt><dd data-ftp-summary="purpose">Receber backup de um equipamento</dd><dt>Equipamento</dt><dd data-ftp-summary="equipment">-</dd><dt>Nome da conta</dt><dd data-ftp-summary="name">-</dd><dt>Usuário</dt><dd data-ftp-summary="username">-</dd><dt>Permissão</dt><dd data-ftp-summary="permission">Somente envio</dd><dt>Pasta</dt><dd data-ftp-summary="folder">/</dd><dt>Quota</dt><dd data-ftp-summary="quota">Sem limite</dd><dt>Status</dt><dd><span class="badge equipment-online">Ativa</span></dd></dl></section><section class="ftp-how-it-works"><h3>ⓘ &nbsp; Como funciona</h3><p>Os equipamentos usarão as credenciais desta conta FTP para enviar os backups. Cada conta possui uma pasta isolada.</p></section></aside>
          </form></div>"""
    active_accounts = sum(1 for row in rows if row["is_active"] and not row["deleted_at"])
    synced_accounts = sum(1 for row in rows if row["sync_status"] == "synced" and not row["deleted_at"])
    content = f"""<section class="ftp-list-page"><header class="ftp-list-hero"><div><span class="eyebrow">FTP Push</span><h1>Contas FTP</h1><p>Gerencie credenciais, equipamentos vinculados e recebimentos FTP.</p></div><div class="actions"><a class="button secondary" href="/ftp/uploads">Ver uploads</a><a class="button" href="/ftp?new=1">Nova conta FTP</a></div></header>
      {message('error', error)}<section class="ftp-list-summary"><article><span>Contas encontradas</span><strong>{len(rows)}</strong></article><article><span>Contas ativas</span><strong>{active_accounts}</strong></article><article><span>Sincronizadas</span><strong>{synced_accounts}</strong></article></section>
      <form method="get" class="ftp-list-filters"><label class="ftp-list-search">Nome ou usuário<input name="q" value="{e(search)}" placeholder="Buscar conta FTP..."></label>
      <label>Situação<select name="lifecycle"><option value="current" {'selected' if lifecycle == 'current' else ''}>Atuais</option><option value="deleted" {'selected' if lifecycle == 'deleted' else ''}>Excluídas</option><option value="all" {'selected' if lifecycle == 'all' else ''}>Todas</option></select></label>
      <label>Status operacional<select name="active"><option value="">Todos</option><option value="1" {'selected' if active == '1' else ''}>Ativas</option><option value="0" {'selected' if active == '0' else ''}>Inativas</option></select></label><button type="submit">Filtrar</button></form>
      <section class="panel table-panel ftp-list-table"><table><thead><tr><th>Conta</th><th>Equipamento / ambiente</th><th>Finalidade</th><th>Último upload</th><th>Status</th><th>Ação</th></tr></thead><tbody>{body or '<tr><td colspan="6" class="ftp-list-empty">Nenhuma conta FTP encontrada.</td></tr>'}</tbody></table></section></section>"""
    if query.get('new') and user['can_admin']:
        content = f"{message('error', error)}{create}"
    return render("Contas FTP", content, user)


def handle_ftp_create(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ); password = form.get("password", "")
    retry_environ = dict(environ)
    retry_environ["QUERY_STRING"] = urlencode({"new": "1", "equipment_id": form.get("equipment_id", "")})
    if password != form.get("confirm_password", ""):
        return ftp_accounts_page(retry_environ, "Confirmação de senha diferente.")
    try:
        with connect() as conn:
            account_type = form.get("account_type", "backup")
            equipment_id = int(form.get("equipment_id", "0")) or None
            equipment = (conn.execute("SELECT hostname,ssh_backup_driver FROM equipment WHERE id=?", (equipment_id,)).fetchone()
                         if equipment_id else None)
        if account_type == "backup" and not equipment:
            raise FTPAccountError("Selecione um equipamento para a conta de backup.")
        if equipment and equipment["ssh_backup_driver"] == "huawei_olt_ssh_ftp":
            from .huawei_olt import SAFE_VALUE_RE
            if not SAFE_VALUE_RE.fullmatch(password):
                raise FTPAccountError("Para Huawei OLT, a senha FTP deve usar apenas letras, números e . _ @ % + ! / -.")
        if account_type == "file_server":
            equipment_id = None
            account_name = form.get("name", "").strip()
        else:
            account_name = f"FTP · {equipment['hostname']}"
        result = FTPProvisioningService(connect_factory=connect, helper=run_helper).create_account(
            equipment_id=equipment_id, account_type=account_type, name=account_name,
            username=form.get("username", ""), password=password,
            permission_mode=form.get("permission_mode", "upload_only"), allowed_source=form.get("allowed_source", ""),
            quota_bytes=_positive_int(form.get("quota_bytes", ""), "Quota"),
            max_files=_positive_int(form.get("max_files", ""), "Numero maximo de arquivos"),
            notes=form.get("notes", ""), user_id=user["id"], ip_address=environ.get("REMOTE_ADDR", ""),
            upload_subdirectory=form.get("upload_subdirectory", ""))
        if not result.ok:
            return ftp_accounts_page(retry_environ, f"A conta não foi criada: {result.error or 'falha na sincronização com o Pure-FTPd'}.")
        content = f"""<section class="ftp-created-page"><nav class="breadcrumbs ftp-wizard-breadcrumbs"><a href="/">Início</a><span>/</span><a href="/ftp">Contas FTP</a><span>/</span><strong>Cadastro concluído</strong></nav>
          <ol class="ftp-wizard-steps ftp-created-steps" aria-label="Etapas concluídas"><li class="is-complete"><span>1</span><div><strong>Dados da conta</strong><small>Concluído</small></div></li><li class="is-complete"><span>2</span><div><strong>Usuário e permissões</strong><small>Concluído</small></div></li><li class="is-complete"><span>3</span><div><strong>Pasta e armazenamento</strong><small>Concluído</small></div></li><li class="is-active"><span>4</span><div><strong>Revisão e conclusão</strong><small>Conta criada</small></div></li></ol>
          <header class="ftp-created-hero"><span class="ftp-created-check">✓</span><div><span class="eyebrow">Conta FTP criada</span><h1>Credencial criada com sucesso</h1><p>A conta já está ativa e pronta para receber arquivos.</p></div></header>
          <article class="panel ftp-created-card"><div class="ftp-created-heading"><div><h2>Guarde esta credencial</h2><p>A senha é exibida somente agora e não poderá ser consultada novamente.</p></div><span class="badge status-success">Ativa</span></div>
          <div class="ftp-created-secret"><div><span>Usuário FTP</span><strong>{e(result.username)}</strong></div><div><span>Senha temporária</span><code>{e(password)}</code></div></div>
          <dl class="ftp-created-details"><div><dt>Pasta remota</dt><dd>/{e(form.get('upload_subdirectory', '').strip().strip('/'))}</dd></div><div><dt>Modo de conexão</dt><dd>Passivo (PASV)</dd></div><div><dt>Finalidade</dt><dd>Recebimento de backups</dd></div></dl>
          <div class="ftp-created-warning"><strong>Importante</strong><p>Copie e armazene a senha em local seguro antes de continuar. Se perdê-la, será necessário gerar uma nova credencial.</p></div>
          <footer class="ftp-created-actions"><a class="button secondary" href="/ftp/{result.account_id}">Ver conta FTP</a>{f'<a class="button" href="/equipment/{result.equipment_id}#backup">Concluir e voltar ao equipamento</a>' if result.equipment_id else '<a class="button" href="/ftp">Concluir cadastro</a>'}</footer></article>
          </section>"""
        return Response(layout("Credencial FTP criada", content, user), HTTPStatus.CREATED,
                        [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store, no-cache, must-revalidate"), ("Pragma", "no-cache")])
    except (ValueError, FTPAccountError) as exc:
        return ftp_accounts_page(retry_environ, str(exc))


def ftp_account_detail_page(environ: dict, account_id: int) -> Response:
    user, response = require_user(environ)
    if response: return response
    query = parse_qs(environ.get("QUERY_STRING", ""))
    notice_kind = (query.get("notice") or [""])[-1]
    notice_text = (query.get("message") or [""])[-1]
    notice = message(notice_kind, notice_text) if notice_kind in {"success", "error", "info"} and notice_text else ""
    with connect() as conn:
        snapshot = ftp_account_detail(conn, account_id)
    row = snapshot["row"]
    uploads = snapshot["uploads"]
    server = snapshot["server"]
    if not row: return render("Conta FTP", "<p>Conta não encontrada.</p>", user, HTTPStatus.NOT_FOUND)
    actions = ""
    edit_dialog = ""
    password_dialog = ""
    delete_dialog = ""
    if user["can_admin"] and row["deleted_at"] is None:
        allowed_source = row["allowed_source_ip"] or row["allowed_source_cidr"] or ""
        edit_dialog = f'''<dialog id="ftp-account-edit" class="ftp-account-edit-dialog"><form method="dialog"><button class="dialog-close" aria-label="Fechar">×</button></form><header><h2>Editar configuração FTP</h2><p>Ajuste o recebimento sem recriar o usuário ou alterar a senha.</p></header><form method="post" action="/ftp/{account_id}/settings" class="ftp-account-edit-form"><div class="ftp-form-grid"><label>Origem permitida (opcional)<input name="allowed_source" value="{e(allowed_source)}" placeholder="IP ou rede autorizada"><small>Deixe vazio para aceitar qualquer origem permitida pelo firewall.</small></label><label>Pasta de salvamento (opcional)<input name="upload_subdirectory" value="{e(row['upload_subdirectory'] or '')}" maxlength="64" pattern="[A-Za-z0-9_-]+" placeholder="Ex.: backups"><small>Vazio salva na raiz /. A pasta informada será criada automaticamente.</small></label><label>Quota em bytes (opcional)<input name="quota_bytes" type="number" min="1" value="{e(row['quota_bytes'] or '')}" placeholder="Sem limite"></label><label>Máximo de arquivos (opcional)<input name="max_files" type="number" min="1" value="{e(row['max_files'] or '')}" placeholder="Sem limite"></label><label class="ftp-wide">Observações (opcional)<textarea name="notes" rows="3" maxlength="1000">{e(row['notes'] or '')}</textarea></label></div><div class="notice info">A alteração da pasta vale para os próximos envios. Arquivos já recebidos permanecem preservados.</div><footer><button type="button" class="secondary" data-dialog-close>Cancelar</button><button type="submit">Salvar configuração</button></footer></form></dialog>'''
        password_dialog = f'''<dialog id="ftp-account-password" class="ftp-account-edit-dialog ftp-account-password-dialog"><form method="dialog"><button class="dialog-close" aria-label="Fechar">×</button></form><header><h2>Alterar senha FTP</h2><p>Defina uma nova senha para a conta <strong>{e(row['username'])}</strong>.</p></header><form method="post" action="/ftp/{account_id}/reset-password" class="ftp-account-edit-form"><div class="ftp-form-grid"><label>Nova senha<input name="password" type="password" minlength="8" maxlength="32" autocomplete="new-password" required><small>Use pelo menos 8 caracteres.</small></label><label>Confirmar senha<input name="confirm_password" type="password" minlength="8" maxlength="32" autocomplete="new-password" required><small>Repita exatamente a nova senha.</small></label></div><div class="notice warning"><strong>Atenção</strong><p>A senha atual deixará de funcionar imediatamente. Depois, atualize a credencial no equipamento.</p></div><footer><button type="button" class="secondary" data-dialog-close>Cancelar</button><button type="submit">Salvar nova senha</button></footer></form></dialog>'''
        delete_dialog = f'''<dialog id="ftp-account-delete" class="ftp-account-delete-dialog"><form method="dialog"><button class="dialog-close" aria-label="Fechar">×</button></form><div class="ftp-delete-dialog-icon" aria-hidden="true">!</div><h2>Excluir e desvincular conta?</h2><p>A conta <strong>{e(row['name'])}</strong> será desativada e não aceitará novos acessos FTP.</p><div class="notice info"><strong>Seus dados serão preservados</strong><p>Os arquivos recebidos, backups e o histórico de uploads não serão apagados.</p></div><footer><button type="button" class="secondary" data-dialog-close>Cancelar</button><form method="post" action="/ftp/{account_id}/delete"><button class="danger" type="submit">Excluir</button></form></footer></dialog>'''
        toggle = f'''<section class="ftp-admin-action"><div><strong>Estado da conta</strong><small>A conta está ativa e pode receber novos arquivos.</small></div><form method="post" action="/ftp/{account_id}/toggle" onsubmit="return confirm('Desativar esta conta FTP? Novos acessos serão bloqueados, mas os arquivos e o histórico serão preservados.')"><button class="ftp-disable-account-button" type="submit"><svg aria-hidden="true" viewBox="0 0 16 16"><rect x="3" y="2" width="3.5" height="12" rx="1"/><rect x="9.5" y="2" width="3.5" height="12" rx="1"/></svg><span>Desativar</span></button></form></section>''' if row["is_active"] else '<section class="ftp-admin-action"><div><strong>Conta inativa</strong><small>Para reativá-la com segurança, gere uma nova senha abaixo.</small></div></section>'
        actions = f"""<section class="ftp-admin-action"><div><strong>Configuração de recebimento</strong><small>Altere pasta, origem permitida e limites da conta.</small></div><button type="button" class="ftp-edit-settings-button" data-dialog-open="ftp-account-edit"><svg aria-hidden="true" viewBox="0 0 16 16"><path d="M11.8 1.5a1.7 1.7 0 0 1 2.4 2.4L6 12.1l-3.2.7.7-3.2 8.3-8.1Z"/></svg><span>Editar</span></button></section>{toggle}
        <section class="ftp-admin-action"><div><strong>Senha da conta</strong><small>Altere a senha usada pelo equipamento para acessar esta conta.</small></div><button type="button" class="ftp-password-button" data-dialog-open="ftp-account-password"><svg aria-hidden="true" viewBox="0 0 16 16"><path d="M13.5 5.5A6 6 0 1 0 14 9h-2a4 4 0 1 1-.4-1.8L9 8V2h6l-1.5 3.5Z"/></svg><span>Alterar senha</span></button></section>
        <section class="ftp-admin-action ftp-admin-danger"><div><strong>Excluir e desvincular</strong><small>Os arquivos e o histórico serão preservados.</small></div><button type="button" class="ftp-delete-account-button" data-dialog-open="ftp-account-delete"><svg aria-hidden="true" viewBox="0 0 16 16"><path d="M5 2h6l1 2h3v2H1V4h3l1-2Zm-2 5h10l-.7 7H3.7L3 7Z"/></svg><span>Excluir conta</span></button></section>"""
    if row["deleted_at"] is not None:
        actions = '<p class="muted">Conta excluída. Novos envios estão bloqueados; arquivos e histórico foram preservados.</p>'
    upload_rows = "".join(f"<tr><td>{e(local_dt(r['detected_at']))}</td><td>{e(r['original_filename'])}</td><td>{format_size(r['file_size'])}</td><td>{e(r['status'])}</td><td>{e(r['backup_status'] or '-')}</td><td>{f'<a class=\"button small secondary\" href=\"/backups/{e(r["backup_uuid"])}\">Abrir backup</a>' if r['backup_uuid'] else 'Somente histórico'}</td><td>{e(r['error_message'] or '-')}</td></tr>" for r in uploads)
    remote_folder = "/" + e(row["upload_subdirectory"] or "")
    account_status = "Excluída" if row["deleted_at"] else ("Ativa" if row["is_active"] else "Inativa")
    content = f"""<section class="ftp-account-page"><nav class="breadcrumbs ftp-wizard-breadcrumbs"><a href="/">Início</a><span>/</span><a href="/ftp">Contas FTP</a><span>/</span><strong>{e(row['name'])}</strong></nav>
      <header class="ftp-account-hero"><div><span class="eyebrow">Conta FTP</span><h1>{e(row['name'])}</h1><p>Gerencie acesso, recebimento e histórico desta conta.</p></div><div class="actions"><span class="badge status-{'success' if row['is_active'] and not row['deleted_at'] else 'warning'}">{account_status}</span><a class="button secondary" href="/ftp">Voltar</a></div></header>{notice}
      <section class="ftp-account-summary"><article><span>Servidor</span><strong>{e(server)}</strong><small>Porta {row['control_port']}</small></article><article><span>Usuário</span><strong>{e(row['username'])}</strong><small>Credencial protegida</small></article><article><span>Pasta remota</span><strong>{remote_folder}</strong><small>{'Subpasta configurada' if row['upload_subdirectory'] else 'Raiz da conta'}</small></article><article><span>Último upload</span><strong>{e(local_dt(row['last_upload_at']))}</strong><small>{e(row['last_upload_filename'] or 'Nenhum arquivo')}</small></article></section>
      <section class="ftp-account-grid"><article class="panel"><div class="panel-heading"><h2>Configuração da conta</h2><span class="badge">{e(row['sync_status'])}</span></div><dl class="ftp-account-details"><dt>Finalidade</dt><dd>{'Servidor de arquivos independente' if row['account_type'] == 'file_server' else 'Recebimento de backups'}</dd><dt>Equipamento</dt><dd>{f'<a href="/equipment/{row["equipment_id"]}">{e(row["hostname"])}</a>' if row['equipment_id'] else 'Sem vínculo'}</dd><dt>Modo</dt><dd>Passivo (PASV)</dd><dt>Permissão</dt><dd>{e(row['permission_mode'])}</dd><dt>IP permitido</dt><dd>{e(row['allowed_source_ip'] or row['allowed_source_cidr'] or 'Qualquer origem aceita pelo firewall')}</dd><dt>Pasta de salvamento</dt><dd>{remote_folder}</dd></dl><p class="muted">A restrição de IP complementa, mas não substitui, as regras do firewall.</p></article>
      <article class="panel ftp-account-admin"><h2>Ações administrativas</h2><p class="muted">Gerencie o estado da conta e regenere a credencial quando necessário.</p><div class="actions block-actions">{actions or 'Somente leitura. A senha nunca é exibida.'}</div></article></section>
      <section class="panel table-panel ftp-account-history"><div class="panel-heading"><div><h2>Arquivos recebidos</h2><p>Histórico de uploads e backups associados à conta.</p></div></div><table><thead><tr><th>Detectado</th><th>Arquivo</th><th>Tamanho</th><th>Recebimento</th><th>Backup</th><th>Ação</th><th>Erro</th></tr></thead><tbody>{upload_rows or '<tr><td colspan="7">Nenhum arquivo recebido por esta conta.</td></tr>'}</tbody></table></section>{edit_dialog}{password_dialog}{delete_dialog}</section>"""
    return render(row["name"], content, user)


def handle_ftp_settings(environ: dict, account_id: int) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ)
    try:
        allowed_source = validate_source_network(form.get("allowed_source", ""))
        upload_subdirectory = validate_upload_subdirectory(form.get("upload_subdirectory", ""))
        quota_bytes = _positive_int(form.get("quota_bytes", ""), "Quota")
        max_files = _positive_int(form.get("max_files", ""), "Número máximo de arquivos")
        notes = form.get("notes", "").strip()[:1000]
        with connect() as conn:
            row = conn.execute("SELECT * FROM ftp_accounts WHERE id=? AND deleted_at IS NULL", (account_id,)).fetchone()
            if not row: return redirect("/ftp")
            previous = dict(row)
            conn.execute("""UPDATE ftp_accounts SET allowed_source_ip=?,allowed_source_cidr=?,
                upload_subdirectory=?,quota_bytes=?,max_files=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (allowed_source if allowed_source and "/" not in allowed_source else None,
                 allowed_source if "/" in allowed_source else None, upload_subdirectory,
                 quota_bytes, max_files, notes, account_id))
            audit(conn, user["id"], "ftp.account_settings_updated", "ftp_account", row["uuid"],
                  json.dumps({"upload_subdirectory": upload_subdirectory, "source_restricted": bool(allowed_source),
                              "quota_bytes": quota_bytes, "max_files": max_files}, separators=(",", ":")),
                  environ.get("REMOTE_ADDR", ""))
        ok, detail = run_helper("check-account", account_id)
        if not ok:
            with connect() as conn:
                conn.execute("""UPDATE ftp_accounts SET allowed_source_ip=?,allowed_source_cidr=?,
                    upload_subdirectory=?,quota_bytes=?,max_files=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (previous["allowed_source_ip"], previous["allowed_source_cidr"], previous["upload_subdirectory"],
                     previous["quota_bytes"], previous["max_files"], previous["notes"], account_id))
            return redirect_with_message(f"/ftp/{account_id}", "error", f"A configuração não foi aplicada: {detail}.")
        return redirect_with_message(f"/ftp/{account_id}", "success", "Configuração FTP atualizada.")
    except (ValueError, FTPAccountError) as exc:
        return redirect_with_message(f"/ftp/{account_id}", "error", str(exc))


def handle_ftp_toggle(environ: dict, account_id: int) -> Response:
    user, response = require_admin(environ)
    if response: return response
    result = FTPProvisioningService(connect_factory=connect, helper=run_helper).disable_account(
        account_id, delete=False, user_id=user["id"], ip_address=environ.get("REMOTE_ADDR", ""))
    if not result.found:
        return redirect("/ftp")
    if result.error == "already_inactive":
        return render("Ativar conta FTP", message("info", "Redefina a senha para reativar esta conta."), user, HTTPStatus.CONFLICT)
    return redirect(f"/ftp/{account_id}")


def handle_ftp_reset(environ: dict, account_id: int) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ); password = form.get("password", "")
    try:
        if password != form.get("confirm_password", ""): raise FTPAccountError("Confirmação de senha diferente.")
        with connect() as conn:
            account_driver = conn.execute("""SELECT e.ssh_backup_driver FROM ftp_accounts a
                LEFT JOIN equipment e ON e.id=a.equipment_id WHERE a.id=?""", (account_id,)).fetchone()
        if account_driver and account_driver[0] == "huawei_olt_ssh_ftp":
            from .huawei_olt import SAFE_VALUE_RE
            if not SAFE_VALUE_RE.fullmatch(password):
                raise FTPAccountError("Para Huawei OLT, a senha FTP deve usar apenas letras, números e . _ @ % + ! / -.")
        result = FTPProvisioningService(connect_factory=connect, helper=run_helper).reset_password(
            account_id, password, user_id=user["id"], ip_address=environ.get("REMOTE_ADDR", ""))
        if not result.found: return redirect("/ftp")
        content = f'''<section class="credential-result-page"><header class="credential-result-hero"><span aria-hidden="true">✓</span><div><span class="eyebrow">Conta FTP atualizada</span><h1>Nova senha criada</h1><p>A credencial anterior deixou de funcionar.</p></div></header><article class="panel credential-result-card"><div><span>Nova senha FTP</span><code>{e(password)}</code></div><div class="notice warning"><strong>Guarde esta credencial agora.</strong> Ela não poderá ser visualizada novamente.</div><footer><a class="button" href="/ftp/{account_id}">Concluir e voltar à conta</a></footer></article></section>'''
        return Response(layout("Senha FTP redefinida", content, user), HTTPStatus.OK, [("Content-Type", "text/html; charset=utf-8"),("Cache-Control","no-store")])
    except FTPAccountError as exc:
        return render("Senha FTP", message("error", str(exc)) + f'<a href="/ftp/{account_id}">Voltar</a>', user, HTTPStatus.BAD_REQUEST)


def handle_ftp_delete(environ: dict, account_id: int) -> Response:
    user, response = require_admin(environ)
    if response: return response
    result = FTPProvisioningService(connect_factory=connect, helper=run_helper).disable_account(
        account_id, delete=True, user_id=user["id"], ip_address=environ.get("REMOTE_ADDR", ""))
    if result.found and not result.ok:
        return redirect_with_message(f"/ftp/{account_id}", "error", "Não foi possível remover a conta do Pure-FTPd; a exclusão foi revertida.")
    return redirect("/ftp")


def ftp_uploads_page(environ: dict) -> Response:
    user, response = require_user(environ)
    if response: return response
    query = parse_qs(environ.get("QUERY_STRING", "")); status = (query.get("status") or [""])[-1]
    where, params = ["1=1"], []
    if status in {"detected","waiting_stable","processing","imported","rejected","failed","duplicate"}: where.append("r.status=?"); params.append(status)
    if not user["can_admin"]: where.append("r.status IN ('imported','duplicate')")
    with connect() as conn:
        rows = conn.execute(f"""SELECT r.*,a.name account_name,equipment.hostname,backups.uuid backup_uuid FROM ftp_received_files r
          JOIN ftp_accounts a ON a.id=r.ftp_account_id JOIN equipment ON equipment.id=r.equipment_id LEFT JOIN backups ON backups.id=r.backup_id
          WHERE {' AND '.join(where)} ORDER BY r.id DESC LIMIT 200""", params).fetchall()
    upload_lines = []
    status_labels = {"detected":"Detectado", "waiting_stable":"Aguardando arquivo", "processing":"Processando", "imported":"Importado", "rejected":"Rejeitado", "failed":"Falhou", "duplicate":"Duplicado"}
    status_classes = {"imported":"success", "duplicate":"neutral", "failed":"error", "rejected":"warning", "processing":"running", "waiting_stable":"queued", "detected":"queued"}
    for row in rows:
        backup_link = f'<a href="/backups/{e(row["backup_uuid"])}">Abrir</a>' if row["backup_uuid"] else "-"
        upload_lines.append(f"<tr><td>{e(local_dt(row['detected_at']))}</td><td><strong>{e(row['hostname'])}</strong></td><td>{e(row['account_name'])}</td><td><span class='ftp-upload-file'>{e(row['original_filename'])}</span></td><td>{format_size(row['file_size'])}</td><td><span class='badge status-{status_classes.get(row['status'],'neutral')}'>{e(status_labels.get(row['status'], row['status']))}</span></td><td>{e(row['source_ip'] or '-')}</td><td>{backup_link}</td><td><span class='ftp-upload-error'>{e(row['error_message'] or '-')}</span></td></tr>")
    body = "".join(upload_lines)
    options = "".join(f'<option value="{key}" {"selected" if status == key else ""}>{label}</option>' for key, label in status_labels.items())
    imported = sum(1 for row in rows if row["status"] == "imported")
    attention = sum(1 for row in rows if row["status"] in {"rejected", "failed"})
    content = f'''<section class="ftp-uploads-page"><header class="page-head ftp-uploads-head"><div><span class="eyebrow">FTP Push</span><h1>Uploads FTP</h1><p>Acompanhe o recebimento, processamento e associação dos arquivos.</p></div><a class="button secondary" href="/ftp">Gerenciar contas</a></header><section class="ftp-uploads-summary"><article><span>Registros exibidos</span><strong>{len(rows)}</strong></article><article><span>Importados</span><strong>{imported}</strong></article><article><span>Requerem atenção</span><strong>{attention}</strong></article></section><form method="get" action="/ftp/uploads" class="panel ftp-uploads-filter"><label>Status<select name="status"><option value="">Todos os estados</option>{options}</select></label><button type="submit">Filtrar</button><a href="/ftp/uploads">Limpar filtro</a></form><section class="panel table-panel ftp-uploads-table"><header><div><span class="eyebrow">Histórico de recebimento</span><h2>Arquivos detectados</h2></div><span>{len(rows)} registros</span></header><table><thead><tr><th>Data</th><th>Equipamento</th><th>Conta</th><th>Arquivo</th><th>Tamanho</th><th>Status</th><th>IP</th><th>Backup</th><th>Erro</th></tr></thead><tbody>{body or '<tr><td colspan="9" class="ftp-uploads-empty">Nenhum upload encontrado.</td></tr>'}</tbody></table></section></section>'''
    return render("Uploads FTP", content, user)


def reports_overview_page(environ: dict) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    query, filters = report_query(environ)
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    period_start = datetime.strptime(filters.start, "%Y-%m-%d").date() if filters.start else today - timedelta(days=6)
    period_end = datetime.strptime(filters.end, "%Y-%m-%d").date() if filters.end else today
    if period_end < period_start:
        period_start, period_end = period_end, period_start
    period_days = (period_end - period_start).days + 1
    previous_end = period_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    with connect() as conn:
        data = report_overview(conn, filters, get_setting(conn, "storage_root", "/"))
        options = report_choices(conn)
        chart = daily_chart(conn, filters, days=7)
        gaps = equipment_gap_summary(conn, filters)
        failures = failure_report(conn, filters)[:3]
        ftp_data = ftp_report(conn, filters)
        method_clauses = []
        method_params = []
        if filters.equipment_id:
            method_clauses.append("equipment_id=?")
            method_params.append(filters.equipment_id)
        if filters.start:
            method_clauses.append("received_at>=?")
            method_params.append(filters.start)
        if filters.end:
            method_clauses.append("received_at<datetime(?, '+1 day')")
            method_params.append(filters.end)
        if filters.status == "success":
            method_clauses.append("backup_status='available'")
        elif filters.status == "failed":
            method_clauses.append("backup_status IN ('failed','quarantined')")
        method_where = " WHERE " + " AND ".join(method_clauses) if method_clauses else ""
        method_rows = conn.execute("""SELECT CASE WHEN source_method IN ('ssh','ftp','manual') THEN source_method ELSE 'outros' END method,
            COUNT(*) total FROM backups""" + method_where + " GROUP BY 1 ORDER BY total DESC", method_params).fetchall()
        comparison_equipment = " AND equipment_id=?" if filters.equipment_id else ""
        comparison_params = [period_start.isoformat(), (period_end + timedelta(days=1)).isoformat(),
                             previous_start.isoformat(), (previous_end + timedelta(days=1)).isoformat()]
        if filters.equipment_id:
            comparison_params.append(filters.equipment_id)
        comparison = conn.execute(
            "SELECT "
            "SUM(CASE WHEN backup_status='available' AND received_at>=? AND received_at<? THEN 1 ELSE 0 END) current_success,"
            "SUM(CASE WHEN backup_status='available' AND received_at>=? AND received_at<? THEN 1 ELSE 0 END) previous_success,"
            "SUM(CASE WHEN backup_status IN ('failed','quarantined') AND received_at>=? AND received_at<? THEN 1 ELSE 0 END) current_failed,"
            "SUM(CASE WHEN backup_status IN ('failed','quarantined') AND received_at>=? AND received_at<? THEN 1 ELSE 0 END) previous_failed,"
            "SUM(CASE WHEN backup_status='available' AND received_at>=? AND received_at<? THEN file_size ELSE 0 END) current_bytes,"
            "SUM(CASE WHEN backup_status='available' AND received_at>=? AND received_at<? THEN file_size ELSE 0 END) previous_bytes "
            "FROM backups WHERE 1=1" + comparison_equipment,
            comparison_params[:4] + comparison_params[:4] + comparison_params[:4] + ([filters.equipment_id] if filters.equipment_id else []),
        ).fetchone()
        attention_equipment = " AND e.id=?" if filters.equipment_id else ""
        def attention_at(cutoff) -> int:
            params = [(cutoff - timedelta(days=3)).isoformat(), (cutoff + timedelta(days=1)).isoformat()]
            if filters.equipment_id:
                params.append(filters.equipment_id)
            return int(conn.execute(
                "SELECT COUNT(*) FROM equipment e WHERE e.is_active=1 AND NOT EXISTS ("
                "SELECT 1 FROM backups b WHERE b.equipment_id=e.id AND b.backup_status='available' "
                "AND b.received_at>=? AND b.received_at<?)" + attention_equipment, params).fetchone()[0])
        current_attention = attention_at(period_end)
        previous_attention = attention_at(previous_end)
    maximum = max([int(row["total"]) for row in chart] or [1])
    week_days = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
    bar_rows = []
    for row in chart:
        day = datetime.strptime(row["day"], "%Y-%m-%d")
        success = int(row["success"] or 0)
        failed = int(row["failed"] or 0)
        bar_rows.append(
            f'<div class="report-activity-row"><div class="report-activity-date"><strong>{day:%d/%m}</strong><span>{week_days[day.weekday()]}</span></div>'
            f'<div class="report-activity-bar"><div class="report-activity-fill" style="width:{100 * int(row["total"]) / maximum:.1f}%">'
            f'<i class="activity-success" style="flex:{success}"></i><i class="activity-failure" style="flex:{failed}"></i></div></div>'
            f'<div class="report-activity-total"><strong>{row["total"]}</strong><span>{"backup" if int(row["total"]) == 1 else "backups"}</span></div></div>'
        )
    bars = "".join(bar_rows) or '<div class="report-empty-chart"><strong>Nenhuma execução no período</strong><span>Altere as datas acima para consultar outro intervalo.</span></div>'
    failure_total = int(comparison["current_failed"] or 0)
    def comparison_text(current: int, previous: int, *, unit: str = "") -> str:
        delta = current - previous
        if delta == 0:
            return "Sem alteração vs. período anterior"
        value = format_size(abs(delta)) if unit == "bytes" else str(abs(delta))
        return f'{"+" if delta > 0 else "−"}{value} vs. período anterior'

    def trend_class(current: int, previous: int, *, positive_good: bool | None) -> str:
        if current == previous or positive_good is None:
            return "trend-neutral"
        is_good = (current > previous) == positive_good
        return "trend-good" if is_good else "trend-bad"

    metrics = (
        ("Backups concluídos", int(comparison["current_success"] or 0), "check", comparison_text(int(comparison["current_success"] or 0), int(comparison["previous_success"] or 0)), trend_class(int(comparison["current_success"] or 0), int(comparison["previous_success"] or 0), positive_good=True)),
        ("Falhas registradas", failure_total, "alert", comparison_text(failure_total, int(comparison["previous_failed"] or 0)), trend_class(failure_total, int(comparison["previous_failed"] or 0), positive_good=False)),
        ("Equipamentos com atenção", current_attention, "warning", comparison_text(current_attention, previous_attention), trend_class(current_attention, previous_attention, positive_good=False)),
        ("Armazenamento utilizado", format_size(data["storage_used"]), "database", comparison_text(int(comparison["current_bytes"] or 0), int(comparison["previous_bytes"] or 0), unit="bytes"), "trend-neutral"),
    )
    metric_icons = {
        "server": '<rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 7h.01M7 17h.01"/>',
        "archive": '<path d="M21 8v13H3V8M1 3h22v5H1zM10 12h4"/>',
        "check": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
        "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v7c0 1.7 4 3 9 3s9-1.3 9-3V5M3 12v7c0 1.7 4 3 9 3s9-1.3 9-3v-7"/>',
        "file": '<path d="M6 2h8l4 4v16H6zM14 2v5h5"/>',
        "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>',
        "alert": '<path d="M10.3 3.6 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
        "warning": '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 17h.01"/>',
    }
    cards = "".join(f'<article class="stat report-stat"><span class="report-stat-icon"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{metric_icons[icon]}</svg></span><div><span>{e(label)}</span><strong>{e(value)}</strong><small class="{trend}">{e(detail)}</small></div></article>' for label, value, icon, detail, trend in metrics)
    chart_max = max(1, max((max(int(row["success"] or 0), int(row["failed"] or 0)) for row in chart), default=0))
    chart_step = 440 / max(1, len(chart) - 1)
    success_points = " ".join(f'{30 + index * chart_step:.1f},{150 - 105 * int(row["success"] or 0) / chart_max:.1f}' for index, row in enumerate(chart))
    failure_points = " ".join(f'{30 + index * chart_step:.1f},{150 - 105 * int(row["failed"] or 0) / chart_max:.1f}' for index, row in enumerate(chart))
    success_dots = "".join(f'<circle class="report-dot-success" cx="{30 + index * chart_step:.1f}" cy="{150 - 105 * int(row["success"] or 0) / chart_max:.1f}" r="3.5"/>' for index, row in enumerate(chart))
    failure_dots = "".join(f'<circle class="report-dot-failure" cx="{30 + index * chart_step:.1f}" cy="{150 - 105 * int(row["failed"] or 0) / chart_max:.1f}" r="3.5"/>' for index, row in enumerate(chart))
    chart_scale = "".join(f'<text x="22" y="{position}">{value}</text>' for position, value in (
        (48, chart_max), (83, round(chart_max * 2 / 3)), (118, round(chart_max / 3)), (153, 0)))
    chart_labels = "".join(f'<span>{datetime.strptime(row["day"], "%Y-%m-%d"):%d/%m}<small>{week_days[datetime.strptime(row["day"], "%Y-%m-%d").weekday()]}</small></span>' for row in chart)
    failure_lines = "".join(f'<li><span>{e(row["hostname"])}</span><strong>{row["failure_count"]}</strong></li>' for row in failures) or '<li class="empty">Nenhuma falha registrada.</li>'
    method_total = sum(int(row["total"]) for row in method_rows) or 1
    method_names = {"ssh": "SSH", "ftp": "FTP", "manual": "Manual", "outros": "Outros"}
    method_colors = {"ssh": "#16a36d", "ftp": "#2878d0", "manual": "#7a52c7", "outros": "#98a2b3"}
    method_stops = []
    method_cursor = 0.0
    for row in method_rows:
        method_end = method_cursor + 100 * int(row["total"]) / method_total
        method_stops.append(f'{method_colors.get(row["method"], "#98a2b3")} {method_cursor:.1f}% {method_end:.1f}%')
        method_cursor = method_end
    method_gradient = ",".join(method_stops) or "#e7edf3 0 100%"
    method_lines = "".join(f'<li class="method-{e(row["method"])}"><i></i><span>{e(method_names.get(row["method"], row["method"]))}</span><strong>{100 * int(row["total"]) / method_total:.0f}% <small>({row["total"]})</small></strong></li>' for row in method_rows) or '<li class="empty">Sem dados.</li>'
    alerts = (
        (f'{failure_total} falhas recentes', "/reports/equipment", "danger"),
        (f'{gaps["h72"]} equipamentos sem backup há 72h', "/reports/equipment", "warning"),
        (f'{ftp_data["rejected"] or 0} arquivos rejeitados no FTP', "/reports/ftp", "info"),
    )
    alert_lines = "".join(f'<a class="report-overview-alert {kind}" href="{href}"><i><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 17h.01"/></svg></i><span>{e(label)}</span><b>›</b></a>' for label, href, kind in alerts)
    quick_links = "".join(f'<a href="{href}">{report_icon(icon)}<span>{label}</span></a>' for href, label, icon in (
        ("/reports/backups", "Backups", "archive"), ("/reports/equipment", "Equipamentos", "server"),
        ("/reports/storage", "Armazenamento", "database"), ("/reports/ftp", "FTP", "upload"),
        ("/reports/audit", "Auditoria", "shield"), ("/reports/exports", "Exportações", "download")))
    equipment_options = "".join(f'<option value="{row["id"]}" {"selected" if filters.equipment_id == row["id"] else ""}>{e(row["name"])}</option>' for row in options["equipment"])
    period_label = f'{filters.start} a {filters.end}' if filters.start and filters.end else "Últimos 7 dias"
    requested_period = query.get("period", "")
    period_preset = requested_period if requested_period in {"1", "7", "10", "15", "20", "25", "30"} else ("custom" if filters.start or filters.end else "7")
    overview_filters = f'''<form method="get" action="/reports" class="panel report-overview-filterbar">
      <details><summary>{report_icon("grid")}<span>Período: <strong>{e(period_label)}</strong></span><b>⌄</b></summary><div class="report-overview-period"><label class="report-period-preset">Período rápido<select name="period" aria-label="Período rápido" onchange="const f=this.form;if(this.value==='custom')return;const d=new Date(),s=new Date();s.setDate(d.getDate()-Number(this.value)+1);const z=x=>x.getFullYear()+'-'+String(x.getMonth()+1).padStart(2,'0')+'-'+String(x.getDate()).padStart(2,'0');f.elements.start.value=z(s);f.elements.end.value=z(d)"><option value="1" {"selected" if period_preset == "1" else ""}>Último 1 dia</option><option value="7" {"selected" if period_preset == "7" else ""}>Últimos 7 dias</option><option value="10" {"selected" if period_preset == "10" else ""}>Últimos 10 dias</option><option value="15" {"selected" if period_preset == "15" else ""}>Últimos 15 dias</option><option value="20" {"selected" if period_preset == "20" else ""}>Últimos 20 dias</option><option value="25" {"selected" if period_preset == "25" else ""}>Últimos 25 dias</option><option value="30" {"selected" if period_preset == "30" else ""}>Últimos 30 dias</option><option value="custom" {"selected" if period_preset == "custom" else ""}>Selecionar datas</option></select></label><label>Data inicial<input type="date" name="start" value="{e(filters.start)}" onchange="this.form.querySelector('[aria-label=\'Período rápido\']').value='custom'"></label><label>Data final<input type="date" name="end" value="{e(filters.end)}" onchange="this.form.querySelector('[aria-label=\'Período rápido\']').value='custom'"></label><button class="report-period-apply" type="submit">✓ Aplicar período</button></div></details>
      <label>{report_icon("server")}<span>Equipamentos:</span><select name="equipment_id"><option value="">Todos</option>{equipment_options}</select></label>
      <label>{report_icon("shield")}<span>Status:</span><select name="status"><option value="">Todos</option><option value="success" {"selected" if filters.status == "success" else ""}>Concluídos</option><option value="failed" {"selected" if filters.status == "failed" else ""}>Falhas</option></select></label>
      <button class="secondary" type="button" onclick="this.closest('form').querySelector('details').toggleAttribute('open')">{report_icon("grid")}Alterar filtros</button><button type="submit">✓ Aplicar</button>
    </form>'''
    content = (report_header("/reports", "Relatórios", "Acompanhe a operação, identifique falhas e acesse rapidamente os relatórios por categoria.") +
               overview_filters + f'<section class="stats report-stats report-overview-stats">{cards}</section>'
               f'<section class="report-overview-main"><article class="panel report-overview-activity"><div class="panel-heading"><div><h2>{report_icon("activity")}Atividade dos backups</h2><p>Desempenho dos backups no período selecionado.</p></div><div class="report-chart-legend"><span><i class="activity-success"></i>Concluídos</span><span><i class="activity-failure"></i>Falhas</span></div></div><svg viewBox="0 0 500 170" role="img" aria-label="Atividade dos backups"><path class="report-chart-grid" d="M30 45H470M30 80H470M30 115H470M30 150H470"/><line class="report-chart-axis" x1="30" y1="30" x2="30" y2="150"/><g class="report-chart-scale">{chart_scale}</g><polyline class="report-line-success" points="{success_points}"/><polyline class="report-line-failure" points="{failure_points}"/>{success_dots}{failure_dots}</svg><div class="report-line-labels">{chart_labels}</div></article>'
               f'<aside class="report-overview-side"><article class="panel"><h2>{report_icon("bolt")}Acesso rápido</h2><div class="report-quick-grid">{quick_links}</div></article><article class="panel"><h2>{report_icon("bell")}Alertas operacionais</h2><div class="report-alert-list">{alert_lines}</div></article></aside></section>'
               f'<section class="report-overview-bottom"><article class="panel report-failure-ranking"><h2>{report_icon("server")}Equipamentos com mais falhas</h2><div class="report-ranking-head"><span>Equipamento</span><span>Falhas</span></div><ul class="report-ranking">{failure_lines}</ul><a class="report-panel-link" href="/reports/equipment">Ver todos os equipamentos ›</a></article><article class="panel report-method-panel"><h2>{report_icon("pie")}Distribuição por método</h2><div class="report-method-chart"><div class="report-method-donut" style="--method-gradient:{method_gradient}"><span><strong>{method_total}</strong><small>backups</small></span></div><ul class="report-method-list">{method_lines}</ul></div><a class="report-panel-link" href="/reports/backups">Ver detalhes ›</a></article><article class="panel report-operation-summary"><h2>{report_icon("shield")}Resumo da operação</h2><ul><li><span>Equipamentos ativos</span><strong>{data["equipment_active"]}</strong></li><li><span>Backups nas últimas 24h</span><strong>{data["backups_24h"]}</strong></li><li><span>Uploads recebidos via FTP</span><strong>{ftp_data["uploads"] or 0}</strong></li></ul><a class="report-panel-link" href="/reports/equipment">Ver situação dos equipamentos ›</a></article></section>')
    return render("Relatórios - Visão Geral", content, user)


def reports_backups_page(environ: dict) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    _, filters = report_query(environ)
    with connect() as conn:
        rows, total = backup_report(conn, filters)
        options = report_choices(conn)
    status_labels = {"success": "Concluído", "failed": "Falhou", "partial": "Parcial", "running": "Em execução", "available": "Disponível"}
    method_labels = {"ssh": "Backup via SSH", "ftp": "Recebimento FTP", "manual": "Importação manual", "rclone": "rclone", "telegram": "Telegram"}
    lines = "".join(
        f'<tr><td><strong>{e(local_dt(row["occurred_at"]))}</strong></td>'
        f'<td><div class="report-table-primary report-backups-equipment"><span>{report_icon("server")}</span><div><strong>{e(row["hostname"])}</strong><small>{e(row["vendor"] or "Fabricante não informado")}</small></div></div></td>'
        f'<td><div class="report-method-cell"><span class="report-method-icon">{report_icon("archive")}</span><div><strong>{e(method_labels.get(row["method"], row["method"]))}</strong><span>{e(row["origin"] or "Origem não informada")}</span></div></div></td>'
        f'<td><span class="badge status-{e(row["status"])}">{e(status_labels.get(row["status"], row["status"]))}</span></td>'
        f'<td>{e(duration_label(row["duration_ms"]))}</td><td><strong>{format_size(row["file_size"])}</strong></td>'
        f'<td><details class="report-row-details report-backup-details"><summary>Ver detalhes</summary><div><span><b>Grupo do equipamento</b><strong>{e(row["group_name"] or "Não informado")}</strong><small>Grupo administrativo ao qual o equipamento pertence.</small></span><span><b>POP / localização</b><strong>{e(row["pop"] or "Não informado")}</strong><small>Ponto de presença associado ao equipamento.</small></span><span><b>Destino do backup</b><strong>{e(row["destination"] or "Armazenamento local")}</strong><small>Local definido para armazenamento do arquivo.</small></span><span><b>Executado por</b><strong>{e(row["operator"] or "Rotina automática")}</strong><small>Usuário ou processo responsável pela execução.</small></span><span class="wide"><b>Identificação de integridade (SHA-256)</b><code title="{e(row["sha256"] or "")}">{e(short_identifier(row["sha256"], 20) or "Não disponível")}</code><small>Resumo do código usado para validar a integridade do arquivo.</small></span></div></details></td><td hidden>{e(row["hostname"])}</td></tr>' for row in rows
    ) or f'<tr><td colspan="7"><div class="report-backups-empty"><span>{report_icon("archive")}</span><div><strong>Nenhum registro encontrado</strong><p>Não há backups correspondentes aos filtros selecionados.</p></div><a class="button secondary" href="/reports/backups">Limpar filtros</a></div></td></tr>'
    content = ('<section class="report-reference-page report-backups-reference">' +
               report_header("/reports/backups", "Backups", "Consulte as execuções e os arquivos registrados no período selecionado.") +
               report_filter_form("/reports/backups", filters, options, method=True) +
               f'<section class="panel table-panel report-table-panel report-backups-results"><div class="panel-heading"><span class="report-backups-panel-icon">{report_icon("archive")}</span><div><span class="eyebrow">Resultados</span><h2>Backups encontrados</h2><p>Execuções e arquivos registrados no período selecionado.</p></div><span class="report-backups-total">{total} {"registro" if total == 1 else "registros"}</span></div><div class="report-backups-table-wrap"><table class="report-modern-table"><thead><tr><th>Data e hora</th><th>Equipamento</th><th>Método e origem</th><th>Status</th><th>Duração</th><th>Tamanho</th><th></th></tr></thead><tbody>{lines}</tbody></table></div>{report_pager("/reports/backups", filters, total)}</section></section>')
    return render("Relatórios - Backups", content, user)


def reports_equipment_page(environ: dict) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    _, filters = report_query(environ)
    with connect() as conn:
        rows, total = equipment_report(conn, filters)
        gaps = equipment_gap_summary(conn, filters)
        options = report_choices(conn)
    lines = "".join(
        f'<tr><td><div class="report-table-primary report-equipment-cell"><span>{report_icon("server")}</span><div><strong>{e(row["hostname"])}</strong><small>{"Monitoramento ativo" if row["is_active"] else "Equipamento arquivado"}</small></div></div></td>'
        f'<td><span class="badge {"status-success" if row["is_active"] else "status-disabled"}">{"Ativo" if row["is_active"] else "Arquivado"}</span></td>'
        f'<td><div class="report-table-primary"><strong>{e(local_dt(row["last_backup"]))}</strong><span>{e(str(row["days_without_backup"]) + " dia(s) sem backup" if row["last_backup"] else "Nenhum backup registrado")}</span></div></td>'
        f'<td><strong>{row["backup_count"]}</strong></td><td><strong class="{"report-value-danger" if row["failure_count"] else ""}">{row["failure_count"]}</strong></td>'
        f'<td><details class="report-row-details"><summary>Detalhes</summary><div><span><b>Última falha</b>{e(local_dt(row["last_failure"]))}</span><span><b>Falhas consecutivas</b>{row["consecutive_failures"]}</span><span><b>Última restauração</b>{e(local_dt(row["last_restore"]))}</span></div></details></td></tr>' for row in rows
    ) or '<tr><td colspan="6" class="report-table-empty">Nenhum equipamento encontrado.</td></tr>'

    gap_cards = "".join(f'<article class="stat report-equipment-stat"><span class="report-stat-icon">{report_icon(icon)}</span><div><span>{e(label)}</span><strong>{e(value)}</strong><small>{e(detail)}</small></div></article>' for label, value, icon, detail in (
        ("Sem backup há 24h", gaps["h24"], "archive", "Requer acompanhamento"),
        ("Sem backup há 48h", gaps["h48"], "shield", "Atenção operacional"),
        ("Sem backup há 7 dias", gaps["d7"], "server", "Situação crítica"),
        ("Backup mais antigo", local_dt(gaps["oldest_backup"]) if gaps["oldest_backup"] else "Nenhum", "database", f'{gaps["h72"]} sem backup há 72h'),
    ))
    content = ('<section class="report-reference-page report-equipment-reference">' +
               report_header("/reports/equipment", "Equipamentos", "Acompanhe a continuidade dos backups e identifique equipamentos que exigem atenção.") +
               report_filter_form("/reports/equipment", filters, options) +
               f'<section class="stats report-stats report-equipment-stats">{gap_cards}</section>' +
               f'<section class="panel table-panel report-table-panel report-equipment-continuity"><div class="panel-heading"><span class="report-equipment-panel-icon">{report_icon("server")}</span><div><span class="eyebrow">Continuidade</span><h2>Situação dos equipamentos</h2><p>Acompanhamento do último backup e das ocorrências registradas.</p></div><span class="report-equipment-total">{total} {"equipamento" if total == 1 else "equipamentos"}</span></div><div class="report-equipment-table-wrap"><table class="report-modern-table"><thead><tr><th>Equipamento</th><th>Situação</th><th>Último backup</th><th>Backups</th><th>Falhas</th><th></th></tr></thead><tbody>{lines}</tbody></table></div>{report_pager("/reports/equipment", filters, total)}</section></section>')
    return render("Relatórios - Equipamentos", content, user)


def reports_storage_page(environ: dict) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    _, filters = report_query(environ)
    with connect() as conn:
        data = storage_report(conn, filters, get_setting(conn, "storage_root", "/"))
        options = report_choices(conn)
    metrics = (("Capacidade total", data["disk_total"], "database", f'{format_size(data["disk_free"])} disponíveis'),
               ("Espaço ocupado", data["disk_used"], "archive", "Uso atual do disco"),
               ("Backups locais", data["local_bytes"], "server", f'{format_size(data["ftp_bytes"])} recebidos por FTP'),
               ("rclone", data["synced_bytes"], "rclone", "Arquivos sincronizados"),
               ("Lixeira", data["trash_bytes"], "shield", f'{format_size(data["rejected_bytes"])} rejeitados pelo FTP'),
               ("Tamanho médio", int(data["average"] or 0), "grid", f'Maior arquivo: {format_size(data["largest"])}'))
    cards = "".join(f'<article class="stat report-equipment-stat"><span class="report-stat-icon">{report_icon(icon)}</span><div><span>{e(label)}</span><strong>{format_size(value)}</strong><small>{e(detail)}</small></div></article>' for label, value, icon, detail in metrics)
    used_percent = 100 * int(data["disk_used"]) / max(1, int(data["disk_total"]))
    chart_percent = min(100.0, max(0.0, used_percent))
    content = ('<section class="report-reference-page report-storage-reference">' + report_header("/reports/storage", "Armazenamento", "Visualize a ocupação do disco e a distribuição dos arquivos armazenados.") +
               report_filter_form("/reports/storage", filters, options) + f'<section class="stats report-stats report-storage-stats">{cards}</section>'
               f'<section class="panel report-storage-panel report-storage-modern"><div class="panel-heading"><span class="report-storage-panel-icon">{report_icon("database")}</span><div><span class="eyebrow">Capacidade</span><h2>Uso do armazenamento</h2><p>Espaço utilizado em relação à capacidade total disponível.</p></div><span class="report-storage-health"><i></i>Monitoramento ativo</span></div><div class="report-storage-body"><div class="report-storage-donut" style="--storage-used:{chart_percent * 3.6:.2f}deg"><span><strong>{used_percent:.1f}%</strong><small>utilizado</small></span></div><div class="report-storage-breakdown"><div class="report-storage-track"><i style="width:{chart_percent:.1f}%"></i></div><div class="report-storage-labels"><span><i class="used"></i><small>Espaço utilizado</small><strong>{format_size(data["disk_used"])}</strong></span><span><i class="free"></i><small>Espaço disponível</small><strong>{format_size(data["disk_free"])}</strong></span><span><i class="total"></i><small>Capacidade total</small><strong>{format_size(data["disk_total"])}</strong></span></div></div></div></section></section>')
    return render("Relatórios - Armazenamento", content, user)


def reports_ftp_page(environ: dict) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    _, filters = report_query(environ)
    with connect() as conn:
        data = ftp_report(conn, filters)
        options = report_choices(conn)
    metrics = (("Contas FTP", data["accounts"], "server", "Contas vinculadas"), ("Uploads recebidos", data["uploads"] or 0, "upload", f'Último: {local_dt(data["last_upload"])}'),
               ("Arquivos importados", data["imported"] or 0, "archive", f'Último: {local_dt(data["last_import"])}'),
               ("Arquivos rejeitados", data["rejected"] or 0, "reject", "Não processados"), ("Falhas", data["failed"] or 0, "alert", "Falhas de processamento"))
    cards = "".join(f'<article class="stat report-equipment-stat"><span class="report-stat-icon">{report_icon(icon)}</span><div><span>{e(label)}</span><strong>{e(value)}</strong><small>{e(detail)}</small></div></article>' for label, value, icon, detail in metrics)
    top = "".join(f'<tr><td><div class="report-table-primary report-ftp-equipment"><span>{report_icon("server")}</span><div><strong>{e(row["hostname"])}</strong><small>Equipamento vinculado</small></div></div></td><td><strong class="report-ftp-count">{row["uploads"]}</strong></td><td><time class="report-ftp-date">{e(local_dt(row["last_upload"]))}</time></td></tr>' for row in data["top"])
    ftp_body = top or f'''<tr><td colspan="3"><div class="report-ftp-empty"><span>{report_icon('upload')}</span><div><strong>Nenhum upload no período</strong><p>Não há arquivos recebidos por FTP com os filtros selecionados.</p></div><a class="button secondary" href="/ftp">Abrir recebimento por FTP</a></div></td></tr>'''
    content = ('<section class="report-reference-page report-ftp-reference">' + report_header("/reports/ftp", "FTP", "Acompanhe as contas e o processamento dos arquivos recebidos por FTP.") +
               report_filter_form("/reports/ftp", filters, options) + f'<section class="stats report-stats report-ftp-stats">{cards}</section>'
               f'<section class="panel table-panel report-table-panel report-ftp-movement"><div class="panel-heading"><span class="report-ftp-panel-icon">{report_icon("upload")}</span><div><span class="eyebrow">Movimentação</span><h2>Equipamentos com mais uploads</h2><p>Volume de recebimentos por equipamento no período selecionado.</p></div><span class="report-ftp-total">{data["uploads"] or 0} uploads</span></div><div class="report-ftp-table-wrap"><table class="report-modern-table"><thead><tr><th>Equipamento</th><th>Uploads</th><th>Último upload</th></tr></thead><tbody>{ftp_body}</tbody></table></div></section></section>')
    return render("Relatórios - FTP", content, user)


def reports_telegram_page(environ: dict) -> Response:
    user, response = require_user(environ)
    if response: return response
    _, filters = report_query(environ)
    with connect() as conn:
        data = telegram_report(conn, filters); options = report_choices(conn)
    rows = "".join(f"<tr><td><div class=\"report-table-primary report-telegram-equipment\"><span>{report_icon('server')}</span><div><strong>{e(row['hostname'])}</strong><small>Equipamento vinculado</small></div></div></td><td><strong class=\"report-telegram-count\">{row['sent']}</strong></td><td><span class=\"report-telegram-volume\">{format_size(row['bytes_sent'])}</span></td></tr>" for row in data["top_equipment"])
    delivery_body = rows or f'''<tr><td colspan="3"><div class="report-telegram-empty"><span>{report_icon('send')}</span><div><strong>Nenhum envio no período</strong><p>Não há arquivos encaminhados ao Telegram com os filtros selecionados.</p></div><a class="button secondary" href="/telegram-backup">Abrir cópia no Telegram</a></div></td></tr>'''
    metrics = (("Arquivos enviados", data["sent"], "send", f'{format_size(data["bytes_sent"])} transferidos'), ("Taxa de sucesso", f"{data['success_rate']:.1f}%", "shield", "Entregas concluídas"), ("Falhas", data["failed"], "alert", "Envios não concluídos"), ("Ignorados", data["skipped"], "archive", "Envios dispensados"))
    cards = "".join(f'<article class="stat report-equipment-stat"><span class="report-stat-icon">{report_icon(icon)}</span><div><span>{e(label)}</span><strong>{e(value)}</strong><small>{e(detail)}</small></div></article>' for label, value, icon, detail in metrics)
    content = ('<section class="report-reference-page report-telegram-reference">' + report_header("/reports/telegram", "Telegram", "Acompanhe as cópias secundárias enviadas pelo Telegram.") +
        report_filter_form("/reports/telegram", filters, options) + f'<section class="stats report-stats report-telegram-stats">{cards}</section>' +
        f'<section class="panel table-panel report-table-panel report-telegram-deliveries"><div class="panel-heading"><span class="report-telegram-panel-icon">{report_icon("send")}</span><div><span class="eyebrow">Entregas</span><h2>Equipamentos com mais envios</h2><p>Arquivos encaminhados como cópia secundária no período selecionado.</p></div><span class="report-telegram-total">{data["sent"]} enviados</span></div><div class="report-telegram-table-wrap"><table class="report-modern-table"><thead><tr><th>Equipamento</th><th>Envios</th><th>Volume transferido</th></tr></thead><tbody>{delivery_body}</tbody></table></div></section></section>')
    return render("Relatórios - Telegram", content, user)


def reports_audit_page(environ: dict) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    _, filters = report_query(environ)
    with connect() as conn:
        rows, total = audit_report(conn, filters)
        options = report_choices(conn)
        equipment_names = {str(row["id"]): row["hostname"] for row in conn.execute("SELECT id,hostname FROM equipment")}
        audit_metrics = conn.execute("""
            SELECT
              SUM(CASE WHEN created_at >= datetime('now','-24 hours') AND action!='cloud.worker_executed' THEN 1 ELSE 0 END) recent,
              COUNT(DISTINCT CASE WHEN user_id IS NOT NULL THEN user_id END) users,
              SUM(CASE WHEN (action LIKE '%failed%' OR action LIKE '%deleted%' OR action LIKE '%purged%')
                       AND action!='cloud.worker_executed' THEN 1 ELSE 0 END) attention,
              SUM(CASE WHEN user_id IS NULL AND action!='cloud.worker_executed' THEN 1 ELSE 0 END) system_events
            FROM audit_log
        """).fetchone()
    event_names = {
        "scheduler.executed": "Agendador executado", "backup.created": "Backup criado",
        "backup.downloaded": "Backup baixado", "equipment.created": "Equipamento criado",
        "equipment.updated": "Equipamento atualizado", "equipment.deleted": "Equipamento excluído",
        "user.login": "Acesso realizado", "user.logout": "Sessão encerrada",
        "settings.general_updated": "Painel atualizado", "setup_saved": "Configuração inicial concluída",
        "configuration.imported": "Configuração importada", "configuration.exported": "Configuração exportada",
    }
    audit_lines = []
    for row in rows:
        description = row["details"] or "Nenhum detalhe adicional"
        detail_data = {}
        try:
            detail_data = json.loads(description)
        except (TypeError, ValueError, json.JSONDecodeError):
            detail_data = {}
        if not isinstance(detail_data, dict):
            detail_data = {}
        equipment_id = detail_data.get("equipment_id")
        if not equipment_id and row["entity"] == "equipment":
            equipment_id = row["entity_id"]
        equipment_name = (detail_data.get("hostname") or detail_data.get("name") or
                          equipment_names.get(str(equipment_id or "")) or "equipamento")
        action = row["action"]
        if action == "scheduler.executed":
            queued = int(detail_data.get("queued", 0))
            description = f'{queued} {"backup foi enviado" if queued == 1 else "backups foram enviados"} para a fila.'
        elif action in {"created", "equipment.created"} and row["entity"] == "equipment":
            description = f"Cadastrou o equipamento {equipment_name}."
        elif action == "equipment.updated":
            description = f"Alterou os dados do equipamento {equipment_name}."
        elif action in {"equipment.deleted", "equipment.purged"}:
            description = f"Excluiu o equipamento {equipment_name}."
        elif action == "equipment.deactivated":
            description = f"Desativou o equipamento {equipment_name}."
        elif action in {"job.manual_queued", "olt.backup_triggered"}:
            description = f"Enviou um backup do equipamento {equipment_name} para execução."
        elif action == "job.run_success":
            description = f"Backup do equipamento {equipment_name} concluído com sucesso."
        elif action in {"job.run_failed", "job.run_timeout"}:
            description = f"O backup do equipamento {equipment_name} não foi concluído."
        elif action == "backup.downloaded":
            description = f"Baixou um backup do equipamento {equipment_name}."
        elif action == "backup.moved_to_trash":
            description = f"Moveu um backup do equipamento {equipment_name} para a lixeira."
        elif action.startswith("credential.test_"):
            description = f"Testou a credencial do equipamento {equipment_name}."
        elif action.startswith("credential."):
            description = f"Alterou a credencial do equipamento {equipment_name}."
        elif action.startswith("ftp.") or action.startswith("mikrotik_ftp."):
            description = f"Alterou a configuração FTP do equipamento {equipment_name}."
        elif action == "user.created":
            description = f'Cadastrou o usuário {row["entity_id"] or "informado"}.'
        elif action == "user.updated":
            description = "Alterou os dados de um usuário."
        elif action in {"login", "user.login"}:
            description = "Acessou o painel."
        elif action in {"logout", "user.logout"}:
            description = "Encerrou a sessão no painel."
        elif action == "password_changed":
            description = "Alterou a senha de acesso ao painel."
        elif action in {"settings.general_updated", "setup_saved"}:
            description = "Alterou as configurações gerais do painel."
        elif action == "configuration.imported":
            description = "Importou uma configuração para o painel."
        elif action == "configuration.exported":
            description = "Exportou as configurações do painel."
        elif action.startswith("settings."):
            description = "Alterou uma configuração do painel."
        elif detail_data:
            description = "Ação registrada com sucesso."
        action_group = ("danger" if any(term in action for term in ("failed", "deleted", "purged", "rejected"))
                        else "success" if any(term in action for term in ("success", "created", "login", "restored"))
                        else "system" if row["username"] == "Sistema" else "info")
        actor_initial = "S" if row["username"] == "Sistema" else str(row["username"] or "U")[:1].upper()
        audit_lines.append(
            f'<tr class="audit-row audit-{action_group}"><td><div class="audit-date"><strong>{e(local_dt(row["created_at"]))}</strong><span>Registro #{row["id"]}</span></div></td>'
            f'<td><div class="audit-actor"><i>{e(actor_initial)}</i><div class="report-table-primary"><strong>{e(row["username"])}</strong><span>{e(row["ip_address"] or "Ação interna do sistema")}</span></div></div></td>'
            f'<td><span class="report-event-label">{report_icon("settings" if action_group == "system" else "alert" if action_group == "danger" else "check_circle" if action_group == "success" else "shield")}{e(event_names.get(row["action"], str(row["action"]).replace(".", " · ")))}</span></td>'
            f'<td class="report-audit-description">{e(description)}</td></tr>'
        )
    lines = "".join(audit_lines) or '<tr><td colspan="4" class="report-table-empty">Nenhum evento de auditoria encontrado.</td></tr>'
    metric_cards = "".join((
        f'<article class="stat report-audit-stat"><span class="report-stat-icon">{report_icon("shield")}</span><div><span>Eventos encontrados</span><strong>{total}</strong><small>Com os filtros atuais</small></div></article>',
        f'<article class="stat report-audit-stat"><span class="report-stat-icon">{report_icon("clock")}</span><div><span>Últimas 24 horas</span><strong>{int(audit_metrics["recent"] or 0)}</strong><small>Atividade recente</small></div></article>',
        f'<article class="stat report-audit-stat"><span class="report-stat-icon">{report_icon("user")}</span><div><span>Responsáveis</span><strong>{int(audit_metrics["users"] or 0)}</strong><small>Usuários identificados</small></div></article>',
        f'<article class="stat report-audit-stat"><span class="report-stat-icon">{report_icon("alert")}</span><div><span>Requer atenção</span><strong>{int(audit_metrics["attention"] or 0)}</strong><small>Falhas e exclusões</small></div></article>',
    ))
    content = ('<section class="report-reference-page report-audit-reference">' + report_header("/reports/audit", "Auditoria", "Consulte as ações administrativas realizadas e suas respectivas origens.") +
               f'<section class="stats report-stats report-audit-stats">{metric_cards}</section>' +
               report_filter_form("/reports/audit", filters, options, audit=True, status=False) +
               f'<section class="panel table-panel report-table-panel report-audit-panel"><div class="panel-heading"><div><span class="eyebrow">Rastreabilidade</span><h2>Eventos registrados</h2><p>{total} {"evento encontrado" if total == 1 else "eventos encontrados"}. Rotinas técnicas repetitivas ficam ocultas por padrão.</p></div><span class="audit-live-indicator"><i></i>Registro contínuo</span></div><div class="report-audit-table-wrap"><table class="report-modern-table report-audit-table"><thead><tr><th>Data e hora</th><th>Responsável</th><th>Evento</th><th>Resumo</th></tr></thead><tbody>{lines}</tbody></table></div>{report_pager("/reports/audit", filters, total)}</section></section>')
    return render("Relatórios - Auditoria", content, user)


def reports_exports_page(environ: dict) -> Response:
    user, response = require_user(environ)
    if response:
        return response
    _, filters = report_query(environ)
    with connect() as conn:
        options = report_choices(conn)
    allowed = bool(user["can_download"] or user["can_admin"])
    common = "".join(hidden(key, value) for key, value in report_filter_values(filters, include_page=False).items())
    export_form = f'''<section class="panel report-export-modern"><header><span class="report-export-icon">{report_icon("download")}</span><div><span class="eyebrow">Gerar arquivo</span><h2>Nova exportação</h2><p>Escolha o relatório e o formato mais adequado para uso dos dados.</p></div><span class="report-export-ready"><i></i>Pronto para exportar</span></header><form method="get" action="/reports/export" class="report-export-modern-form">
      {common}
      <div class="report-export-source"><label>Conteúdo do arquivo<select name="report"><option value="backups">Relatório de Backups</option><option value="equipment">Relatório de Equipamentos</option><option value="audit">Relatório de Auditoria</option></select><small>Os filtros definidos acima serão aplicados ao arquivo.</small></label><label>Separador do CSV<select name="separator"><option value=";">Ponto e vírgula (;)</option><option value=",">Vírgula (,)</option><option value="tab">Tabulação</option></select><small>Utilizado somente quando o formato CSV for escolhido.</small></label></div>
      <fieldset class="report-export-formats"><legend>Formato do arquivo</legend><label><input type="radio" name="format" value="csv" checked><span class="format-csv"><i>{report_icon("archive")}</i><strong>CSV</strong><small>Leve e compatível com planilhas e integrações.</small><b>Selecionar</b></span></label><label><input type="radio" name="format" value="xlsx"><span class="format-xlsx"><i>{report_icon("grid")}</i><strong>Excel</strong><small>Planilha formatada com filtros e colunas ajustadas.</small><b>Selecionar</b></span></label><label><input type="radio" name="format" value="pdf"><span class="format-pdf"><i>{report_icon("download")}</i><strong>PDF</strong><small>Documento pronto para leitura e compartilhamento.</small><b>Selecionar</b></span></label></fieldset>
      <footer><span>{report_icon("shield")}A exportação respeita suas permissões e os filtros selecionados.</span><button type="submit">{report_icon("download")}Gerar exportação</button></footer>
    </form></section>''' if allowed else '<div class="notice info"><span>Seu perfil permite consultar relatórios, mas não exportá-los.</span></div>'
    content = (report_header("/reports/exports", "Exportações", "Gere arquivos dos relatórios aplicando o período e os filtros desejados.") +
               report_filter_form("/reports/exports", filters, options, method=True) + export_form)
    return render("Relatórios - Exportações", content, user)


def reports_export(environ: dict) -> Response:
    user, response = require_download(environ)
    if response:
        return response
    raw, filters = report_query(environ)
    report_name = raw.get("report", "backups")
    if report_name not in {"backups", "equipment", "audit"}:
        report_name = "backups"
    export_format = raw.get("format", "csv")
    if export_format not in {"csv", "xlsx", "pdf"}:
        return render("Formato inválido", "<p>Formato de exportação inválido.</p>", user, HTTPStatus.BAD_REQUEST)
    with connect() as conn:
        headers, rows = export_dataset(conn, report_name, filters)
        company = filters.company or get_setting(conn, "provider_name", get_setting(conn, "installation_name", "Backup Manager Local"))
    period = f'{filters.start or "início"} a {filters.end or "agora"}'
    timestamp = now_utc().strftime("%Y%m%d-%H%M%S")
    if export_format == "csv":
        separator = "\t" if raw.get("separator") == "tab" else raw.get("separator", ";")
        payload, content_type = csv_export(headers, rows, separator), "text/csv; charset=utf-8"
    elif export_format == "xlsx":
        payload, content_type = xlsx_export(headers, rows, report_name.title()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        payload = pdf_export(headers, rows, title=f"Relatório de {report_name.title()}", company=company, period=period)
        content_type = "application/pdf"
    return Response(payload, HTTPStatus.OK, [
        ("Content-Type", content_type),
        ("Content-Disposition", f'attachment; filename="relatorio-{report_name}-{timestamp}.{export_format}"'),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
    ])


def audit_page(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response:
        return response
    with connect() as conn:
        auth_rows = conn.execute("""SELECT a.*,u.username FROM audit_log a LEFT JOIN users u ON u.id=a.user_id
            WHERE a.action IN ('login','login_failed') ORDER BY a.id DESC LIMIT 20""").fetchall()
        failed_10m = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='login_failed' AND created_at>=datetime('now','-10 minutes')").fetchone()[0]
        failed_24h = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='login_failed' AND created_at>=datetime('now','-24 hours')").fetchone()[0]
        distinct_ips = conn.execute("SELECT COUNT(DISTINCT ip_address) FROM audit_log WHERE action='login_failed' AND created_at>=datetime('now','-24 hours') AND ip_address!=''").fetchone()[0]
        top_ips = conn.execute("""SELECT ip_address,COUNT(*) attempts,MAX(created_at) last_at FROM audit_log
            WHERE action='login_failed' AND created_at>=datetime('now','-24 hours') GROUP BY ip_address ORDER BY attempts DESC,last_at DESC LIMIT 5""").fetchall()
        top_accounts = conn.execute("""SELECT COALESCE(NULLIF(entity_id,''),'Não informado') account,COUNT(*) attempts,
            COUNT(DISTINCT ip_address) ips,MAX(created_at) last_at FROM audit_log WHERE action='login_failed'
            AND created_at>=datetime('now','-24 hours') GROUP BY account ORDER BY attempts DESC,last_at DESC LIMIT 5""").fetchall()
    state = "Atenção" if failed_10m else "Normal"
    state_class = "warning" if failed_10m else "success"
    def geo_cells(address: str) -> tuple[str, str]:
        flag, country, asn = ip_geo_summary(address or "")
        marker = (f'<i class="security-local-icon">{report_icon("server")}</i>' if flag == "xx"
                  else f'<img src="/static/assets/flags/{e(flag)}.svg" alt="" loading="lazy">')
        return f'<span class="security-country">{marker}{e(country)}</span>', e(asn)

    ip_lines_parts = []
    for row in top_ips:
        country_html, asn = geo_cells(row["ip_address"] or "")
        ip_lines_parts.append(f'<tr><td><strong>{e(row["ip_address"] or "Não informado")}</strong></td><td>{country_html}</td><td>{asn}</td><td>{row["attempts"]}</td><td>{e(local_dt(row["last_at"]))}</td></tr>')
    ip_lines = "".join(ip_lines_parts) or '<tr><td colspan="5" class="report-table-empty">Nenhuma tentativa registrada.</td></tr>'
    account_lines = "".join(f'<tr><td><strong>{e(row["account"])}</strong></td><td>{row["attempts"]}</td><td>{row["ips"]}</td><td>{e(local_dt(row["last_at"]))}</td></tr>' for row in top_accounts) or '<tr><td colspan="4" class="report-table-empty">Nenhuma conta visada.</td></tr>'
    auth_lines = []
    for row in auth_rows:
        country_html, asn = geo_cells(row["ip_address"] or "")
        event_valid = row["action"] == "login"
        event_label = "Login válido" if event_valid else "Tentativa inválida"
        date_value = local_dt(row["created_at"])
        account = row["username"] or row["entity_id"] or "Não informado"
        auth_lines.append(f'''<tr class="{'auth-valid' if event_valid else 'auth-invalid'}"><td><time class="security-event-date">{e(date_value)}</time></td><td><span class="security-event-type">{report_icon('shield')}{event_label}</span></td><td><strong class="security-event-account">{e(account)}</strong></td><td><code class="security-event-ip">{e(row['ip_address'] or '-')}</code></td><td>{country_html}</td><td><span class="security-event-asn">{asn}</span></td></tr>''')
    body = "".join(auth_lines) or '<tr><td colspan="6" class="report-table-empty">Nenhum evento de autenticação.</td></tr>'
    content = f"""
    <section class="security-center-page" id="security-report">
      <header class="security-center-head"><span>{report_icon('shield')}</span><div><small>Proteção de acesso</small><h1>Central de segurança</h1><p>Análise automática de tentativas por IP, conta e intervalo de tempo.</p></div><aside class="status-{state_class}"><i></i><strong>{state}</strong><small>Estado atual</small></aside></header>
      <section class="security-kpis"><article>{report_icon('alert')}<div><strong>{failed_10m}</strong><span>Falhas em 10 minutos</span></div></article><article>{report_icon('clock')}<div><strong>{failed_24h}</strong><span>Falhas nas últimas 24h</span></div></article><article>{report_icon('grid')}<div><strong>{distinct_ips}</strong><span>Endereços IP distintos</span></div></article><article>{report_icon('shield')}<div><strong>0</strong><span>Acessos bloqueados</span></div></article></section>
      <section class="security-rankings"><article class="panel"><header>{report_icon('activity')}<div><h2>IPs com mais tentativas</h2><p>Origens mais ativas nas últimas 24 horas</p></div></header><table><thead><tr><th>Endereço IP</th><th>País</th><th>ASN / Operadora</th><th>Tentativas</th><th>Última atividade</th></tr></thead><tbody>{ip_lines}</tbody></table></article><article class="panel"><header>{report_icon('user')}<div><h2>Contas mais visadas</h2><p>Usuários escolhidos nas tentativas de acesso</p></div></header><table><thead><tr><th>Conta</th><th>Tentativas</th><th>IPs</th><th>Última atividade</th></tr></thead><tbody>{account_lines}</tbody></table></article></section>
      <section class="panel security-auth-events"><header><span>{report_icon('activity')}</span><div><small>Monitoramento de acesso</small><h2>Eventos recentes de autenticação</h2><p>As 20 ocorrências mais recentes; sucessos e falhas permanecem destacados.</p></div><a class="button secondary" href="/reports/audit">Ver histórico completo</a></header><div><table><thead><tr><th>Data e hora</th><th>Evento</th><th>Conta</th><th>IP de origem</th><th>País</th><th>ASN / Operadora</th></tr></thead><tbody>{body}</tbody></table></div><footer><span>{report_icon('shield')} Registro contínuo de autenticação</span><a href="/reports/audit">Consultar todos os eventos <b>→</b></a></footer></section>
    </section>
    """
    return render("Auditoria", content, user)


def telegram_backup_page(environ: dict) -> Response:
    user, response = require_user(environ)
    if response: return response
    view = parse_qs(environ.get("QUERY_STRING", "")).get("view", ["destinations"])[-1]
    if view not in {"settings", "destinations", "policies", "queue", "history", "topics"}: view = "settings"
    csrf = session_csrf(environ)
    with connect() as conn:
        content = telegram_backup_page_content(
            conn,
            user=user,
            view=view,
            csrf=csrf,
            local_dt=local_dt,
            can_operate=can_operate,
            format_size=format_size,
            sanitize_chat_id=sanitize_chat_id,
            render=render,
            get_setting=get_setting,
        )
    return content


def _telegram_form(environ: dict, admin=True):
    user, response = (require_admin(environ) if admin else require_operator(environ))
    if response: return user, response, None
    form = parse_form(environ)
    if not valid_session_csrf(environ, form.get("csrf_token", "")):
        return user, render("Requisição recusada", "<p>Token CSRF inválido.</p>", user, HTTPStatus.FORBIDDEN), None
    return user, None, form


def handle_telegram_destination_create(environ: dict) -> Response:
    user, response, form = _telegram_form(environ)
    if response: return response
    try:
        thread = int(form["default_thread_id"]) if form.get("default_thread_id") else None
        with connect() as conn:
            telegram_create_destination(conn, name=form.get("name", ""), chat_id=form.get("chat_id", ""), destination_type=form.get("destination_type", "private"), default_thread_id=thread,
                flags={key: form.get(key) == "1" for key in ("alerts", "daily", "weekly", "executive", "backup_files")}, user_id=user["id"])
    except (ValueError, sqlite3.IntegrityError) as exc: return redirect_with_message("/telegram-backup", "error", str(exc))
    return redirect_with_message("/telegram-backup", "success", "Destino Telegram criado.")


def handle_telegram_policy_create(environ: dict) -> Response:
    user, response, form = _telegram_form(environ)
    if response: return response
    try:
        scope = form.get("scope_type", "global"); group_id = int(form["group_id"]) if form.get("group_id") else None; equipment_id = int(form["equipment_id"]) if form.get("equipment_id") else None
        if scope == "group" and not group_id: raise ValueError("Selecione o grupo.")
        if scope == "equipment" and not equipment_id: raise ValueError("Selecione o equipamento.")
        with connect() as conn:
            policy_uuid = str(uuid.uuid4())
            conn.execute("""INSERT INTO telegram_backup_policies(uuid,scope_type,group_id,equipment_id,destination_id,thread_id,
                include_ssh,include_ftp,include_manual,send_new_backups,compression_mode,compression_level,all_artifacts,file_types)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (policy_uuid, scope, group_id if scope=="group" else None, equipment_id if scope=="equipment" else None,
                int(form["destination_id"]), int(form["thread_id"]) if form.get("thread_id") else None, 1, 1, 1, int("send_new_backups" in form),
                form.get("compression_mode", "none"), int(form.get("compression_level", "6")), int("all_artifacts" in form), form.get("file_types", "")[:200]))
            set_setting(conn, "telegram_backup_enabled", "1")
            audit(conn, user["id"], "telegram.backup_policy_created", "telegram_backup_policy", policy_uuid, json.dumps({"scope": scope}), environ.get("REMOTE_ADDR", ""))
    except (ValueError, KeyError, sqlite3.IntegrityError) as exc: return redirect_with_message("/telegram-backup?view=policies", "error", str(exc))
    return redirect_with_message("/telegram-backup?view=policies", "success", "Política Telegram criada e integração habilitada.")


def handle_telegram_backup_settings(environ: dict) -> Response:
    user, response, form = _telegram_form(environ)
    if response: return response
    try:
        destination_id = int(form["destination_id"]); max_mib = int(form.get("max_mib", "50")); attempts = int(form.get("max_attempts", "5")); retry = int(form.get("retry_seconds", "60"))
        if not 1 <= max_mib <= 2048 or not 1 <= attempts <= 20 or not 1 <= retry <= 86400: raise ValueError("Parâmetros Telegram fora do intervalo permitido.")
        with connect() as conn:
            target = conn.execute("SELECT 1 FROM telegram_destinations WHERE id=? AND is_active=1 AND use_for_backup_files=1 AND deleted_at IS NULL", (destination_id,)).fetchone()
            if not target: raise ValueError("Destino padrão inválido.")
            set_setting(conn, "telegram_backup_enabled", "1" if form.get("enabled") == "1" else "0")
            set_setting(conn, "telegram_backup_default_destination", str(destination_id)); set_setting(conn, "telegram_backup_max_file_bytes", str(max_mib*1048576)); set_setting(conn, "telegram_backup_max_attempts", str(attempts)); set_setting(conn, "telegram_backup_retry_base_seconds", str(retry))
            audit(conn, user["id"], "telegram.backup_settings_updated", "settings", "telegram_backup", "{}", environ.get("REMOTE_ADDR", ""))
    except (ValueError, KeyError) as exc: return redirect_with_message("/telegram-backup?view=settings", "error", str(exc))
    return redirect_with_message("/telegram-backup?view=settings", "success", "Configuração de envio de backups salva.")


def handle_telegram_test_file(environ: dict, destination_id: int) -> Response:
    user, response, form = _telegram_form(environ)
    if response: return response
    try:
        with connect() as conn: telegram_queue_test_file(conn, destination_id, user["id"])
    except ValueError as exc: return redirect_with_message("/telegram-backup", "error", str(exc))
    return redirect_with_message("/telegram-backup", "info", "Arquivo fictício enfileirado; o worker fará o envio e removerá o temporário.")


def handle_telegram_topic_create(environ: dict) -> Response:
    user, response, form = _telegram_form(environ)
    if response: return response
    try:
        with connect() as conn:
            mapping_uuid = str(uuid.uuid4())
            conn.execute("""INSERT INTO telegram_topic_mappings(uuid,destination_id,mapping_type,equipment_id,group_id,match_value,thread_id)
                VALUES(?,?,?,?,?,?,?)""", (mapping_uuid, int(form["destination_id"]), form["mapping_type"], int(form["equipment_id"]) if form.get("equipment_id") else None,
                int(form["group_id"]) if form.get("group_id") else None, form.get("match_value", "")[:80], int(form["thread_id"])))
            audit(conn, user["id"], "telegram.topic_mapping_updated", "telegram_topic_mapping", mapping_uuid, "{}", environ.get("REMOTE_ADDR", ""))
    except (ValueError, KeyError, sqlite3.IntegrityError) as exc: return redirect_with_message("/telegram-backup?view=topics", "error", str(exc))
    return redirect_with_message("/telegram-backup?view=topics", "success", "Mapeamento de tópico salvo.")


def handle_telegram_item_action(environ: dict, item_id: int, action: str) -> Response:
    user, response, form = _telegram_form(environ, admin=False)
    if response: return response
    with connect() as conn:
        ok = telegram_retry_item(conn, item_id, user["id"]) if action == "retry" else telegram_cancel_item(conn, item_id, user["id"])
    return redirect_with_message("/telegram-backup?view=queue", "success" if ok else "info", "Item atualizado." if ok else "Ação não aplicável.")


def handle_telegram_backup_enqueue(environ: dict, backup_uuid: str) -> Response:
    user, response, form = _telegram_form(environ, admin=False)
    if response: return response
    with connect() as conn:
        backup = conn.execute("SELECT id FROM backups WHERE uuid=?", (backup_uuid,)).fetchone()
        if not backup: return render("Backup não encontrado", "<p>Backup não encontrado.</p>", user, HTTPStatus.NOT_FOUND)
        existing = conn.execute("SELECT id,status FROM telegram_backup_items WHERE backup_id=? ORDER BY id DESC LIMIT 1", (backup["id"],)).fetchone()
        if existing and existing["status"] in {"failed", "skipped"}: ok = telegram_retry_item(conn, existing["id"], user["id"])
        elif existing: ok = False
        else: ok = bool(telegram_enqueue_backup(conn, backup["id"], user_id=user["id"]))
    return redirect_with_message(f"/backups/{backup_uuid}", "success" if ok else "info", "Entrega Telegram enfileirada." if ok else "Já existe uma entrega ou a política não permite o envio.")


def cloud_page(environ: dict) -> Response:
    user, response = require_user(environ)
    if response: return response
    view = parse_qs(environ.get("QUERY_STRING", "")).get("view", ["targets"])[-1]
    with connect() as conn:
        ctx = cloud_page_context(conn)
        targets = ctx["targets"]
        policies = ctx["policies"]
        items = ctx["items"]
        equipment = ctx["equipment"]
        public_base_url = get_setting(conn, "public_base_url", "").strip().rstrip("/")
    if not public_base_url:
        host = environ.get("HTTP_HOST", "").strip().lower()
        if re.fullmatch(r"[a-z0-9.-]+", host) and "." in host:
            public_base_url = f"https://{host}"
    if view not in {"targets", "policies", "queue", "history"}:
        view = "targets"
    cloud_icon = lambda path: f'<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{path}</svg>'
    tab_data = (("targets", "Destinos", '<path d="M7 18h11a4 4 0 0 0 .5-8 7 7 0 0 0-13-1A4.5 4.5 0 0 0 7 18Z"/>'),
                ("policies", "Políticas", '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/>'),
                ("queue", "Fila", '<path d="M4 6h16M4 12h16M4 18h10"/>'),
                ("history", "Histórico", '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'))
    tabs = '<nav class="cloud-tabs" aria-label="Seções da sincronização">' + "".join(
        f'<a class="{"active" if view == key else ""}" href="/cloud?view={key}">{cloud_icon(path)}<span>{label}</span></a>'
        for key, label, path in tab_data) + '</nav>'
    active_targets = sum(1 for item in targets if item["is_active"])
    pending_items = sum(1 for item in items if item["status"] in {"queued", "processing", "retry_wait"})
    synced_items = sum(1 for item in items if item["status"] == "synced")
    stats = f'''<section class="cloud-stats"><article><i>{cloud_icon('<path d="M7 18h11a4 4 0 0 0 .5-8 7 7 0 0 0-13-1A4.5 4.5 0 0 0 7 18Z"/>')}</i><div><span>Destinos ativos</span><strong>{active_targets}</strong><small>{len(targets)} configurados</small></div></article><article><i>{cloud_icon('<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>')}</i><div><span>Na fila</span><strong>{pending_items}</strong><small>Aguardando processamento</small></div></article><article><i>{cloud_icon('<circle cx="12" cy="12" r="9"/><path d="m8 12 2.6 2.6L16.5 9"/>')}</i><div><span>Sincronizados</span><strong>{synced_items}</strong><small>Entregas recentes</small></div></article></section>'''
    if view == "targets":
        rows = "".join(f"<tr><td><div class='cloud-target-name'><i>{cloud_icon('<path d=\"M7 18h11a4 4 0 0 0 .5-8 7 7 0 0 0-13-1A4.5 4.5 0 0 0 7 18Z\"/>')}</i><div><strong>{e(r['name'])}</strong><small>{'rclone · '+r['rclone_remote'] if r['rclone_remote'] else 'Destino simulado' if r['provider']=='simulate' else 'Destino legado desativado'}</small></div></div></td><td><span class='badge status-{'success' if r['is_active'] else 'cancelled'}'>{'Ativo' if r['is_active'] else 'Inativo'}</span></td><td>{e(r['rclone_path'] or r['destination_label'] or 'Pasta gerenciada')}</td><td>{e(r['sync_window_start'] or 'Sempre')} – {e(r['sync_window_end'] or 'Sempre')}</td><td>{r['max_attempts']}</td><td><div class='cloud-row-actions'>{f'''<form method='post' action='/cloud/targets/{r['id']}/toggle'><button class='small' type='submit'>{'Desativar' if r['is_active'] else 'Ativar'}</button></form><form method='post' action='/cloud/targets/{r['id']}/test'><button class='small secondary' type='submit'>Testar</button></form><form method='post' action='/cloud/targets/{r['id']}/delete'><button class='small danger' type='submit'>Excluir</button></form>''' if user['can_admin'] else 'Visualização'}</div></td></tr>" for r in targets)
        form = "" if not user["can_admin"] else f'''<details class="panel cloud-create-card"><summary>{cloud_icon('<path d="M12 5v14M5 12h14"/>')}<span><strong>Novo destino simulado</strong><small>Crie um destino para testes sem envio externo.</small></span><b>Configurar</b></summary><form method="post" action="/cloud/targets"><label>Nome<input name="name" required maxlength="120"></label><label>Pasta lógica<input name="destination_label" maxlength="160"></label><label>Janela inicial<input name="sync_window_start" type="time"></label><label>Janela final<input name="sync_window_end" type="time"></label><label>Máximo de tentativas<input name="max_attempts" type="number" min="1" max="20" value="3"></label><label>Retry base (s)<input name="retry_base_seconds" type="number" min="1" max="86400" value="60"></label><button type="submit">Criar destino</button></form></details>'''
        provider_options = "".join(f'<option value="{e(key)}">{e(label)}</option>' for key,label in RCLONE_PROVIDERS.items() if key != "drive")
        try: configured_remotes = list_rclone_remotes() if user["can_admin"] else []
        except RcloneError: configured_remotes = []
        remote_options = "".join(f'<option value="{e(remote)}">{e(remote)}</option>' for remote in configured_remotes)
        destination_step = (f'''<form method="post" action="/cloud/rclone"><input type="hidden" name="csrf_token" value="{session_csrf(environ)}"><div class="cloud-step-title"><b>2</b><span><strong>Criar o destino</strong><small>Escolha o remote conectado, teste o acesso e defina a pasta dos backups.</small></span></div><label>Nome exibido<input name="name" required maxlength="120" value="Backup externo"></label><label>Remote conectado<select name="remote_name" required>{remote_options}</select></label><label>Pasta para os backups<input name="base_path" maxlength="240" value="BackupManager"></label><label>Limite de velocidade (KB/s)<input name="bandwidth_limit_kbps" type="number" min="1" placeholder="Sem limite"></label><button type="submit">Testar conexão e criar destino</button></form>''' if configured_remotes else '''<div class="cloud-step-disabled"><b>2</b><span><strong>Criar o destino</strong><small>Conclua primeiro a conexão do provedor. Depois esta etapa exibirá o remote automaticamente.</small></span></div>''')
        rclone_form = "" if not user["can_admin"] else f'''<section class="panel cloud-rclone-setup"><header>{cloud_icon('<path d="M7 18h11a4 4 0 0 0 .5-8 7 7 0 0 0-13-1A4.5 4.5 0 0 0 7 18Z"/>')}<div><span class="eyebrow">Outros provedores</span><h2>Assistente rclone</h2><p>Use para S3, B2, SFTP, OneDrive ou Dropbox.</p></div></header><form method="post" action="/cloud/rclone/setup"><input type="hidden" name="csrf_token" value="{session_csrf(environ)}"><div class="cloud-step-title"><b>1</b><span><strong>Conectar o provedor</strong><small>Escolha onde os backups serão guardados e dê um nome simples à conexão.</small></span></div><label>Nome da conexão<input name="remote_name" required maxlength="63" placeholder="Ex.: meudrive"></label><label>Provedor<select name="provider" required>{provider_options}</select></label><button type="submit">Conectar provedor</button></form>{destination_step}</section>'''
        google_form = "" if not user["can_admin"] else f'''<section class="panel cloud-rclone-setup cloud-google-connect"><header>{cloud_icon('<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>')}<div><span class="eyebrow">Configuração simples</span><h2>Conectar com Google</h2><p>Login direto pelo navegador, sem PowerShell, comandos ou transporte de JSON.</p></div></header><form method="post" action="/cloud/rclone/google/connect"><input type="hidden" name="csrf_token" value="{session_csrf(environ)}"><div class="cloud-step-title"><b>1</b><span><strong>Aplicativo OAuth da Web</strong><small>No Google Cloud, use o tipo Aplicativo da Web e cadastre exatamente a URL de retorno abaixo.</small></span></div><label class="wide">URL pública HTTPS<input name="public_base_url" type="url" required value="{e(public_base_url)}" placeholder="https://backup.exemplo.com.br"><small>URL de retorno: <code>{e((public_base_url or 'https://backup.exemplo.com.br') + GOOGLE_RCLONE_CALLBACK_PATH)}</code></small></label><label>Nome da conexão<input name="remote_name" required maxlength="63" value="meudrive"></label><label>Nome exibido<input name="name" required maxlength="120" value="Backup externo"></label><label>ID do cliente<input name="client_id" required autocomplete="off"></label><label>Chave secreta<input name="client_secret" type="password" required autocomplete="new-password"></label><label>Pasta dos backups<input name="base_path" maxlength="240" value="BackupManager"></label><label>Limite de velocidade (KB/s)<input name="bandwidth_limit_kbps" type="number" min="1" placeholder="Sem limite"></label><button type="submit">Entrar com Google</button></form></section>'''
        body = f"<section class='cloud-create-grid'>{google_form}{rclone_form}{form}</section><section class='panel table-panel cloud-table-panel'><header><div><span class='eyebrow'>Armazenamento externo via rclone</span><h2>Destinos configurados</h2><p>{len(targets)} {'destino cadastrado' if len(targets)==1 else 'destinos cadastrados'}.</p></div></header><table><thead><tr><th>Destino</th><th>Status</th><th>Pasta</th><th>Janela</th><th>Tentativas</th><th>Ações</th></tr></thead><tbody>{rows or '<tr><td colspan=6 class=cloud-empty>Nenhum destino configurado.</td></tr>'}</tbody></table></section>"
    elif view == "policies":
        rows = "".join(f"<tr><td><div class='cloud-policy-target'><i>{cloud_icon('<path d=\"M7 18h11a4 4 0 0 0 .5-8 7 7 0 0 0-13-1A4.5 4.5 0 0 0 7 18Z\"/>')}</i><strong>{e(r['target_name'])}</strong></div></td><td><span class='cloud-scope-badge'>{'Global' if r['scope_type']=='global' else 'Equipamento'}</span></td><td>{e(r['hostname'] or 'Todos os equipamentos')}</td><td><span class='cloud-method-state {'enabled' if r['include_ssh'] else ''}'>{'✓' if r['include_ssh'] else '—'} SSH</span></td><td><span class='cloud-method-state {'enabled' if r['include_ftp'] else ''}'>{'✓' if r['include_ftp'] else '—'} FTP</span></td><td><span class='badge status-{'success' if r['enabled'] else 'cancelled'}'>{'Ativa' if r['enabled'] else 'Inativa'}</span></td></tr>" for r in policies)
        options = "".join(f"<option value='{r['id']}'>{e(r['name'])}</option>" for r in targets)
        eq_options = "<option value=''>Global</option>" + "".join(f"<option value='{r['id']}'>{e(r['hostname'])}</option>" for r in equipment)
        form = "" if not user["can_admin"] else f'''<details class="panel cloud-policy-create"><summary>{cloud_icon('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="M12 8v8M8 12h8"/>')}<span><strong>Nova política de sincronização</strong><small>Defina quais backups serão enviados e para qual destino.</small></span><b>Criar política</b></summary><form method="post" action="/cloud/policies"><div class="cloud-policy-fields"><label>Destino externo<select name="target_id" required><option value="">Selecione um destino</option>{options}</select><small>Conta ou armazenamento que receberá a cópia.</small></label><label>Aplicar a<select name="equipment_id">{eq_options}</select><small>Use Global para aplicar a todos os equipamentos.</small></label></div><fieldset><legend>Métodos incluídos</legend><label class="cloud-check-card"><input type="checkbox" name="include_ssh" value="1" checked><span>{cloud_icon('<path d="M4 5h16v14H4zM8 9h8M8 13h5"/>')}<b>Backups SSH</b><small>Incluir arquivos coletados por SSH.</small></span></label><label class="cloud-check-card"><input type="checkbox" name="include_ftp" value="1" checked><span>{cloud_icon('<path d="M12 16V4M7 9l5-5 5 5M5 20h14"/>')}<b>Backups FTP</b><small>Incluir arquivos recebidos por FTP.</small></span></label><label class="cloud-check-card"><input type="checkbox" name="sync_new_backups" value="1"><span>{cloud_icon('<path d="M4 12a8 8 0 0 1 14-5l2 2M20 4v5h-5"/>')}<b>Envio automático</b><small>Sincronizar novos backups assim que chegarem.</small></span></label></fieldset><div class="cloud-policy-actions"><button type="submit">Salvar política</button></div></form></details>'''
        body = f"{form}<section class='panel table-panel cloud-table-panel cloud-policy-table'><header><div><span class='eyebrow'>Regras de envio</span><h2>Políticas configuradas</h2><p>{len(policies)} {'política cadastrada' if len(policies)==1 else 'políticas cadastradas'}.</p></div><span class='cloud-policy-active'>{sum(1 for p in policies if p['enabled'])} ativas</span></header><table><thead><tr><th>Destino</th><th>Escopo</th><th>Equipamento</th><th>SSH</th><th>FTP</th><th>Status</th></tr></thead><tbody>{rows or '<tr><td colspan=6 class=cloud-empty>Nenhuma política configurada.</td></tr>'}</tbody></table></section>"
    else:
        queue_statuses = {"queued", "processing", "retry_wait"}
        history_statuses = {"synced", "failed", "skipped", "cancelled"}
        visible = [r for r in items if r["status"] in (queue_statuses if view == "queue" else history_statuses)]
        status_labels = {"queued":"Na fila", "processing":"Processando", "retry_wait":"Nova tentativa", "synced":"Sincronizado", "failed":"Falhou", "skipped":"Ignorado", "cancelled":"Cancelado"}
        def item_actions(row) -> str:
            if not user["can_admin"]: return "Sem ações"
            if row["status"] == "failed":
                if row["target_deleted_at"] or not row["target_is_active"]:
                    return "Destino antigo"
                return f"<form method='post' action='/cloud/items/{row['id']}/retry'><button class='small' type='submit'>Tentar novamente</button></form>"
            if row["status"] in queue_statuses:
                return f"<form method='post' action='/cloud/items/{row['id']}/cancel'><button class='small secondary' type='submit'>Cancelar</button></form>"
            return "Sem ações"
        rows = "".join(f"<tr><td><div class='cloud-item-date'><strong>{e(local_dt(r['queued_at']))}</strong><small>{'Próxima: '+e(local_dt(r['next_attempt_at'])) if r['next_attempt_at'] else 'Processamento concluído'}</small></div></td><td><div class='cloud-item-equipment'><strong>{e(r['hostname'])}</strong></div></td><td><a class='cloud-item-backup' href='/backups/{e(r['backup_uuid'])}' title='{e(r['backup_uuid'])}'>{e(r['backup_uuid'])[:8]}…</a></td><td><strong>{e(r['target_name'])}</strong><small class='cloud-item-size'>{format_size(r['bytes_total'])}</small></td><td><span class='badge status-{'success' if r['status']=='synced' else 'warning' if r['status'] in queue_statuses else 'error' if r['status']=='failed' else 'cancelled'}'>{e(status_labels.get(r['status'],r['status']))}</span></td><td><span class='cloud-item-attempt'>{r['attempt']} de {r['max_attempts']}</span></td><td><span class='cloud-item-error'>{e(r['error_code'] or 'Nenhum erro')}</span></td><td><div class='cloud-row-actions'>{item_actions(r)}</div></td></tr>" for r in visible)
        empty = "Nenhum item aguardando processamento." if view == "queue" else "Nenhuma sincronização finalizada."
        body = f"<section class='panel table-panel cloud-table-panel cloud-items-panel'><header><div><span class='eyebrow'>{'Processamento' if view == 'queue' else 'Resultados'}</span><h2>{'Fila de sincronização' if view == 'queue' else 'Histórico de sincronizações'}</h2><p>{len(visible)} {'item' if len(visible)==1 else 'itens'} nesta visualização.</p></div></header><table><thead><tr><th>Data</th><th>Equipamento</th><th>Backup</th><th>Destino</th><th>Status</th><th>Tentativas</th><th>Resultado</th><th>Ações</th></tr></thead><tbody>{rows or f'<tr><td colspan=\"8\" class=\"cloud-empty\">{empty}</td></tr>'}</tbody></table></section>"
    return render("Sincronização externa", f"<section class='cloud-modern-page'><header class='page-head cloud-page-head'><div><span class='eyebrow'>Proteção fora do servidor</span><h1>Sincronização externa</h1><p>Mantenha cópias dos backups em destinos externos sem alterar a fonte local.</p></div><span class='cloud-page-state'><i></i>{active_targets} {'destino ativo' if active_targets == 1 else 'destinos ativos'}</span></header>{stats}{tabs}{body}</section>", user)


def handle_cloud_target_create(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ)
    try:
        name = form.get("name", "").strip()
        if not name: raise ValueError("Nome obrigatório.")
        start, end = form.get("sync_window_start") or None, form.get("sync_window_end") or None
        if bool(start) != bool(end): raise ValueError("Informe início e fim da janela.")
        with connect() as conn:
            target_uuid = str(uuid.uuid4())
            conn.execute("""INSERT INTO cloud_targets(uuid,name,provider,mode,destination_label,sync_window_start,sync_window_end,max_attempts,retry_base_seconds,created_by_user_id)
                VALUES(?,?,'simulate','simulate',?,?,?,?,?,?)""", (target_uuid,name,form.get("destination_label","")[:160],start,end,int(form.get("max_attempts",3)),int(form.get("retry_base_seconds",60)),user["id"]))
            audit(conn,user["id"],"cloud.target_created","cloud_target",target_uuid,json.dumps({"provider":"simulate","mode":"simulate"}),environ.get("REMOTE_ADDR",""))
    except (ValueError, TypeError) as exc: return redirect_with_message("/cloud", "error", str(exc))
    return redirect_with_message("/cloud", "success", "Destino simulado criado.")


def handle_rclone_target_create(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ)
    if not valid_session_csrf(environ, form.get("csrf_token", "")):
        return render("Requisição recusada", "<p>Token CSRF inválido.</p>", user, HTTPStatus.FORBIDDEN)
    try:
        name = form.get("name", "").strip()
        if not name: raise ValueError("Nome obrigatório.")
        remote = normalize_remote(form.get("remote_name", ""))
        base_path = normalize_rclone_path(form.get("base_path", "BackupManager"))
        limit = int(form["bandwidth_limit_kbps"]) if form.get("bandwidth_limit_kbps") else None
        if limit is not None and limit < 1: raise ValueError("Limite de banda inválido.")
        test_rclone_remote(remote, base_path)
        with connect() as conn:
            target_uuid = str(uuid.uuid4())
            cursor = conn.execute("""INSERT INTO cloud_targets(uuid,name,provider,mode,destination_label,bandwidth_limit_kbps,created_by_user_id)
                VALUES(?,?,'simulate','simulate',?,?,?)""", (target_uuid, name[:120], f"{remote}:{base_path}", limit, user["id"]))
            conn.execute("""INSERT INTO rclone_connections(target_id,remote_name,base_path,status,last_test_at)
                VALUES(?,?,?,'verified',CURRENT_TIMESTAMP)""", (cursor.lastrowid, remote, base_path))
            audit(conn,user["id"],"cloud.rclone_connected","cloud_target",target_uuid,
                  json.dumps({"remote":remote,"path":base_path},separators=(",",":")),environ.get("REMOTE_ADDR",""))
        return redirect_with_message("/cloud", "success", "Destino rclone testado e adicionado.")
    except (ValueError, TypeError, RcloneError) as exc:
        message = exc.safe_message if isinstance(exc, RcloneError) else str(exc)
        return redirect_with_message("/cloud", "error", message)


def _rclone_setup_page(environ: dict, remote: str, payload: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    state = payload.get("State", "")
    if not state:
        return redirect_with_message("/cloud", "success", f"Remote {remote} configurado. Agora adicione o destino e escolha a pasta.")
    option = payload.get("Option") or {}
    protocol_error = str(payload.get("Error") or "").strip()
    if protocol_error or not option:
        message = protocol_error or "O rclone não informou a próxima etapa. Reinicie a configuração do remote."
        content = f'''<section class="cloud-modern-page rclone-wizard"><header class="page-head"><div><span class="eyebrow">Assistente rclone</span><h1>Não foi possível continuar</h1><p>O remote {e(remote)} não foi concluído.</p></div></header><section class="panel"><div class="rclone-wizard-step"><span>Erro de configuração</span><strong>Resposta recusada pelo rclone</strong><pre>{e(message)}</pre></div><form method="post" action="/cloud/rclone/setup/cancel"><input type="hidden" name="csrf_token" value="{session_csrf(environ)}"><input type="hidden" name="remote_name" value="{e(remote)}"><div class="rclone-wizard-actions"><button type="submit">Cancelar e limpar</button></div></form></section></section>'''
        return render("Falha no assistente rclone", content, user, HTTPStatus.BAD_REQUEST)
    name = str(option.get("Name", "opção"))
    labels = {
        "client_id": "ID do cliente Google",
        "client_secret": "Chave secreta do cliente Google",
        "config_token": "Autorizar acesso ao Google Drive",
    }
    help_texts = {
        "client_id": "Abra o JSON do aplicativo para computador e cole somente o valor client_id.",
        "client_secret": "No mesmo JSON, cole somente o valor client_secret. Ele será armazenado no arquivo protegido do rclone.",
    }
    help_text = help_texts.get(name, str(option.get("Help", "Informe o valor solicitado pelo rclone.")))
    if name == "config_token":
        command = re.search(r'(?m)^\s*(rclone authorize .+?)\s*$', help_text)
        help_text = ("Execute o comando abaixo em um computador que tenha rclone e navegador. "
                     "Autorize sua conta Google e cole aqui o JSON retornado. Não compartilhe o comando nem o resultado."
                     + (f"\n\n{command.group(1)}" if command else ""))
    examples = option.get("Examples") if isinstance(option.get("Examples"), list) else []
    default = option.get("DefaultStr", option.get("Default", ""))
    if examples:
        choices = "".join(f'<option value="{e(item.get("Value", ""))}">{e(item.get("Help") or item.get("Value", ""))}</option>' for item in examples)
        field = f'<select name="result">{choices}</select>'
    else:
        input_type = "password" if option.get("IsPassword") else "text"
        field = f'<input type="{input_type}" name="result" value="{e(default)}" maxlength="8192" {'required' if option.get('Required') else ''} autocomplete="off">'
    content = f'''<section class="cloud-modern-page rclone-wizard"><header class="page-head"><div><span class="eyebrow">Assistente rclone</span><h1>Configurar {e(remote)}</h1><p>Responda apenas ao que for necessário. As opções técnicas seguras são preenchidas automaticamente.</p></div></header><section class="panel"><div class="rclone-wizard-step"><span>Etapa de configuração</span><strong>{e(labels.get(name, name.replace('_',' ').title()))}</strong><pre>{e(help_text)}</pre></div><form method="post" action="/cloud/rclone/setup/continue"><input type="hidden" name="csrf_token" value="{session_csrf(environ)}"><input type="hidden" name="remote_name" value="{e(remote)}"><input type="hidden" name="state" value="{e(state)}"><label>Informação solicitada{field}</label><div class="rclone-wizard-actions"><button class="secondary" type="submit" formaction="/cloud/rclone/setup/cancel" formnovalidate>Cancelar</button><button type="submit">Continuar</button></div></form></section></section>'''
    return render("Assistente rclone", content, user)


def handle_rclone_setup(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ)
    if not valid_session_csrf(environ, form.get("csrf_token", "")):
        return render("Requisição recusada", "<p>Token CSRF inválido.</p>", user, HTTPStatus.FORBIDDEN)
    try:
        remote = normalize_remote(form.get("remote_name", ""))
        payload = begin_rclone_remote(remote, form.get("provider", ""))
        return _rclone_setup_page(environ, remote, payload)
    except (ValueError, RcloneError) as exc:
        return redirect_with_message("/cloud", "error", exc.safe_message if isinstance(exc,RcloneError) else str(exc))


def handle_rclone_setup_continue(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ)
    if not valid_session_csrf(environ, form.get("csrf_token", "")):
        return render("Requisição recusada", "<p>Token CSRF inválido.</p>", user, HTTPStatus.FORBIDDEN)
    try:
        remote = normalize_remote(form.get("remote_name", ""))
        payload = continue_rclone_remote(remote, form.get("state", ""), form.get("result", ""))
        return _rclone_setup_page(environ, remote, payload)
    except (ValueError, RcloneError) as exc:
        return redirect_with_message("/cloud", "error", exc.safe_message if isinstance(exc,RcloneError) else str(exc))


def handle_rclone_setup_cancel(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ)
    if not valid_session_csrf(environ, form.get("csrf_token", "")):
        return render("Requisição recusada", "<p>Token CSRF inválido.</p>", user, HTTPStatus.FORBIDDEN)
    try:
        remote = normalize_remote(form.get("remote_name", ""))
        removed = cancel_rclone_remote(remote)
    except (ValueError, RcloneError) as exc:
        return redirect_with_message("/cloud", "error", exc.safe_message if isinstance(exc, RcloneError) else str(exc))
    if not removed:
        return redirect_with_message("/cloud", "info", "O assistente já estava encerrado; nenhuma conexão foi removida.")
    return redirect_with_message("/cloud", "success", "Configuração cancelada e dados parciais removidos.")


def _google_public_base_url(value: str) -> str:
    parsed = urlsplit((value or "").strip())
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or
            parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("Informe a URL pública HTTPS do Backup Manager, sem caminho adicional.")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise ValueError("O Google exige um domínio HTTPS; endereço IP não é aceito.")
    if parsed.port not in (None, 443):
        raise ValueError("A URL pública deve usar a porta HTTPS padrão.")
    return (value or "").strip().rstrip("/")


def handle_google_rclone_connect(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ)
    if not valid_session_csrf(environ, form.get("csrf_token", "")):
        return render("Requisição recusada", "<p>Token CSRF inválido.</p>", user, HTTPStatus.FORBIDDEN)
    try:
        remote = normalize_remote(form.get("remote_name", ""))
        name = form.get("name", "").strip()
        if not name: raise ValueError("Informe o nome exibido do destino.")
        base_path = normalize_rclone_path(form.get("base_path", "BackupManager"))
        public_base = _google_public_base_url(form.get("public_base_url", ""))
        limit = int(form["bandwidth_limit_kbps"]) if form.get("bandwidth_limit_kbps") else None
        if limit is not None and limit < 1: raise ValueError("Limite de banda inválido.")
        with connect() as conn:
            existing = conn.execute("""SELECT t.id,t.uuid FROM cloud_targets t
                JOIN rclone_connections r ON r.target_id=t.id
                WHERE r.remote_name=? AND t.deleted_at IS NULL ORDER BY t.id DESC LIMIT 1""", (remote,)).fetchone()
            if remote in list_rclone_remotes() and not existing:
                raise ValueError("Já existe um remote com esse nome.")
            if existing:
                target_id, target_uuid = existing["id"], existing["uuid"]
                conn.execute("""UPDATE cloud_targets SET name=?,destination_label=?,bandwidth_limit_kbps=?,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?""", (name[:120], f"{remote}:{base_path}", limit, target_id))
                conn.execute("""UPDATE rclone_connections SET base_path=?,status='failed',
                    last_error_code='GOOGLE_OAUTH_PENDING',updated_at=CURRENT_TIMESTAMP WHERE target_id=?""", (base_path, target_id))
            else:
                target_uuid = str(uuid.uuid4())
                cursor = conn.execute("""INSERT INTO cloud_targets(uuid,name,provider,mode,is_active,destination_label,
                    bandwidth_limit_kbps,created_by_user_id) VALUES(?,?,'simulate','simulate',0,?,?,?)""",
                    (target_uuid, name[:120], f"{remote}:{base_path}", limit, user["id"]))
                target_id = cursor.lastrowid
                conn.execute("""INSERT INTO rclone_connections(target_id,remote_name,base_path,status,last_error_code)
                    VALUES(?,?,?,'failed','GOOGLE_OAUTH_PENDING')""", (target_id, remote, base_path))
            set_setting(conn, "public_base_url", public_base)
            auth_url = begin_google_rclone_oauth(
                conn, target_id, form.get("client_id", ""), form.get("client_secret", ""),
                public_base + GOOGLE_RCLONE_CALLBACK_PATH, user_id=user["id"],
                session_token=cookie_token(environ) or "")
            audit(conn, user["id"], "cloud.google_oauth_started", "cloud_target", target_uuid,
                  json.dumps({"remote": remote, "scope": "drive.file"}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
        return redirect(auth_url)
    except (ValueError, TypeError, RcloneError, GoogleOAuthError) as exc:
        message_text = exc.safe_message if isinstance(exc, (RcloneError, GoogleOAuthError)) else str(exc)
        return redirect_with_message("/cloud", "error", message_text)


def handle_google_rclone_callback(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    query = parse_qs(environ.get("QUERY_STRING", ""))
    code, state = query.get("code", [""])[-1], query.get("state", [""])[-1]
    try:
        if query.get("error") or not code or not state:
            raise GoogleOAuthError("GOOGLE_OAUTH_DENIED", "A autorização foi cancelada ou retornou incompleta.")
        with connect() as conn:
            public_base = _google_public_base_url(get_setting(conn, "public_base_url", ""))
            target_id = google_rclone_target_for_state(conn, state)
            result = complete_google_rclone_oauth(
                conn, target_id, code, state, public_base + GOOGLE_RCLONE_CALLBACK_PATH,
                user_id=user["id"], session_token=cookie_token(environ) or "")
            connection = conn.execute("SELECT remote_name,base_path FROM rclone_connections WHERE target_id=?", (target_id,)).fetchone()
            if not connection: raise GoogleOAuthError("GOOGLE_TARGET_MISSING", "O destino vinculado à autorização não foi encontrado.")
            # This remote belongs to the target selected by the signed OAuth
            # state. Replacing it makes a failed/expired authorization
            # recoverable without touching unrelated rclone remotes.
            create_rclone_drive_remote(connection["remote_name"], result.client_id,
                                       result.client_secret, result.token, replace=True)
            test_rclone_remote(connection["remote_name"], connection["base_path"])
            conn.execute("""UPDATE rclone_connections SET status='verified',last_test_at=CURRENT_TIMESTAMP,
                last_error_code=NULL,updated_at=CURRENT_TIMESTAMP WHERE target_id=?""", (target_id,))
            conn.execute("UPDATE cloud_targets SET is_active=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (target_id,))
            enable_cloud_automatic_backup(conn, target_id)
            target = conn.execute("SELECT uuid FROM cloud_targets WHERE id=?", (target_id,)).fetchone()
            audit(conn, user["id"], "cloud.google_connected", "cloud_target", target["uuid"],
                  json.dumps({"remote": connection["remote_name"], "scope": "drive.file",
                              "automatic_backup": True}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
        return redirect_with_message("/cloud", "success", "Google Drive conectado; novos backups SSH e FTP serão enviados automaticamente.")
    except (ValueError, RcloneError, GoogleOAuthError) as exc:
        message_text = exc.safe_message if isinstance(exc, (RcloneError, GoogleOAuthError)) else str(exc)
        return redirect_with_message("/cloud", "error", message_text)


def handle_cloud_policy_create(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    form = parse_form(environ)
    try:
        target_id = int(form.get("target_id",0)); equipment_id = int(form["equipment_id"]) if form.get("equipment_id") else None
        with connect() as conn:
            target = conn.execute("SELECT uuid FROM cloud_targets WHERE id=? AND deleted_at IS NULL",(target_id,)).fetchone()
            if not target: raise ValueError("Destino inválido.")
            policy_uuid = str(uuid.uuid4())
            conn.execute("""INSERT INTO cloud_sync_policies(uuid,target_id,scope_type,equipment_id,include_ssh,include_ftp,sync_new_backups)
                VALUES(?,?,?,?,?,?,?)""",(policy_uuid,target_id,"equipment" if equipment_id else "global",equipment_id,int("include_ssh" in form),int("include_ftp" in form),int("sync_new_backups" in form)))
            audit(conn,user["id"],"cloud.policy_created","cloud_policy",policy_uuid,json.dumps({"target_uuid":target["uuid"],"scope":"equipment" if equipment_id else "global"}),environ.get("REMOTE_ADDR",""))
    except (ValueError, TypeError) as exc: return redirect_with_message("/cloud?view=policies", "error", str(exc))
    return redirect_with_message("/cloud?view=policies", "success", "Política criada.")


def handle_cloud_backup_enqueue(environ: dict, backup_uuid: str) -> Response:
    user, response = require_admin(environ)
    if response: return response
    with connect() as conn:
        backup = conn.execute("SELECT id FROM backups WHERE uuid=?",(backup_uuid,)).fetchone()
        created = cloud_enqueue_backup(conn, backup["id"], user_id=user["id"]) if backup else []
    message = "Backup adicionado à fila de sincronização externa." if created else "Nenhum destino elegível ou item já existente."
    return redirect_with_message(f"/backups/{backup_uuid}", "success" if created else "info", message)


def handle_cloud_target_action(environ: dict, target_id: int, action: str) -> Response:
    user, response = require_admin(environ)
    if response: return response
    with connect() as conn:
        target=conn.execute("SELECT * FROM cloud_targets WHERE id=? AND deleted_at IS NULL",(target_id,)).fetchone()
        if not target: return Response("Not found",HTTPStatus.NOT_FOUND)
        if action == "test":
            connection=conn.execute("SELECT remote_name,base_path FROM rclone_connections WHERE target_id=?",(target_id,)).fetchone()
            if not connection: return redirect_with_message("/cloud","info","Destino legado não possui configuração rclone.")
            try:
                test_rclone_remote(connection["remote_name"],connection["base_path"])
            except RcloneError as exc:
                conn.execute("UPDATE rclone_connections SET status='failed',last_test_at=CURRENT_TIMESTAMP,last_error_code=?,updated_at=CURRENT_TIMESTAMP WHERE target_id=?",(exc.code,target_id))
                audit(conn,user["id"],"cloud.rclone_test_failed","cloud_target",target["uuid"],json.dumps({"code":exc.code}),environ.get("REMOTE_ADDR",""))
                return redirect_with_message("/cloud","error",exc.safe_message)
            conn.execute("UPDATE rclone_connections SET status='verified',last_test_at=CURRENT_TIMESTAMP,last_error_code=NULL,updated_at=CURRENT_TIMESTAMP WHERE target_id=?",(target_id,))
            audit(conn,user["id"],"cloud.rclone_test_success","cloud_target",target["uuid"],"{}",environ.get("REMOTE_ADDR",""))
            return redirect_with_message("/cloud","success","Destino rclone acessível.")
        if action == "toggle":
            enabled=0 if target["is_active"] else 1; conn.execute("UPDATE cloud_targets SET is_active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(enabled,target_id)); event="cloud.target_enabled" if enabled else "cloud.target_disabled"
        elif action == "delete":
            conn.execute("UPDATE cloud_targets SET is_active=0,deleted_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",(target_id,)); event="cloud.target_updated"
        else:
            event="cloud.target_updated"
        audit(conn,user["id"],event,"cloud_target",target["uuid"],json.dumps({"action":action}),environ.get("REMOTE_ADDR",""))
    return redirect_with_message("/cloud","success","Destino atualizado.")


def updates_page(environ: dict) -> Response:
    user, response = require_user(environ)
    if response: return response
    with connect() as conn:
        ctx = updates_page_context(conn)
        history = ctx["history"]
        settings = ctx["settings"]
        downloads = ctx["downloads"]
    update_icon = lambda path: f'<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{path}</svg>'
    status_labels = {"success":"Concluída", "completed":"Concluída", "failed":"Falhou", "queued":"Na fila", "downloading":"Baixando", "ready":"Pronta", "rejected":"Recusada", "cancelled":"Cancelada"}
    status_class = lambda value: "success" if value in {"success","completed","ready"} else "failed" if value in {"failed","rejected"} else "running" if value in {"queued","downloading"} else "cancelled"
    rows = "".join(f"<tr><td>{e(local_dt(r['requested_at']))}</td><td><div class='updates-version-cell'><strong>{e(r['from_version'])}</strong><span>→</span><strong>{e(r['to_version'] or '-')}</strong></div></td><td><span class='badge status-{status_class(r['status'])}'>{e(status_labels.get(r['status'],r['status']))}</span></td><td>{e(r['error_code'] or '-')}</td></tr>" for r in history)
    upload = ""
    if user["can_admin"]:
        csrf=session_csrf(environ)
        selected_rc=" selected" if settings.get("update_channel")=="rc" else ""; selected_stable="" if selected_rc else " selected"
        upload = f'''<section class="updates-management-grid"><article class="panel updates-repository-card"><header><span>{update_icon('<path d="M4 5h16v14H4zM8 9h8M8 13h5"/>')}</span><div><span class="eyebrow">Origem oficial</span><h2>Repositório remoto</h2><p>Configure o canal usado para consultar novos pacotes.</p></div><i class="updates-state-dot {'active' if settings.get('update_repository_enabled')=='1' else ''}"></i></header><form method="post" action="/settings/updates/repository"><input type="hidden" name="csrf_token" value="{csrf}"><label class="updates-enabled"><input type="checkbox" name="enabled" value="1" {'checked' if settings.get('update_repository_enabled')=='1' else ''}><span><strong>Consulta automática</strong><small>Permitir verificações periódicas.</small></span></label><label class="wide">URL HTTPS<input name="url" type="url" value="{e(settings.get('update_repository_url',''))}" placeholder="https://updates.example/backup-manager/updates.json"></label><label>Canal<select name="channel"><option value="stable"{selected_stable}>Stable</option><option value="rc"{selected_rc}>RC</option></select></label><label>Intervalo (horas)<input name="interval" type="number" min="1" max="168" value="{e(settings.get('update_check_interval_hours','24'))}"></label><div class="updates-form-actions"><button type="submit">Salvar configuração</button></div></form><div class="updates-quick-actions"><form method="post" action="/settings/updates/check"><input type="hidden" name="csrf_token" value="{csrf}"><button type="submit" class="secondary">{update_icon('<path d="M4 12a8 8 0 0 1 14-5l2 2M20 4v5h-5"/>')}Verificar agora</button></form><form method="post" action="/settings/updates/download"><input type="hidden" name="csrf_token" value="{csrf}"><button type="submit">{update_icon('<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>')}Baixar atualização</button></form></div></article><article class="panel updates-upload-card"><header><span>{update_icon('<path d="M12 21V9M7 14l5-5 5 5M5 3h14"/>')}</span><div><span class="eyebrow">Instalação manual</span><h2>Carregar pacote local</h2><p>Valide um pacote assinado no formato BMU.</p></div></header><form method="post" action="/settings/updates/upload" enctype="multipart/form-data"><input type="hidden" name="csrf_token" value="{csrf}"><label><span class="updates-dropzone">{update_icon('<path d="M12 16V4M7 9l5-5 5 5M5 20h14"/>')}<strong>Selecionar pacote .bmu</strong><small>O pacote será validado antes de qualquer instalação.</small></span><input type="file" name="package" accept=".bmu" required></label><button type="submit">Validar pacote</button></form><p>{update_icon('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/>')} Downloads apenas entram na fila; a aplicação da atualização é sempre manual.</p></article></section>'''
    download_percent = lambda row: min(100, round(100 * row["bytes_downloaded"] / row["bytes_total"])) if row["bytes_total"] else 0
    download_rows="".join(f"<tr><td>{e(local_dt(r['created_at']))}</td><td><strong>{e(r['version'])}</strong></td><td><span class='badge status-{status_class(r['status'])}'>{e(status_labels.get(r['status'],r['status']))}</span></td><td><div class='updates-progress'><span><i style='width:{download_percent(r)}%'></i></span><small>{format_size(r['bytes_downloaded'])} / {format_size(r['bytes_total'])}</small></div></td><td>{e(r['error_code'] or '-')}</td></tr>" for r in downloads)
    release=ctx["release"]
    available=f"{e(release.get('version','-'))} — {e(release.get('published_at','-'))} — {format_size(int(release.get('package_size',0)))}"
    notes=e(str(release.get('release_notes',''))[:4000]); security="Sim" if release.get("security_update") else "Não"
    content = f'''<section class="updates-modern-page"><header class="page-head updates-page-head"><div><span class="eyebrow">Manutenção segura</span><h1>Atualizações</h1><p>Consulte, valide e prepare novos pacotes com aplicação sempre manual.</p></div><span class="updates-safe-state">{update_icon('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>')}Instalação protegida</span></header><section class="updates-stats"><article><i>{update_icon('<path d="m8 9-4 3 4 3M16 9l4 3-4 3M14 5l-4 14"/>')}</i><div><span>Versão instalada</span><strong>{e(__version__)}</strong><small>Canal {e(settings.get('update_channel',__channel__)).upper()}</small></div></article><article><i>{update_icon('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>')}</i><div><span>Última verificação</span><strong>{e(status_labels.get(settings.get('update_last_check_status','never'),settings.get('update_last_check_status','Nunca')))}</strong><small>{e(local_dt(settings.get('update_last_check_at','')) or 'Ainda não realizada')}</small></div></article><article><i>{update_icon('<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>')}</i><div><span>Versão disponível</span><strong>{e(release.get('version','Nenhuma'))}</strong><small>Segurança: {security}</small></div></article></section><section class="panel updates-release-card"><header><span>{update_icon('<path d="M4 5h16v14H4zM8 9h8M8 13h5"/>')}</span><div><span class="eyebrow">Versão disponível</span><h2>Notas da versão</h2><p>{available}</p></div></header><p>{notes or 'Nenhuma atualização elegível no canal configurado.'}</p></section>{upload}<section class="updates-tables"><section class="panel table-panel updates-table-panel"><header><div><h2>Downloads</h2><p>Pacotes consultados ou baixados pelo worker.</p></div><span>{len(downloads)} registros</span></header><table><thead><tr><th>Criado</th><th>Versão</th><th>Estado</th><th>Progresso</th><th>Código</th></tr></thead><tbody>{download_rows or '<tr><td colspan="5" class="updates-empty">Nenhum download.</td></tr>'}</tbody></table></section><section class="panel table-panel updates-table-panel"><header><div><h2>Histórico</h2><p>Operações de atualização realizadas no servidor.</p></div><span>{len(history)} registros</span></header><table><thead><tr><th>Solicitada</th><th>Versão</th><th>Estado</th><th>Código</th></tr></thead><tbody>{rows or '<tr><td colspan="4" class="updates-empty">Nenhuma atualização registrada.</td></tr>'}</tbody></table></section></section></section>'''
    return render("Atualizações", content, user)


def _update_form(environ: dict):
    user,response=require_admin(environ)
    if response: return user,response,None
    form=parse_form(environ)
    if not valid_session_csrf(environ,form.get("csrf_token","")): return user,render("Requisição recusada","<p>Token CSRF inválido.</p>",user,HTTPStatus.FORBIDDEN),None
    return user,None,form


def handle_update_repository(environ: dict) -> Response:
    user,response,form=_update_form(environ)
    if response: return response
    url=form.get("url","").strip(); channel=form.get("channel","")
    try:
        interval=int(form.get("interval","24"))
        parsed=urlsplit(url)
        if form.get("enabled")=="1" and (parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password): raise ValueError
        if channel not in {"stable","rc"} or not 1 <= interval <= 168: raise ValueError
    except (ValueError,TypeError): return redirect_with_message("/settings/updates","error","Configuração de repositório inválida; use HTTPS sem credenciais.")
    with connect() as conn:
        values={"update_repository_enabled":"1" if form.get("enabled")=="1" else "0","update_repository_url":url,"update_channel":channel,"update_check_interval_hours":str(interval)}
        for key,value in values.items(): conn.execute("UPDATE settings SET value=?,updated_at=CURRENT_TIMESTAMP WHERE key=?",(value,key))
        audit(conn,user["id"],"update.repository_configured","update_repository","",json.dumps({"enabled":values["update_repository_enabled"],"host":parsed.hostname or "","channel":channel,"interval":interval}),environ.get("REMOTE_ADDR",""))
    return redirect_with_message("/settings/updates","success","Repositório configurado.")


def handle_update_check(environ: dict) -> Response:
    user,response,form=_update_form(environ)
    if response: return response
    try:
        with connect() as conn: release=check_updates(conn,public_key=os.environ.get("BACKUP_MANAGER_UPDATE_PUBLIC_KEY",str(DEFAULT_PUBLIC_KEY)))
    except RepositoryError as exc: return redirect_with_message("/settings/updates","error",f"Consulta falhou ({exc.code}).")
    return redirect_with_message("/settings/updates","success",f"Atualização {release['version']} disponível." if release else "Sistema atualizado.")


def handle_update_download(environ: dict) -> Response:
    user,response,form=_update_form(environ)
    if response: return response
    try:
        with connect() as conn: identifier=queue_download(conn,user_id=user["id"])
    except RepositoryError as exc: return redirect_with_message("/settings/updates","error",f"Download não enfileirado ({exc.code}).")
    return redirect_with_message("/settings/updates","info",f"Download enfileirado ({identifier[:8]}).")


def handle_update_upload(environ: dict) -> Response:
    user, response = require_admin(environ)
    if response: return response
    maximum = 512 * 1024 * 1024
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = maximum + 1
    if length <= 0 or length > maximum:
        return redirect_with_message("/settings/updates", "error", "Pacote excede o limite permitido.")
    fields, files = parse_multipart(environ)
    if not valid_session_csrf(environ, fields.get("csrf_token", "")):
        return render("Requisição recusada", "<p>Token CSRF inválido.</p>", user, HTTPStatus.FORBIDDEN)
    uploaded = files.get("package")
    if not uploaded or not uploaded.filename.lower().endswith(".bmu"):
        return redirect_with_message("/settings/updates", "error", "Selecione um pacote .bmu válido.")
    update_root = Path(os.environ.get("BACKUP_MANAGER_UPDATE_ROOT", str(DEFAULT_UPDATE_ROOT)))
    incoming = update_root / "incoming"; incoming.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        operation = create_operation(conn, uploaded.filename, user["id"])
    target = incoming / f"{operation}.bmu"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(target, flags, 0o600), "wb") as output:
            data = uploaded.file.read(maximum + 1)
            if len(data) > maximum: raise UpdateError("PACKAGE_SIZE")
            output.write(data)
        result = validate_package(target, os.environ.get("BACKUP_MANAGER_UPDATE_PUBLIC_KEY", str(DEFAULT_PUBLIC_KEY)))
        with connect() as conn:
            record_validation(conn, operation, result)
            audit(conn, user["id"], "update.package_validated", "update_operation", operation, f"version={result.manifest['version']}", environ.get("REMOTE_ADDR", ""))
        return redirect_with_message("/settings/updates", "success", f"Pacote validado para {result.manifest['version']}. Plano disponível no histórico.")
    except (UpdateError, OSError) as exc:
        code = exc.code if isinstance(exc, UpdateError) else "UPLOAD_FAILED"
        target.unlink(missing_ok=True)
        with connect() as conn:
            conn.execute("UPDATE update_operations SET status='rejected',error_code=?,error_message='Pacote recusado.',finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE uuid=?", (code, operation))
            audit(conn, user["id"], "update.package_rejected", "update_operation", operation, f"code={code}", environ.get("REMOTE_ADDR", ""))
        return redirect_with_message("/settings/updates", "error", f"Pacote recusado ({code}).")


def handle_cloud_item_action(environ: dict, item_id: int, action: str) -> Response:
    user, response = require_admin(environ)
    if response: return response
    with connect() as conn:
        ok = cloud_retry_item(conn,item_id,user["id"]) if action == "retry" else cloud_cancel_item(conn,item_id,user["id"])
    return redirect_with_message("/cloud?view=queue","success" if ok else "info","Item atualizado." if ok else "Ação não aplicável.")


def static_file(path: str, query_string: str = "") -> Response:
    target = (STATIC_DIR / path.replace("/static/", "", 1)).resolve()
    if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
        return Response("Not found", HTTPStatus.NOT_FOUND)
    content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    requested_version = parse_qs(query_string).get("v", [""])[-1]
    current_version = static_version(target)
    cache_control = "public, max-age=31536000, immutable" if requested_version == current_version else "no-cache"
    return Response(target.read_bytes(), HTTPStatus.OK, [
        ("Content-Type", content_type), ("Cache-Control", cache_control),
        ("ETag", f'"{current_version}"'),
    ])


def _emit_wsgi(start_response, response: Response):
    headers = [("Content-Length", str(len(response.body))), *response.headers]
    if not any(key.lower() == "content-type" for key, _ in headers):
        headers.append(("Content-Type", "text/plain; charset=utf-8"))
    start_response(f"{response.status.value} {response.status.phrase}", headers)
    return [response.body]


def _schema_maintenance_response(environ: dict, code: str) -> Response:
    if wants_json(environ):
        return json_response({"ready": False, "code": code}, HTTPStatus.SERVICE_UNAVAILABLE)
    body = ("<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><title>Manutenção</title></head>"
            "<body><main><h1>Aplicação em manutenção</h1>"
            "<p>O banco de dados precisa de atualização administrativa antes de continuar.</p>"
            f"<p>Código: {e(code)}</p></main></body></html>")
    return Response(body, HTTPStatus.SERVICE_UNAVAILABLE, [("Content-Type", "text/html; charset=utf-8"),
                                                            ("Cache-Control", "no-store")])


def _application(environ: dict, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")
    if method not in {"GET", "HEAD", "OPTIONS"} and environ.get("HTTP_SEC_FETCH_SITE", "").lower() == "cross-site":
        return _emit_wsgi(start_response, Response("Requisição entre sites recusada.", HTTPStatus.FORBIDDEN))
    csrf_response = csrf_protect(environ)
    if csrf_response:
        return _emit_wsgi(start_response, csrf_response)
    if method == "GET" and path == "/health/live":
        return _emit_wsgi(start_response, json_response({"live": True}))
    if method == "GET" and path == "/health/ready":
        status = check_schema_version()
        response = json_response({"ready": status.ready, "code": status.code or "READY"},
                                 HTTPStatus.OK if status.ready else HTTPStatus.SERVICE_UNAVAILABLE)
        return _emit_wsgi(start_response, response)
    if method == "GET" and path.startswith("/static/"):
        return _emit_wsgi(start_response, static_file(path, environ.get("QUERY_STRING", "")))
    try:
        require_schema_current()
    except SchemaCompatibilityError as exc:
        return _emit_wsgi(start_response, _schema_maintenance_response(environ, exc.code))
    with connect() as conn:
        DISPLAY_TIMEZONE.set(get_setting(conn, "timezone", "America/Sao_Paulo"))
        DISPLAY_DATE_FORMAT.set(get_setting(conn, "date_format", "ymd"))
        DISPLAY_TIME_FORMAT.set(get_setting(conn, "time_format", "24h"))
    routes = {
        ("GET", "/"): dashboard,
        ("GET", "/login"): lambda env: login_page(),
        ("POST", "/login"): handle_login,
        ("POST", "/logout"): handle_logout,
        ("GET", "/change-password"): lambda env: change_password_page(require_user(env)[0]) if require_user(env)[0] else require_user(env)[1],
        ("POST", "/change-password"): lambda env: handle_change_password(env, require_user(env)[0]) if require_user(env)[0] else require_user(env)[1],
        ("POST", "/profile/avatar"): handle_profile_avatar,
        ("GET", "/setup"): setup_page,
        ("POST", "/setup"): handle_setup,
        ("GET", "/settings"): settings_page,
        ("POST", "/settings/general"): handle_settings_general,
        ("POST", "/settings/users"): handle_settings_users,
        ("POST", "/settings/https"): handle_settings_https,
        ("GET", "/settings/configuration/export"): settings_configuration_export,
        ("POST", "/settings/configuration/import"): settings_configuration_import,
        ("GET", "/settings/branding/logo"): lambda env: branding_file("logo"),
        ("GET", "/settings/branding/favicon"): lambda env: branding_file("favicon"),
        ("GET", "/equipment"): equipment_page,
        ("POST", "/equipment"): handle_equipment_create,
        ("POST", "/equipment/delete"): handle_equipment_delete,
        ("GET", "/backups"): backups_page,
        ("GET", "/jobs"): jobs_page,
        ("GET", "/job-runs"): job_runs_page,
        ("GET", "/audit"): audit_page,
        ("GET", "/reports"): reports_overview_page,
        ("GET", "/reports/backups"): reports_backups_page,
        ("GET", "/reports/equipment"): reports_equipment_page,
        ("GET", "/reports/storage"): reports_storage_page,
        ("GET", "/reports/ftp"): reports_ftp_page,
        ("GET", "/reports/telegram"): reports_telegram_page,
        ("GET", "/reports/audit"): reports_audit_page,
        ("GET", "/reports/exports"): reports_exports_page,
        ("GET", "/reports/export"): reports_export,
        ("GET", "/lifecycle"): lifecycle_page,
        ("POST", "/lifecycle/policy"): handle_lifecycle_policy,
        ("POST", "/lifecycle/simulate"): handle_lifecycle_simulate,
        ("POST", "/lifecycle/execute"): handle_lifecycle_execute,
        ("POST", "/lifecycle/health"): handle_lifecycle_health,
        ("POST", "/lifecycle/empty-trash"): handle_empty_trash,
        ("GET", "/settings/notifications"): notifications_page,
        ("POST", "/settings/notifications"): handle_notifications_save,
        ("POST", "/settings/notifications/test"): handle_notifications_test,
        ("POST", "/settings/notifications/summary-test"): handle_summary_test,
        ("GET", "/settings/notifications/status"): notifications_status,
        ("GET", "/settings/updates"): updates_page,
        ("POST", "/settings/updates/repository"): handle_update_repository,
        ("POST", "/settings/updates/check"): handle_update_check,
        ("POST", "/settings/updates/download"): handle_update_download,
        ("POST", "/settings/updates/upload"): handle_update_upload,
        ("GET", "/ftp"): ftp_accounts_page,
        ("POST", "/ftp"): handle_ftp_create,
        ("GET", "/ftp/uploads"): ftp_uploads_page,
        ("GET", "/cloud"): cloud_page,
        ("GET", "/telegram-backup"): telegram_backup_page,
        ("POST", "/telegram-backup/destinations"): handle_telegram_destination_create,
        ("POST", "/telegram-backup/policies"): handle_telegram_policy_create,
        ("POST", "/telegram-backup/settings"): handle_telegram_backup_settings,
        ("POST", "/telegram-backup/topics"): handle_telegram_topic_create,
        ("POST", "/cloud/targets"): handle_cloud_target_create,
        ("POST", "/cloud/rclone"): handle_rclone_target_create,
        ("POST", "/cloud/rclone/setup"): handle_rclone_setup,
        ("POST", "/cloud/rclone/setup/continue"): handle_rclone_setup_continue,
        ("POST", "/cloud/rclone/setup/cancel"): handle_rclone_setup_cancel,
        ("POST", "/cloud/rclone/google/connect"): handle_google_rclone_connect,
        ("GET", GOOGLE_RCLONE_CALLBACK_PATH): handle_google_rclone_callback,
        ("POST", "/cloud/policies"): handle_cloud_policy_create,
    }
    if path.startswith("/static/"):
        response = static_file(path, environ.get("QUERY_STRING", ""))
    elif method == "GET" and re.fullmatch(r"/equipment/\d+/edit", path):
        response = equipment_edit_page(environ, int(path.split("/")[2]))
    elif method == "GET" and re.fullmatch(r"/ftp/\d+", path):
        response = ftp_account_detail_page(environ, int(path.rsplit("/", 1)[1]))
    elif method == "POST" and re.fullmatch(r"/ftp/\d+/toggle", path):
        response = handle_ftp_toggle(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/ftp/\d+/reset-password", path):
        response = handle_ftp_reset(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/ftp/\d+/settings", path):
        response = handle_ftp_settings(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/ftp/\d+/delete", path):
        response = handle_ftp_delete(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/equipment/\d+/edit", path):
        response = handle_equipment_edit(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/equipment/\d+/deactivate", path):
        response = handle_equipment_deactivate(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/equipment/\d+/archive", path):
        response = handle_equipment_archive(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/equipment/\d+/purge", path):
        response = handle_equipment_purge(environ, int(path.split("/")[2]))
    elif method == "GET" and re.fullmatch(r"/equipment/\d+", path):
        response = equipment_detail_page(environ, int(path.rsplit("/", 1)[1]))
    elif method == "POST" and re.fullmatch(r"/equipment/\d+/backups/import", path):
        response = handle_backup_import(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/equipment/\d+/olt-script", path):
        response = handle_olt_manual_script(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/equipment/\d+/olt-execute", path):
        response = handle_olt_execute(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/equipment/\d+/mikrotik-ftp", path):
        response = handle_mikrotik_ftp_create(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/mikrotik-ftp/\d+/test", path):
        response = handle_mikrotik_ftp_test(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/mikrotik-ftp/\d+/rotate", path):
        response = handle_mikrotik_ftp_rotate(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/mikrotik-ftp/\d+/install-ssh", path):
        response = handle_mikrotik_ftp_install_ssh(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/mikrotik-ftp/\d+/repair-ssh", path):
        response = handle_mikrotik_ftp_install_ssh(environ, int(path.split("/")[2]), repair=True)
    elif method == "POST" and re.fullmatch(r"/mikrotik-ftp/\d+/execute-ssh", path):
        response = handle_mikrotik_ftp_execute_ssh(environ, int(path.split("/")[2]))
    elif method == "GET" and re.fullmatch(r"/mikrotik-ftp/\d+/execute-status", path):
        response = handle_mikrotik_ftp_execute_status(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/mikrotik-ftp/\d+/script", path):
        response = handle_mikrotik_ftp_script(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/mikrotik-ftp/\d+/deactivate", path):
        response = handle_mikrotik_ftp_deactivate(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/mikrotik-ftp/\d+/remove-ssh", path):
        response = handle_mikrotik_ftp_remove_ssh(environ, int(path.split("/")[2]))
    elif method == "GET" and re.fullmatch(r"/mikrotik-ftp/tests/[0-9a-fA-F-]{36}", path):
        response = mikrotik_ftp_test_status(environ, path.rsplit("/", 1)[1])
    elif method == "POST" and re.fullmatch(r"/equipment/\d+/credentials", path):
        response = handle_credential_create(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/equipment/\d+/ssh-test", path):
        response = handle_equipment_ssh_test(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/equipment/\d+/jobs", path):
        response = handle_job_create(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/equipment/\d+/jobs/run-now", path):
        response = handle_equipment_run_now(environ, int(path.split("/")[2]))
    elif method == "GET" and re.fullmatch(r"/backups/[0-9a-fA-F-]{36}", path):
        response = backup_detail_page(environ, path.rsplit("/", 1)[1])
    elif method == "POST" and re.fullmatch(r"/backups/[0-9a-fA-F-]{36}/telegram-enqueue", path):
        response = handle_telegram_backup_enqueue(environ, path.split("/")[2])
    elif method == "GET" and re.fullmatch(r"/backups/[0-9a-fA-F-]{36}/download", path):
        response = download_backup(environ, path.split("/")[2])
    elif method == "POST" and re.fullmatch(r"/backups/[0-9a-fA-F-]{36}/trash", path):
        response = move_backup_to_trash(environ, path.split("/")[2])
    elif method == "POST" and re.fullmatch(r"/backups/[0-9a-fA-F-]{36}/restore", path):
        response = restore_backup(environ, path.split("/")[2])
    elif method == "POST" and re.fullmatch(r"/backups/[0-9a-fA-F-]{36}/purge", path):
        response = handle_backup_purge(environ, path.split("/")[2])
    elif method == "POST" and re.fullmatch(r"/backups/[0-9a-fA-F-]{36}/cloud-enqueue", path):
        response = handle_cloud_backup_enqueue(environ, path.split("/")[2])
    elif method == "POST" and re.fullmatch(r"/cloud/targets/\d+/(toggle|delete|test)", path):
        response = handle_cloud_target_action(environ,int(path.split("/")[3]),path.split("/")[4])
    elif method == "POST" and re.fullmatch(r"/cloud/items/\d+/(retry|cancel)", path):
        response = handle_cloud_item_action(environ,int(path.split("/")[3]),path.split("/")[4])
    elif method == "POST" and re.fullmatch(r"/telegram-backup/items/\d+/(retry|cancel)", path):
        response = handle_telegram_item_action(environ,int(path.split("/")[3]),path.split("/")[4])
    elif method == "POST" and re.fullmatch(r"/telegram-backup/destinations/\d+/test-file", path):
        response = handle_telegram_test_file(environ,int(path.split("/")[3]))
    elif method == "POST" and re.fullmatch(r"/credentials/\d+/edit", path):
        response = handle_credential_edit(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/credentials/\d+/toggle", path):
        response = handle_credential_toggle(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/credentials/\d+/delete", path):
        response = handle_credential_delete(environ, int(path.split("/")[2]))
    elif method == "POST" and re.fullmatch(r"/credentials/\d+/test", path):
        response = handle_credential_test(environ, int(path.split("/")[2]))
    elif method == "GET" and re.fullmatch(r"/jobs/[0-9a-fA-F-]{36}/edit", path):
        response = job_edit_page(environ, path.split("/")[2])
    elif method == "POST" and re.fullmatch(r"/jobs/[0-9a-fA-F-]{36}/edit", path):
        response = handle_job_edit(environ, path.split("/")[2])
    elif method == "POST" and re.fullmatch(r"/jobs/[0-9a-fA-F-]{36}/run", path):
        response = handle_job_run(environ, path.split("/")[2])
    elif method == "POST" and re.fullmatch(r"/jobs/[0-9a-fA-F-]{36}/pause", path):
        response = handle_job_status(environ, path.split("/")[2], "paused", "job.paused")
    elif method == "POST" and re.fullmatch(r"/jobs/[0-9a-fA-F-]{36}/resume", path):
        response = handle_job_status(environ, path.split("/")[2], "active", "job.resumed")
    elif method == "POST" and re.fullmatch(r"/jobs/[0-9a-fA-F-]{36}/delete", path):
        response = handle_job_status(environ, path.split("/")[2], "deleted", "job.deleted")
    elif method == "GET" and re.fullmatch(r"/jobs/[0-9a-fA-F-]{36}/runs", path):
        response = job_runs_page(environ, path.split("/")[2])
    elif method == "GET" and re.fullmatch(r"/job-runs/[0-9a-fA-F-]{36}/status", path):
        response = run_status_endpoint(environ, path.split("/")[2])
    elif method == "GET" and re.fullmatch(r"/job-runs/[0-9a-fA-F-]{36}", path):
        response = run_detail_page(environ, path.split("/")[2])
    else:
        handler = routes.get((method, path))
        response = handler(environ) if handler else Response("Not found", HTTPStatus.NOT_FOUND)
    return _emit_wsgi(start_response, response)


def application(environ: dict, start_response):
    resolved_ip = client_ip(environ)
    environ["REMOTE_ADDR"] = resolved_ip
    REQUEST_IP.set(resolved_ip)
    REQUEST_CSRF.set(session_csrf(environ))
    def secure_start_response(status, headers, exc_info=None):
        names = {key.lower() for key, _ in headers}
        security_headers = [
            ("X-Frame-Options", "DENY"),
            ("X-Content-Type-Options", "nosniff"),
            ("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://accounts.google.com"),
            ("Referrer-Policy", "same-origin"),
        ]
        if environ.get("HTTP_X_FORWARDED_PROTO", "").split(",", 1)[0].strip().lower() == "https":
            security_headers.append(("Strict-Transport-Security", "max-age=31536000; includeSubDomains"))
        headers.extend((key, value) for key, value in security_headers if key.lower() not in names)
        if exc_info is None:
            return start_response(status, headers)
        return start_response(status, headers, exc_info)
    try:
        return _application(environ, secure_start_response)
    except Exception:
        logger.exception("Exceção não tratada na requisição %s %s", environ.get("REQUEST_METHOD", ""), environ.get("PATH_INFO", ""))
        if environ.get("REQUEST_METHOD") == "POST" and environ.get("PATH_INFO") == "/settings/notifications/test":
            _audit_notification_test_error(None, environ, "UnhandledRequestError")
            response = redirect_with_message("/settings?tab=notifications", "error",
                                             "Não foi possível agendar a mensagem de teste. Tente novamente e consulte o journal se o problema persistir.")
            headers = [("Content-Length", str(len(response.body))), *response.headers]
            secure_start_response(f"{response.status.value} {response.status.phrase}", headers)
            return [response.body]
        body = (
            "<!doctype html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>Falha operacional - Backup Manager Local</title></head><body>"
            "<main><h1>Não foi possível concluir a operação</h1>"
            "<p>Ocorreu uma falha interna. Tente novamente; se persistir, consulte a auditoria e os serviços.</p>"
            "<p><a href=\"/\">Voltar ao Dashboard</a></p></main></body></html>"
        ).encode("utf-8")
        secure_start_response("500 Internal Server Error", [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ])
        return [body]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    status = check_schema_version()
    if not status.ready:
        logger.warning("Schema indisponível para operações: %s", status.code)
    host = os.environ.get("BACKUP_MANAGER_HOST", "127.0.0.1")
    port = int(os.environ.get("BACKUP_MANAGER_PORT", "8080"))
    print(f"Backup Manager Local em http://{host}:{port}")
    with make_server(host, port, application, handler_class=ProductionRequestHandler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
