from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .security import hash_password


CONFIG_FORMAT = "backup-manager-configuration"
CONFIG_VERSION = 1
MAX_CONFIG_BYTES = 2 * 1024 * 1024
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
NGINX_SITE_PATHS = (
    Path("/etc/nginx/sites-enabled/backup-manager-local"),
    Path("/etc/nginx/sites-available/backup-manager-local"),
    Path("/etc/nginx/sites-enabled/backup-manager-local.conf"),
    Path("/etc/nginx/sites-available/backup-manager-local.conf"),
)
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
GENERAL_KEYS = {
    "installation_name", "provider_name", "timezone", "date_format", "time_format",
    "institutional_text", "administrative_contact", "home_page", "language",
    "primary_environment",
    "default_retention_days", "trash_retention_days", "maximum_upload_size",
    "lifecycle_enabled", "cloud_sync_enabled", "cloud_sync_auto_enqueue",
    "update_repository_enabled", "update_repository_url", "update_channel",
    "update_check_interval_hours",
    "telegram_daily_summary_enabled", "telegram_daily_summary_time",
    "telegram_weekly_summary_enabled", "telegram_weekly_summary_day", "telegram_weekly_summary_time",
    "telegram_executive_summary_enabled", "telegram_executive_summary_time",
    "telegram_backup_enabled", "telegram_backup_default_destination", "telegram_backup_compression",
    "telegram_backup_max_attempts", "telegram_backup_retry_base_seconds", "telegram_backup_stuck_minutes",
    "telegram_api_mode",
}


def _cpu_usage_percent(sample_seconds: float = 0.12) -> float:
    def snapshot() -> tuple[int, int]:
        values = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values), idle

    try:
        total_before, idle_before = snapshot()
        time.sleep(sample_seconds)
        total_after, idle_after = snapshot()
        total_delta = total_after - total_before
        idle_delta = idle_after - idle_before
        return max(0.0, min(100.0, 100.0 * (total_delta - idle_delta) / total_delta)) if total_delta else 0.0
    except (OSError, ValueError, IndexError):
        return 0.0
SECRET_MARKERS = ("secret", "token", "password", "private", "credential", "session", "key")


def ensure_roles(conn) -> None:
    conn.execute(
        "INSERT INTO roles(slug,name,can_download,can_admin) VALUES('operator','Operador',1,0) "
        "ON CONFLICT(slug) DO UPDATE SET name='Operador',can_download=1,can_admin=0"
    )
    conn.execute("UPDATE roles SET name='Administrador',can_download=1,can_admin=1 WHERE slug='admin'")
    conn.execute("UPDATE roles SET name='Leitura',can_download=1,can_admin=0 WHERE slug='read_download'")


def role_description(slug: str) -> str:
    return {
        "admin": "Acesso completo.",
        "operator": "Executa backups e administra equipamentos.",
        "read_download": "Consulta informações e baixa backups.",
    }.get(slug, "Perfil de acesso do sistema.")


def validate_general(values: dict[str, str]) -> dict[str, str]:
    installation = values.get("installation_name", "").strip()
    company = values.get("provider_name", "").strip()
    timezone_name = values.get("timezone", "").strip()
    if not installation or len(installation) > 120:
        raise ValueError("Informe um nome de instalação válido.")
    if not company or len(company) > 160:
        raise ValueError("Informe uma empresa válida.")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(timezone_name)
    except (KeyError, ValueError):
        raise ValueError("Timezone inválido.") from None
    date_format = values.get("date_format", "dmy")
    time_format = values.get("time_format", "24h")
    home_page = values.get("home_page", "/")
    if date_format not in {"dmy", "ymd"} or time_format not in {"24h", "12h"}:
        raise ValueError("Formato de data ou hora inválido.")
    if home_page not in {"/", "/backups", "/equipment", "/reports"}:
        raise ValueError("Página inicial inválida.")
    contact = values.get("administrative_contact", "").strip()
    if len(contact) > 200:
        raise ValueError("Contato administrativo muito longo.")
    return {
        "installation_name": installation,
        "provider_name": company,
        "timezone": timezone_name,
        "date_format": date_format,
        "time_format": time_format,
        "institutional_text": values.get("institutional_text", "").strip()[:1000],
        "administrative_contact": contact,
        "home_page": home_page,
        "language": "pt_BR",
    }


def validate_branding_file(filename: str, payload: bytes, kind: str) -> str:
    if not payload or len(payload) > 1024 * 1024:
        raise ValueError("O arquivo visual deve ter até 1 MB.")
    suffix = Path(filename).suffix.lower()
    allowed = {"logo": {".png", ".jpg", ".jpeg", ".webp"}, "favicon": {".png", ".ico"}}[kind]
    signatures = {
        ".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff",
        ".webp": b"RIFF", ".ico": b"\x00\x00\x01\x00",
    }
    if suffix not in allowed or not payload.startswith(signatures[suffix]):
        raise ValueError("Formato de imagem não permitido.")
    if suffix == ".webp" and payload[8:12] != b"WEBP":
        raise ValueError("Arquivo WebP inválido.")
    return suffix


def save_branding(data_dir: Path, filename: str, payload: bytes, kind: str) -> str:
    suffix = validate_branding_file(filename, payload, kind)
    directory = data_dir / "branding"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{kind}{suffix}"
    temporary = directory / f".{kind}.tmp"
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o640)
    os.replace(temporary, target)
    return str(target)


def validate_domain(domain: str) -> str:
    normalized = domain.strip().lower().rstrip(".")
    if not DOMAIN_RE.fullmatch(normalized):
        raise ValueError("Informe um domínio público válido.")
    return normalized


def configured_https_domain(fallback: str = "", *, paths: tuple[Path, ...] = NGINX_SITE_PATHS) -> str:
    """Retorna o domínio efetivamente publicado pelo Nginx, com fallback seguro."""
    for path in paths:
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for directive in re.findall(r"^\s*server_name\s+([^;]+);", body, re.MULTILINE):
            for candidate in directive.split():
                try:
                    return validate_domain(candidate)
                except ValueError:
                    continue
    try:
        return validate_domain(fallback) if fallback else ""
    except ValueError:
        return ""


def certbot_auto_renewal(domain: str, *, timer_path: Path = Path("/etc/systemd/system/timers.target.wants/certbot.timer"),
                         renewal_dir: Path = Path("/etc/letsencrypt/renewal")) -> bool:
    """Confirma que o timer está habilitado e há configuração de renovação para o domínio."""
    try:
        normalized = validate_domain(domain)
    except ValueError:
        return False
    return timer_path.exists() and (renewal_dir / f"{normalized}.conf").is_file()


def validate_email(email: str) -> str:
    normalized = email.strip()
    if not EMAIL_RE.fullmatch(normalized) or len(normalized) > 254:
        raise ValueError("Informe um e-mail válido.")
    return normalized


def certificate_summary(domain: str, *, timeout: float = 4.0) -> dict[str, object]:
    result = {"domain": domain, "issuer": "-", "issued_at": "", "expires_at": "", "days_remaining": None,
              "valid": False}
    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=domain) as secured:
                certificate = secured.getpeercert()
        issuer_parts = dict(item[0] for item in certificate.get("issuer", ()))
        issued = datetime.strptime(certificate["notBefore"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        expires = datetime.strptime(certificate["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        result.update({"issuer": issuer_parts.get("organizationName") or issuer_parts.get("commonName") or "-",
                       "issued_at": issued.isoformat(), "expires_at": expires.isoformat(),
                       "days_remaining": max(0, (expires - datetime.now(timezone.utc)).days), "valid": expires > datetime.now(timezone.utc)})
    except (OSError, ssl.SSLError, KeyError, ValueError):
        pass
    return result


def https_checks(domain: str, *, timeout: float = 3.0) -> dict[str, object]:
    domain = validate_domain(domain)
    result: dict[str, object] = {"domain": domain, "resolves": False, "port_80": False, "port_443": False,
                                "certificate_valid": False, "nginx_configured": False}
    try:
        addresses = socket.getaddrinfo(domain, None, type=socket.SOCK_STREAM)
        result["resolves"] = bool(addresses)
    except OSError:
        pass
    try:
        with socket.create_connection((domain, 443), timeout=timeout):
            result["port_443"] = True
    except OSError:
        pass
    try:
        with socket.create_connection((domain, 80), timeout=timeout):
            result["port_80"] = True
    except OSError:
        pass
    certificate = certificate_summary(domain, timeout=timeout)
    result["certificate_valid"] = certificate["valid"]
    for path in NGINX_SITE_PATHS:
        try:
            text = path.read_text(encoding="utf-8")
            if domain in text and "listen 443 ssl" in text:
                result["nginx_configured"] = True
                break
        except OSError:
            continue
    result["certificate"] = certificate
    return result


def run_https_helper(action: str, domain: str, email: str, *, confirmed: bool,
                     runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> tuple[bool, str]:
    if action not in {"issue", "renew"}:
        raise ValueError("Ação HTTPS inválida.")
    domain, email = validate_domain(domain), validate_email(email)
    if not confirmed:
        raise ValueError("Confirme explicitamente a alteração do acesso HTTPS.")
    helper = os.environ.get("BACKUP_MANAGER_HTTPS_HELPER", "/opt/backup-manager-local/scripts/configure-https.sh")
    command = ["/usr/bin/sudo", "-n", helper, "--action", action, "--domain", domain, "--email", email, "--confirm"]
    try:
        completed = runner(command, capture_output=True, text=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False, "O helper HTTPS não pôde ser executado."
    detail = (completed.stdout if completed.returncode == 0 else completed.stderr).strip()[-500:]
    return completed.returncode == 0, detail or ("Operação concluída." if completed.returncode == 0 else "Operação recusada.")


def system_summary(storage_root: str) -> dict[str, object]:
    os_name = "Linux"
    try:
        values = {}
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')
        os_name = values.get("PRETTY_NAME", os_name)
    except OSError:
        pass
    ipv4: list[str] = []
    ipv6: list[str] = []
    interfaces: list[str] = []
    try:
        completed = subprocess.run(["/usr/sbin/ip", "-j", "address", "show"], capture_output=True, text=True,
                                   timeout=3, check=False)
        for interface in json.loads(completed.stdout or "[]"):
            if interface.get("ifname") == "lo":
                continue
            if interface.get("ifname"):
                interfaces.append(interface["ifname"])
            for address in interface.get("addr_info", []):
                local = address.get("local", "")
                if address.get("family") == "inet" and local:
                    ipv4.append(local)
                elif address.get("family") == "inet6" and local and not local.startswith("fe80:"):
                    ipv6.append(local)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    dns_servers: list[str] = []
    try:
        for line in Path("/etc/resolv.conf").read_text().splitlines():
            if line.strip().startswith("nameserver "):
                dns_servers.append(line.split()[1])
    except (OSError, IndexError):
        pass
    memory_total = memory_available = 0
    try:
        memory = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            memory[key] = int(value.strip().split()[0]) * 1024
        memory_total, memory_available = memory.get("MemTotal", 0), memory.get("MemAvailable", 0)
    except (OSError, ValueError):
        pass
    try:
        uptime_seconds = int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        uptime_seconds = 0
    cpu_count = max(1, os.cpu_count() or 1)
    cpu_percent = _cpu_usage_percent()
    try:
        load_average = os.getloadavg()
    except OSError:
        load_average = (0.0, 0.0, 0.0)
    root = Path(storage_root)
    while not root.exists() and root != root.parent:
        root = root.parent
    disk = shutil.disk_usage(root)
    uname = os.uname()
    return {"os": os_name, "hostname": socket.gethostname(), "ipv4": ipv4, "ipv6": ipv6,
            "interfaces": interfaces, "dns_servers": dns_servers,
            "kernel": uname.release, "architecture": uname.machine, "cpu_count": cpu_count,
            "load_average": load_average, "storage_root": str(root),
            "cpu_percent": cpu_percent, "memory_used": max(0, memory_total - memory_available),
            "memory_total": memory_total, "disk_used": disk.used, "disk_total": disk.total,
            "uptime_seconds": uptime_seconds, "now": datetime.now(timezone.utc).isoformat()}


def _rows(conn, query: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def export_configuration(conn) -> bytes:
    settings = {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM settings")
                if row["key"] in GENERAL_KEYS and not any(marker in row["key"].lower() for marker in SECRET_MARKERS)}
    payload = {
        "format": CONFIG_FORMAT, "version": CONFIG_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "secrets_included": False,
        "settings": settings,
        "groups": _rows(conn, "SELECT name,description FROM equipment_groups ORDER BY name"),
        "vendors": _rows(conn, "SELECT name FROM vendors ORDER BY name"),
        "pops": _rows(conn, "SELECT name,city,state FROM pops ORDER BY name"),
        "environments": _rows(conn, "SELECT name,is_primary FROM environments ORDER BY name"),
        "equipment": _rows(conn, """SELECT e.name,e.hostname,e.ip_address,e.ssh_port,e.ssh_backup_driver,e.ssh_custom_command,e.is_active,e.notes,
                            v.name vendor,g.name group_name,p.name pop,en.name environment
                            FROM equipment e LEFT JOIN vendors v ON v.id=e.vendor_id
                            LEFT JOIN equipment_groups g ON g.id=e.group_id LEFT JOIN pops p ON p.id=e.pop_id
                            LEFT JOIN environments en ON en.id=e.environment_id ORDER BY e.hostname"""),
        "schedules": _rows(conn, """SELECT j.uuid,e.hostname equipment,j.job_type,j.method,j.schedule_enabled,j.schedule_type,
                            j.schedule_time,j.schedule_days,j.timezone,j.status,j.notes FROM backup_jobs j
                            JOIN equipment e ON e.id=j.equipment_id WHERE j.deleted_at IS NULL ORDER BY j.id"""),
        "retention": _rows(conn, """SELECT e.hostname equipment,r.max_count,r.max_age_days,r.trash_retention_days,
                            r.rejected_retention_days,r.is_enabled FROM retention_policies r
                            LEFT JOIN equipment e ON e.id=r.equipment_id ORDER BY r.id"""),
        "notifications": _rows(conn, """SELECT channel_type,is_enabled,cooldown_minutes,maintenance_enabled,maintenance_start,
                              maintenance_end,timezone,daily_summary_enabled,daily_summary_time,weekly_summary_enabled,
                              weekly_summary_day,weekly_summary_time FROM notification_channels ORDER BY channel_type"""),
        "rclone": {
            "targets": _rows(conn, """SELECT t.uuid,t.name,t.is_active,t.destination_label,t.bandwidth_limit_kbps,
                              t.sync_window_start,t.sync_window_end,t.max_attempts,t.retry_base_seconds,t.notes,
                              c.remote_name,c.base_path FROM cloud_targets t JOIN rclone_connections c ON c.target_id=t.id
                              WHERE t.deleted_at IS NULL ORDER BY t.name"""),
            "policies": _rows(conn, """SELECT t.uuid target_uuid,p.uuid,p.scope_type,e.hostname equipment,
                             en.name environment,g.name group_name,v.name vendor,po.name pop,p.enabled,p.sync_new_backups,
                             p.include_ssh,p.include_ftp,p.minimum_file_size,p.maximum_file_size
                             FROM cloud_sync_policies p JOIN cloud_targets t ON t.id=p.target_id
                             LEFT JOIN equipment e ON e.id=p.equipment_id LEFT JOIN environments en ON en.id=p.environment_id
                             LEFT JOIN equipment_groups g ON g.id=p.group_id LEFT JOIN vendors v ON v.id=p.vendor_id
                             LEFT JOIN pops po ON po.id=p.pop_id JOIN rclone_connections rc ON rc.target_id=t.id
                             WHERE p.deleted_at IS NULL ORDER BY p.id"""),
        },
        "users": _rows(conn, """SELECT u.username,u.full_name,r.slug role,u.is_active
                         FROM users u JOIN roles r ON r.id=u.role_id ORDER BY u.username"""),
        "reconfigure": ["Credenciais dos equipamentos", "Credenciais do arquivo rclone", "Token de notificações",
                        "Certificados HTTPS"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def parse_configuration(payload: bytes) -> dict:
    if not payload or len(payload) > MAX_CONFIG_BYTES:
        raise ValueError("Arquivo de configuração vazio ou maior que 2 MB.")
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Arquivo de configuração inválido.") from None
    if not isinstance(data, dict) or data.get("format") != CONFIG_FORMAT or data.get("version") != CONFIG_VERSION:
        raise ValueError("Formato ou versão do arquivo não suportado.")
    if data.get("secrets_included") is not False:
        raise ValueError("Arquivo recusado: o pacote não pode declarar segredos.")
    for key in ("settings", "groups", "vendors", "pops", "environments", "equipment", "users"):
        if key not in data or not isinstance(data[key], dict if key == "settings" else list):
            raise ValueError("Arquivo de configuração incompleto.")
    serialized = json.dumps(data, ensure_ascii=False).lower()
    for forbidden in ("password_hash", "token_encrypted", "private_key", "client_secret", "refresh_token"):
        if forbidden in serialized:
            raise ValueError("Arquivo recusado por conter campo secreto.")
    return data


def import_configuration(conn, payload: bytes) -> dict[str, int]:
    data = parse_configuration(payload)
    ensure_roles(conn)
    counts = {"settings": 0, "groups": 0, "vendors": 0, "pops": 0, "environments": 0,
              "equipment": 0, "users": 0, "schedules": 0, "retention": 0}
    for key, value in data["settings"].items():
        if key in GENERAL_KEYS and isinstance(value, str) and not any(marker in key.lower() for marker in SECRET_MARKERS):
            conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
                         (key, value[:4000]))
            counts["settings"] += 1
    for item in data["groups"]:
        name = str(item.get("name", "")).strip()[:120]
        if name:
            conn.execute("INSERT INTO equipment_groups(name,description) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET description=excluded.description",
                         (name, str(item.get("description", ""))[:500]))
            counts["groups"] += 1
    for item in data["vendors"]:
        name = str(item.get("name", "")).strip()[:120]
        if name:
            conn.execute("INSERT INTO vendors(name) VALUES(?) ON CONFLICT(name) DO NOTHING", (name,))
            counts["vendors"] += 1
    for item in data["pops"]:
        name = str(item.get("name", "")).strip()[:120]
        if name:
            conn.execute("INSERT INTO pops(name,city,state) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET city=excluded.city,state=excluded.state",
                         (name, str(item.get("city", ""))[:120], str(item.get("state", ""))[:40]))
            counts["pops"] += 1
    for item in data["environments"]:
        name = str(item.get("name", "")).strip()[:120]
        if name:
            conn.execute("INSERT INTO environments(name,is_primary) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET is_primary=excluded.is_primary",
                         (name, 1 if item.get("is_primary") else 0))
            counts["environments"] += 1
    lookup = lambda table, name: (conn.execute(f"SELECT id FROM {table} WHERE name=?", (name,)).fetchone() or [None])[0] if name else None
    for item in data["equipment"]:
        hostname = str(item.get("hostname", "")).strip()[:120]
        address = str(item.get("ip_address", "")).strip()[:253]
        if not hostname or not address:
            continue
        values = (str(item.get("name", hostname))[:120], address, lookup("vendors", item.get("vendor")),
                  lookup("equipment_groups", item.get("group_name")), lookup("pops", item.get("pop")),
                  lookup("environments", item.get("environment")), int(item.get("ssh_port") or 22),
                  str(item.get("ssh_backup_driver", ""))[:40], str(item.get("ssh_custom_command", ""))[:500],
                  1 if item.get("is_active", True) else 0, str(item.get("notes", ""))[:500])
        conn.execute("""INSERT INTO equipment(hostname,name,ip_address,vendor_id,group_id,pop_id,environment_id,ssh_port,ssh_backup_driver,ssh_custom_command,is_active,notes)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(hostname) DO UPDATE SET name=excluded.name,ip_address=excluded.ip_address,
                      vendor_id=excluded.vendor_id,group_id=excluded.group_id,pop_id=excluded.pop_id,environment_id=excluded.environment_id,
                      ssh_port=excluded.ssh_port,ssh_backup_driver=excluded.ssh_backup_driver,ssh_custom_command=excluded.ssh_custom_command,
                      is_active=excluded.is_active,notes=excluded.notes""", (hostname, *values))
        counts["equipment"] += 1
    for item in data["users"]:
        username = str(item.get("username", "")).strip()[:80]
        role = item.get("role") if item.get("role") in {"admin", "operator", "read_download"} else "read_download"
        role_row = conn.execute("SELECT id FROM roles WHERE slug=?", (role,)).fetchone()
        existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone() if username else None
        if username and role_row and existing:
            conn.execute("UPDATE users SET full_name=?,role_id=?,is_active=? WHERE id=?",
                         (str(item.get("full_name", username))[:120], role_row["id"], 1 if item.get("is_active", True) else 0, existing["id"]))
            counts["users"] += 1
        elif username and role_row:
            unavailable_password = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO users(username,full_name,password_hash,role_id,must_change_password,is_active) VALUES(?,?,?,?,1,?)",
                         (username, str(item.get("full_name", username))[:120], hash_password(unavailable_password), role_row["id"],
                          1 if item.get("is_active", True) else 0))
            counts["users"] += 1
    for item in data.get("schedules", []):
        equipment = conn.execute("SELECT id FROM equipment WHERE hostname=?", (item.get("equipment"),)).fetchone()
        identifier = str(item.get("uuid", ""))
        if not equipment or not re.fullmatch(r"[0-9a-fA-F-]{36}", identifier):
            continue
        conn.execute("""INSERT INTO backup_jobs(uuid,equipment_id,job_type,method,schedule_enabled,schedule_type,schedule_time,
                     schedule_days,timezone,status,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(uuid) DO UPDATE SET
                     equipment_id=excluded.equipment_id,method=excluded.method,schedule_enabled=excluded.schedule_enabled,
                     schedule_type=excluded.schedule_type,schedule_time=excluded.schedule_time,schedule_days=excluded.schedule_days,
                     timezone=excluded.timezone,status=excluded.status,notes=excluded.notes""",
                     (identifier, equipment["id"], item.get("job_type", "scheduled"), item.get("method", "manual"),
                      1 if item.get("schedule_enabled") else 0, item.get("schedule_type", "none"), item.get("schedule_time", ""),
                      item.get("schedule_days", ""), item.get("timezone", "UTC"), item.get("status", "active"), str(item.get("notes", ""))[:500]))
        counts["schedules"] += 1
    for item in data.get("retention", []):
        equipment = conn.execute("SELECT id FROM equipment WHERE hostname=?", (item.get("equipment"),)).fetchone() if item.get("equipment") else None
        equipment_id = equipment["id"] if equipment else None
        existing = conn.execute("SELECT id FROM retention_policies WHERE equipment_id IS ?", (equipment_id,)).fetchone()
        values = (item.get("max_count"), item.get("max_age_days"), item.get("trash_retention_days"),
                  item.get("rejected_retention_days"), 1 if item.get("is_enabled", True) else 0)
        if existing:
            conn.execute("UPDATE retention_policies SET max_count=?,max_age_days=?,trash_retention_days=?,rejected_retention_days=?,is_enabled=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (*values, existing["id"]))
        else:
            conn.execute("INSERT INTO retention_policies(equipment_id,max_count,max_age_days,trash_retention_days,rejected_retention_days,is_enabled) VALUES(?,?,?,?,?,?)",
                         (equipment_id, *values))
        counts["retention"] += 1
    for item in data.get("notifications", []):
        if item.get("channel_type") != "telegram":
            continue
        conn.execute("""UPDATE notification_channels SET is_enabled=0,cooldown_minutes=?,maintenance_enabled=?,
                     maintenance_start=?,maintenance_end=?,timezone=?,daily_summary_enabled=?,daily_summary_time=?,
                     weekly_summary_enabled=?,weekly_summary_day=?,weekly_summary_time=?,last_status='requires_reconfiguration',
                     updated_at=CURRENT_TIMESTAMP WHERE channel_type='telegram'""",
                     (item.get("cooldown_minutes", 60), 1 if item.get("maintenance_enabled") else 0,
                      item.get("maintenance_start", "00:00"), item.get("maintenance_end", "00:00"), item.get("timezone", "America/Sao_Paulo"),
                      1 if item.get("daily_summary_enabled") else 0, item.get("daily_summary_time", "08:00"),
                      1 if item.get("weekly_summary_enabled") else 0, item.get("weekly_summary_day", 0), item.get("weekly_summary_time", "08:00")))
    external = data.get("rclone", {}) if isinstance(data.get("rclone"), dict) else {}
    for item in external.get("targets", []):
        identifier = str(item.get("uuid", ""))
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", identifier):
            continue
        conn.execute("""INSERT INTO cloud_targets(uuid,name,provider,mode,is_active,destination_label,bandwidth_limit_kbps,
                     sync_window_start,sync_window_end,max_attempts,retry_base_seconds,notes)
                     VALUES(?,?,'simulate','simulate',0,?,?,?,?,?,?,?) ON CONFLICT(uuid) DO UPDATE SET name=excluded.name,
                     is_active=0,destination_label=excluded.destination_label,bandwidth_limit_kbps=excluded.bandwidth_limit_kbps,
                     sync_window_start=excluded.sync_window_start,sync_window_end=excluded.sync_window_end,
                     max_attempts=excluded.max_attempts,retry_base_seconds=excluded.retry_base_seconds,notes=excluded.notes""",
                     (identifier, str(item.get("name", "rclone"))[:120], str(item.get("destination_label", ""))[:160],
                      item.get("bandwidth_limit_kbps"), item.get("sync_window_start"), item.get("sync_window_end"),
                      item.get("max_attempts", 3), item.get("retry_base_seconds", 60), str(item.get("notes", ""))[:500]))
        target = conn.execute("SELECT id FROM cloud_targets WHERE uuid=?", (identifier,)).fetchone()
        if target and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", str(item.get("remote_name", ""))):
            conn.execute("""INSERT INTO rclone_connections(target_id,remote_name,base_path,status,last_error_code)
                         VALUES(?,?,?,'configured',NULL) ON CONFLICT(target_id) DO UPDATE SET
                         remote_name=excluded.remote_name,base_path=excluded.base_path,status='configured',last_error_code=NULL""",
                         (target["id"], item["remote_name"], str(item.get("base_path", ""))[:240]))
    for item in external.get("policies", []):
        identifier = str(item.get("uuid", "")); target_uuid = str(item.get("target_uuid", ""))
        target = conn.execute("SELECT id FROM cloud_targets WHERE uuid=?", (target_uuid,)).fetchone()
        scope = item.get("scope_type", "global")
        if not target or not re.fullmatch(r"[0-9a-fA-F-]{36}", identifier) or scope not in {"global", "equipment", "environment", "group", "vendor", "pop"}:
            continue
        environment_id = lookup("environments", item.get("environment"))
        group_id = lookup("equipment_groups", item.get("group_name"))
        vendor_id = lookup("vendors", item.get("vendor"))
        pop_id = lookup("pops", item.get("pop"))
        equipment_row = conn.execute("SELECT id FROM equipment WHERE hostname=?", (item.get("equipment"),)).fetchone() if item.get("equipment") else None
        equipment_id = equipment_row["id"] if equipment_row else None
        existing_policy = conn.execute("SELECT id FROM cloud_sync_policies WHERE uuid=?", (identifier,)).fetchone()
        policy_values = (target["id"], scope, equipment_id, environment_id, group_id, vendor_id, pop_id, 0,
                         1 if item.get("sync_new_backups") else 0, 1 if item.get("include_ssh", True) else 0,
                         1 if item.get("include_ftp", True) else 0, item.get("minimum_file_size"), item.get("maximum_file_size"))
        if existing_policy:
            conn.execute("""UPDATE cloud_sync_policies SET target_id=?,scope_type=?,equipment_id=?,environment_id=?,group_id=?,vendor_id=?,pop_id=?,
                         enabled=?,sync_new_backups=?,include_ssh=?,include_ftp=?,minimum_file_size=?,maximum_file_size=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                         (*policy_values, existing_policy["id"]))
        else:
            conn.execute("""INSERT INTO cloud_sync_policies(uuid,target_id,scope_type,equipment_id,environment_id,group_id,vendor_id,pop_id,
                         enabled,sync_new_backups,include_ssh,include_ftp,minimum_file_size,maximum_file_size) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (identifier, *policy_values))
    # Tokens, credenciais e conexões permanecem deliberadamente ausentes e desabilitados.
    return counts
