from __future__ import annotations

import json
import hashlib
import hmac
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .observability import dashboard_snapshot
from .security import decrypt_secret, encrypt_secret
from .telegram_formatting import tg_escape

TELEGRAM_TOKEN_RE = re.compile(r"^[0-9]{6,15}:[A-Za-z0-9_-]{20,80}$")
CHAT_ID_RE = re.compile(r"^-?[0-9]{5,20}$")
WEEKDAY_LABELS = ("Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo")
AUDIT_EVENTS = {
    "backup.manual_uploaded": ("backup_completed", "info", "Backup concluído"),
    "backup.created_from_ssh": ("backup_completed", "info", "Backup SSH concluído"),
    "backup.created_from_ftp": ("ftp_received_file", "info", "Backup recebido por FTP"),
    "job.run_failed": ("backup_failed", "critical", "Backup falhou"),
    "job.run_timeout": ("backup_failed", "critical", "Backup expirou"),
    "ssh.backup_failed": ("backup_failed", "critical", "Backup SSH falhou"),
    "ftp.upload_failed": ("backup_failed", "critical", "Backup FTP falhou"),
    "ftp.upload_rejected": ("ftp_rejected", "warning", "Backup FTP rejeitado"),
    "lifecycle.executed": ("lifecycle_retention_cleanup", "info", "Limpeza de backups expirados concluída"),
    "lifecycle.trash_emptied": ("lifecycle_trash_cleanup", "info", "Limpeza da lixeira concluída"),
    "lifecycle.backup_restored": ("backup_restored", "info", "Backup restaurado"),
    "cloud.sync_failed": ("cloud.sync_failed", "critical", "Sincronização simulada falhou"),
    "cloud.sync_retry_scheduled": ("cloud.sync_failed", "warning", "Retry de sincronização agendado"),
    "cloud.rclone_upload_success": ("cloud.upload_completed", "info", "Backup enviado ao Google Drive"),
}


def notification_next_send(enabled: bool, scheduled_time: str, timezone_name: str,
                           weekday: int | None = None, now: datetime | None = None) -> str:
    """Return the next local summary occurrence without changing scheduler semantics."""
    if not enabled:
        return "Desativado"
    try:
        zone = ZoneInfo(timezone_name or "UTC")
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    current = (now or datetime.now(timezone.utc)).astimezone(zone)
    try:
        hour, minute = (int(part) for part in scheduled_time.split(":", 1))
        candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except (AttributeError, TypeError, ValueError):
        return "Horário inválido"
    if weekday is None:
        if candidate <= current:
            candidate += timedelta(days=1)
    else:
        candidate += timedelta(days=(weekday - candidate.weekday()) % 7)
        if candidate <= current:
            candidate += timedelta(days=7)
    return candidate.strftime("%d/%m/%Y às %H:%M")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sql_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_sql(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value or "", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class Transport(Protocol):
    def send(self, token: str, chat_id: str, message: str) -> tuple[int, str]: ...


@dataclass(frozen=True)
class TelegramAPIError(Exception):
    http_status: int | None
    error_code: int | None
    description: str
    retry_after: int | None = None
    migrate_to_chat_id: str | None = None

    @property
    def safe_code(self) -> str:
        return f"telegram_http_{self.http_status or 0}_api_{self.error_code or 0}"

    @property
    def friendly_message(self) -> str:
        text = self.description.lower()
        if self.error_code == 401: return "Token do bot inválido ou revogado."
        if "message thread not found" in text: return "O tópico informado não existe."
        if "topic_closed" in text or "topic closed" in text: return "O token e o Chat ID estão corretos, mas o tópico Geral deste grupo está fechado. Abra o tópico Geral ou use o teste em tópico."
        if "chat not found" in text: return "O grupo não foi encontrado. Verifique o Chat ID e se o bot participa do grupo."
        if "kicked" in text: return "O bot foi removido do grupo."
        if "not enough rights" in text or "have no rights" in text: return "O bot não possui permissão para enviar mensagens."
        if self.error_code == 429: return "Limite temporário do Telegram. Nova tentativa agendada."
        if self.http_status and self.http_status >= 500: return "O Telegram está temporariamente indisponível."
        if self.http_status is None: return "Não foi possível conectar ao Telegram."
        return self.description or "O Telegram recusou a entrega."


def _safe_description(value: object, token: str = "") -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()
    if token: text = text.replace(token, "[segredo]")
    text = re.sub(r"https?://\S+", "[url]", text)
    return re.sub(r"[^\w À-ÿ.,:;!?()'\-]", "", text)[:240]


class TelegramTransport:
    def call(self, token: str, method: str, fields: dict[str, str] | None = None) -> dict:
        data = urllib.parse.urlencode(fields or {}).encode("utf-8")
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(65536)
                payload = json.loads(raw.decode("utf-8"))
                if response.status != 200 or not payload.get("ok"):
                    raise self._api_error(response.status, payload, token)
                return payload.get("result", {})
        except urllib.error.HTTPError as exc:
            try: payload = json.loads(exc.read(65536).decode("utf-8"))
            except (ValueError, UnicodeDecodeError): payload = {}
            raise self._api_error(exc.code, payload, token) from None
        except TelegramAPIError:
            raise
        except (urllib.error.URLError, TimeoutError, ValueError, UnicodeDecodeError) as exc:
            reason = "timeout" if isinstance(exc, TimeoutError) else type(exc).__name__
            raise TelegramAPIError(None, None, _safe_description(reason, token)) from None

    @staticmethod
    def _api_error(status: int, payload: dict, token: str) -> TelegramAPIError:
        parameters = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
        migrate = parameters.get("migrate_to_chat_id")
        return TelegramAPIError(status, payload.get("error_code"), _safe_description(payload.get("description"), token),
                                parameters.get("retry_after"), str(migrate) if migrate is not None else None)

    def send(self, token: str, chat_id: str, message: str, thread_id: int | None = None, parse_mode: str | None = None) -> tuple[int, str]:
        fields = {"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}
        if thread_id:
            fields["message_thread_id"] = str(thread_id)
        if parse_mode:
            fields["parse_mode"] = parse_mode
        result = self.call(token, "sendMessage", fields)
        return 200, str(result.get("message_id", ""))

    def diagnose(self, token: str, chat_id: str, send_test: bool = False) -> dict:
        bot = self.call(token, "getMe")
        chat = self.call(token, "getChat", {"chat_id": chat_id})
        member = self.call(token, "getChatMember", {"chat_id": chat_id, "user_id": str(bot["id"])})
        status = member.get("status", "unknown")
        can_send = status in {"creator", "administrator", "member"}
        if chat.get("type") == "channel":
            can_send = status in {"creator", "administrator"} and member.get("can_post_messages", True)
        result = {"bot_id": bot.get("id"), "username": bot.get("username", ""), "chat_title": chat.get("title", ""),
                  "chat_type": chat.get("type", ""), "is_forum": bool(chat.get("is_forum")),
                  "member_status": status, "can_send": bool(can_send),
                  "can_post_messages": member.get("can_post_messages"), "sent_message_id": ""}
        if send_test:
            _, result["sent_message_id"] = self.send(token, chat_id, "Backup Manager Local: diagnóstico Telegram concluído com sucesso.")
        return result


@dataclass(frozen=True)
class CycleResult:
    scanned: int = 0
    queued: int = 0
    sent: int = 0
    retried: int = 0
    failed: int = 0
    suppressed: int = 0


def channel(conn):
    return conn.execute("SELECT * FROM notification_channels WHERE channel_type='telegram'").fetchone()


def telegram_config_hash(token: str, chat_id: str) -> str:
    """Return a non-reversible identity for one exact Telegram configuration."""
    payload = f"telegram-config-v1\0{len(token)}\0{token}\0{len(chat_id)}\0{chat_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def current_telegram_config_hash(row) -> str:
    if not row or not row["token_encrypted"] or not row["chat_id"]:
        return ""
    return telegram_config_hash(decrypt_secret(row["token_encrypted"]), row["chat_id"])


def telegram_diagnostic(conn, row=None) -> dict[str, str]:
    """Load the diagnostic only when it belongs to the currently saved credentials."""
    current = row or channel(conn)
    values = {item["key"]: item["value"] for item in
              conn.execute("SELECT key,value FROM settings WHERE key LIKE 'telegram_diag_%'")}
    expected = current_telegram_config_hash(current)
    if not expected or not hmac.compare_digest(values.get("telegram_diag_config_hash", ""), expected):
        return {}
    return values


def invalidate_telegram_diagnostic(conn) -> None:
    conn.execute("DELETE FROM settings WHERE key LIKE 'telegram_diag_%'")


def notification_page_context(conn) -> dict[str, object]:
    row = channel(conn)
    diag = telegram_diagnostic(conn, row)
    history = conn.execute("""SELECT notification_queue.*,notification_history.status AS attempt_status,
                   notification_history.attempt,notification_history.error_code,notification_history.created_at AS attempt_at
                   FROM notification_history JOIN notification_queue ON notification_queue.id=notification_history.queue_id
                   ORDER BY notification_history.id DESC LIMIT 50""").fetchall()
    queue_stats = conn.execute("""SELECT status,COUNT(*) AS total FROM notification_queue
                                   GROUP BY status""").fetchall()
    summary_settings = conn.execute("SELECT * FROM telegram_summary_settings WHERE id=1").fetchone()
    summary_history = conn.execute("""SELECT r.*,d.name destination_name FROM telegram_summary_runs r
            JOIN telegram_destinations d ON d.id=r.destination_id ORDER BY r.id DESC LIMIT 30""").fetchall()
    recent_failed = conn.execute("SELECT COUNT(*) FROM notification_history WHERE status='failed' AND created_at>=datetime('now','-24 hours')").fetchone()[0]
    last_success = conn.execute("SELECT MAX(created_at) FROM notification_history WHERE status='sent'").fetchone()[0]
    last_failure = conn.execute("SELECT MAX(created_at) FROM notification_history WHERE status='failed'").fetchone()[0]
    last_sent = conn.execute("SELECT MAX(sent_at) FROM notification_queue WHERE status='sent'").fetchone()[0]
    return {
        "row": row,
        "diag": diag,
        "history": history,
        "queue_stats": queue_stats,
        "summary_settings": summary_settings,
        "summary_history": summary_history,
        "recent_failed": recent_failed,
        "last_success": last_success,
        "last_failure": last_failure,
        "last_sent": last_sent,
    }


def configure_telegram(conn, *, enabled: bool, token: str, chat_id: str, cooldown_minutes: int,
                       maintenance_enabled: bool, maintenance_start: str, maintenance_end: str,
                       timezone_name: str, daily_enabled: bool, daily_time: str,
                       weekly_enabled: bool, weekly_day: int, weekly_time: str, user_id=None,
                       executive_enabled: bool = False, executive_time: str = "18:00") -> None:
    current = channel(conn)
    if token and not TELEGRAM_TOKEN_RE.fullmatch(token):
        raise ValueError("Token Telegram inválido.")
    if enabled and not token and not current["token_encrypted"]:
        raise ValueError("Informe o token antes de habilitar.")
    if chat_id and not CHAT_ID_RE.fullmatch(chat_id):
        raise ValueError("Chat ID inválido.")
    if enabled and not chat_id:
        raise ValueError("Chat ID obrigatório.")
    if not 1 <= cooldown_minutes <= 10080 or not 0 <= weekly_day <= 6:
        raise ValueError("Cooldown ou dia semanal inválido.")
    for value in (maintenance_start, maintenance_end, daily_time, weekly_time, executive_time):
        try: time.fromisoformat(value)
        except ValueError as exc: raise ValueError("Horário inválido.") from exc
    try: ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc: raise ValueError("Timezone inválido.") from exc
    effective_token = token or decrypt_secret(current["token_encrypted"])
    old_hash = current_telegram_config_hash(current)
    new_hash = telegram_config_hash(effective_token, chat_id) if effective_token and chat_id else ""
    config_changed = not hmac.compare_digest(old_hash, new_hash)
    encrypted = encrypt_secret(token) if token else current["token_encrypted"]
    first_enable = enabled and not current["is_enabled"]
    conn.execute("""UPDATE notification_channels SET is_enabled=?,token_encrypted=?,chat_id=?,cooldown_minutes=?,
                 maintenance_enabled=?,maintenance_start=?,maintenance_end=?,timezone=?,daily_summary_enabled=?,
                 daily_summary_time=?,weekly_summary_enabled=?,weekly_summary_day=?,weekly_summary_time=?,
                 last_status=?,last_error='',updated_at=CURRENT_TIMESTAMP WHERE channel_type='telegram'""",
                 (int(enabled), encrypted, chat_id, cooldown_minutes, int(maintenance_enabled), maintenance_start,
                  maintenance_end, timezone_name, int(daily_enabled), daily_time, int(weekly_enabled), weekly_day,
                 weekly_time, "not_verified" if enabled and config_changed else "configured" if enabled else "disabled"))
    if config_changed:
        invalidate_telegram_diagnostic(conn)
        conn.execute("UPDATE notification_channels SET last_test_at=NULL WHERE channel_type='telegram'")
    if first_enable:
        last_audit = conn.execute("SELECT COALESCE(MAX(id),0) FROM audit_log").fetchone()[0]
        conn.execute("UPDATE notification_cursors SET last_id=?,updated_at=CURRENT_TIMESTAMP WHERE source='audit_log'",
                     (last_audit,))
    conn.execute("""INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address)
                 VALUES(?,'notification.configuration_updated','notification_channel','telegram',?,'')""",
                 (user_id, json.dumps({"enabled": enabled, "chat_configured": bool(chat_id),
                                      "token_configured": bool(encrypted), "cooldown_minutes": cooldown_minutes},
                                     separators=(",", ":"))))
    # Mantém compatibilidade com o Chat ID da v1.0 sem duplicar o token por destino.
    if chat_id:
        destination = conn.execute("SELECT id FROM telegram_destinations WHERE chat_id=? AND deleted_at IS NULL ORDER BY id LIMIT 1", (chat_id,)).fetchone()
        if destination:
            conn.execute("""UPDATE telegram_destinations SET is_active=?,use_for_alerts=1,use_for_daily_summary=?,use_for_weekly_summary=?,
                use_for_executive_summary=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (int(enabled), int(daily_enabled), int(weekly_enabled), int(executive_enabled), destination["id"]))
        else:
            conn.execute("""INSERT INTO telegram_destinations(uuid,name,chat_id,destination_type,is_active,use_for_alerts,
                use_for_daily_summary,use_for_weekly_summary,use_for_executive_summary) VALUES(?,? ,?,'private',?,1,?,?,?)""",
                (str(uuid.uuid4()), "Destino principal", chat_id, int(enabled), int(daily_enabled), int(weekly_enabled), int(executive_enabled)))
        conn.execute("""UPDATE telegram_summary_settings SET daily_enabled=?,daily_time=?,weekly_enabled=?,weekly_day=?,weekly_time=?,
            executive_enabled=?,executive_time=?,updated_at=CURRENT_TIMESTAMP WHERE id=1""",
            (int(daily_enabled), daily_time, int(weekly_enabled), weekly_day, weekly_time, int(executive_enabled), executive_time))
        for key, value in {
            "telegram_daily_summary_enabled": str(int(daily_enabled)), "telegram_daily_summary_time": daily_time,
            "telegram_weekly_summary_enabled": str(int(weekly_enabled)), "telegram_weekly_summary_day": str(weekly_day),
            "telegram_weekly_summary_time": weekly_time, "telegram_executive_summary_enabled": str(int(executive_enabled)),
            "telegram_executive_summary_time": executive_time,
        }.items():
            conn.execute("INSERT INTO settings(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP", (key, value))


def _local_now(row, current: datetime) -> datetime:
    try: zone = ZoneInfo(row["timezone"] or "UTC")
    except ZoneInfoNotFoundError: zone = ZoneInfo("UTC")
    return current.astimezone(zone)


def in_maintenance(row, current: datetime) -> bool:
    if not row["maintenance_enabled"]:
        return False
    local = _local_now(row, current).time().replace(tzinfo=None)
    start, end = time.fromisoformat(row["maintenance_start"]), time.fromisoformat(row["maintenance_end"])
    if start == end:
        return True
    return start <= local < end if start < end else local >= start or local < end


def enqueue(conn, *, event_type: str, severity: str, subject: str, message: str, dedup_key: str,
            source_entity: str = "", source_entity_id: str = "", source_audit_id: int | None = None,
            now: datetime | None = None, bypass_maintenance: bool = False,
            idempotent: bool = False) -> str:
    row = channel(conn)
    queue_uuid = str(uuid.uuid4())
    current = now or utcnow()
    status = "pending"
    if not row["is_enabled"]:
        status = "suppressed"
    elif in_maintenance(row, current) and not bypass_maintenance:
        status = "suppressed"
    selected = conn.execute("""SELECT id,default_thread_id FROM telegram_destinations WHERE is_active=1 AND deleted_at IS NULL
        AND use_for_alerts=1 ORDER BY id LIMIT 1""").fetchone()
    selected_thread = selected["default_thread_id"] if selected else None
    if selected:
        mapping = conn.execute("""SELECT thread_id FROM telegram_topic_mappings WHERE destination_id=? AND is_active=1
            AND ((mapping_type='event' AND match_value=?) OR (mapping_type='category' AND match_value=?))
            ORDER BY CASE mapping_type WHEN 'event' THEN 0 ELSE 1 END,id DESC LIMIT 1""",
            (selected["id"], event_type, "failures" if severity == "critical" else "alerts")).fetchone()
        if mapping: selected_thread = mapping["thread_id"]
    destination_id = selected["id"] if selected else None
    if idempotent:
        existing = conn.execute("""SELECT uuid FROM notification_queue
            WHERE dedup_key=? AND destination_id IS ? AND thread_id IS ? ORDER BY id LIMIT 1""",
            (dedup_key[:240], destination_id, selected_thread)).fetchone()
        if existing:
            return existing["uuid"]
    try:
        cursor = conn.execute("""INSERT INTO notification_queue(uuid,channel_id,event_type,severity,subject,message,
                     dedup_key,status,source_entity,source_entity_id,source_audit_id,next_attempt_at,destination_id,thread_id)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (queue_uuid, row["id"], event_type, severity, subject[:160], message[:3500], dedup_key[:240],
                      status, source_entity[:80], source_entity_id[:160], source_audit_id, sql_time(current),
                      destination_id, selected_thread))
    except sqlite3.IntegrityError:
        if source_audit_id is not None:
            existing = conn.execute("SELECT uuid FROM notification_queue WHERE source_audit_id=? AND channel_id=?",
                                    (source_audit_id, row["id"])).fetchone()
            if existing:
                return existing["uuid"]
        raise
    if status == "suppressed":
        conn.execute("""INSERT INTO notification_history(queue_id,channel_type,event_type,status,attempt,error_code)
                     VALUES(?,'telegram',?,'suppressed',0,?)""",
                     (cursor.lastrowid, event_type, "disabled" if not row["is_enabled"] else "maintenance_window"))
    return queue_uuid


def record_condition(conn, *, key: str, active: bool, event_type: str, severity: str, subject: str,
                     message: str, recovery_message: str, recovery_subject: str | None = None,
                     now: datetime | None = None) -> bool:
    current = now or utcnow()
    state = conn.execute("SELECT * FROM notification_states WHERE dedup_key=?", (key,)).fetchone()
    row = channel(conn)
    if not state:
        conn.execute("INSERT INTO notification_states(dedup_key,is_active,last_seen_at) VALUES(?,?,?)",
                     (key, int(active), sql_time(current)))
        state = conn.execute("SELECT * FROM notification_states WHERE dedup_key=?", (key,)).fetchone()
        should_send = active
    elif active:
        last_sent = parse_sql(state["last_sent_at"])
        should_send = not state["is_active"] or not last_sent or current - last_sent >= timedelta(minutes=row["cooldown_minutes"])
        conn.execute("UPDATE notification_states SET is_active=1,last_seen_at=?,updated_at=CURRENT_TIMESTAMP WHERE dedup_key=?",
                     (sql_time(current), key))
    else:
        should_send = bool(state["is_active"])
        conn.execute("""UPDATE notification_states SET is_active=0,recovered_at=?,updated_at=CURRENT_TIMESTAMP
                     WHERE dedup_key=?""", (sql_time(current), key))
    if not should_send:
        return False
    queue_uuid = enqueue(conn, event_type=event_type if active else f"{event_type}_recovery",
            severity=severity if active else "recovery",
            subject=subject if active else (recovery_subject or f"Recuperação: {subject}"),
            message=message if active else recovery_message, dedup_key=key, now=current)
    queued = conn.execute("SELECT status FROM notification_queue WHERE uuid=?", (queue_uuid,)).fetchone()
    if queued and queued["status"] == "pending":
        conn.execute("UPDATE notification_states SET last_sent_at=? WHERE dedup_key=?", (sql_time(current), key))
    return True


def _audit_details(row) -> dict:
    try:
        value = json.loads(row["details"] or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _display_time(channel_row, value: str | None) -> str:
    parsed = parse_sql(value)
    return _local_now(channel_row, parsed).strftime("%d/%m/%Y %H:%M") if parsed else "Horário indisponível"


def _display_size(value: object) -> str:
    try: size = max(0, int(value or 0))
    except (TypeError, ValueError): size = 0
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(size)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}".replace(".0 ", " ")
        amount /= 1024
    return "0 B"


def _backup_file_label(filename: object) -> str:
    return "Backup de configuração"


def _ftp_notification(conn, row, details: dict, channel_row) -> tuple[str, str, str]:
    backup = None
    backup_uuid = str(details.get("backup_uuid") or "").strip()
    if backup_uuid:
        backup = conn.execute("""SELECT b.id,b.original_filename,b.equipment_id,e.hostname,g.name AS group_name
            FROM backups b JOIN equipment e ON e.id=b.equipment_id
            LEFT JOIN equipment_groups g ON g.id=e.group_id WHERE b.uuid=?""", (backup_uuid,)).fetchone()
    if not backup and details.get("backup_id"):
        backup = conn.execute("""SELECT b.id,b.original_filename,b.equipment_id,e.hostname,g.name AS group_name
            FROM backups b JOIN equipment e ON e.id=b.equipment_id
            LEFT JOIN equipment_groups g ON g.id=e.group_id WHERE b.id=?""", (details["backup_id"],)).fetchone()
    filename = str(details.get("filename") or (backup["original_filename"] if backup else "Arquivo não informado"))
    timestamp = _display_time(channel_row, row["created_at"])
    if not backup:
        key = backup_uuid or filename or str(row["id"])
        return ("Backup recebido sem identificação", f"📄 Arquivo: {filename}\n🕒 Horário: {timestamp}", f"ftp:{key}")
    group = f"\n🏷️ Grupo: {backup['group_name']}" if backup["group_name"] else ""
    key = backup_uuid or str(backup["id"])
    return ("Backup recebido por FTP", f"🖥️ Equipamento: {backup['hostname']}{group}\n📄 Arquivo: {_backup_file_label(filename)}\n🕒 Horário: {timestamp}", f"ftp:{key}")


def _ssh_notification(conn, row, details: dict, channel_row) -> tuple[str, str, str]:
    backup_uuid = str(details.get("backup_uuid") or row["entity_id"] or "").strip()
    backup = equipment = None
    try:
        if backup_uuid:
            backup = conn.execute("""SELECT b.id,b.original_filename,b.equipment_id,e.ssh_backup_driver,
                    COALESCE(NULLIF(e.name,''),e.hostname) AS equipment_name
                FROM backups b JOIN equipment e ON e.id=b.equipment_id WHERE b.uuid=?""",
                (backup_uuid,)).fetchone()
        if not backup and details.get("backup_id"):
            backup = conn.execute("""SELECT b.id,b.original_filename,b.equipment_id,e.ssh_backup_driver,
                    COALESCE(NULLIF(e.name,''),e.hostname) AS equipment_name
                FROM backups b JOIN equipment e ON e.id=b.equipment_id WHERE b.id=?""",
                (details["backup_id"],)).fetchone()
        if not backup and details.get("equipment_id"):
            equipment = conn.execute("""SELECT id,ssh_backup_driver,COALESCE(NULLIF(name,''),hostname) AS equipment_name
                FROM equipment WHERE id=?""", (details["equipment_id"],)).fetchone()
    except sqlite3.OperationalError:
        # Bancos mínimos de diagnóstico podem conter apenas a fila e a auditoria.
        backup = equipment = None
    if not backup and equipment:
        timestamp = _display_time(channel_row, row["created_at"])
        title = "Backup concluído" if equipment["ssh_backup_driver"] == "vsol_olt_telnet_cli" else "Backup SSH concluído"
        return (title, f"🖥️ Equipamento: {equipment['equipment_name']}\n🕒 Horário: {timestamp}",
                f"ssh:{backup_uuid or row['id']}")
    timestamp = _display_time(channel_row, row["created_at"])
    if not backup:
        return "Backup SSH concluído", f"🕒 Horário: {timestamp}", f"ssh:{backup_uuid or row['id']}"
    if backup["ssh_backup_driver"] == "vsol_olt_telnet_cli":
        return ("Backup concluído",
                f"🖥️ {backup['equipment_name']}\n🕒 {timestamp}",
                f"ssh:{backup_uuid or backup['id']}")
    title = "Backup SSH concluído"
    return (title,
            f"🖥️ Equipamento: {backup['equipment_name']}\n📄 Arquivo: {_backup_file_label(backup['original_filename'])}\n🕒 Horário: {timestamp}",
            f"ssh:{backup_uuid or backup['id']}")


def _rclone_notification(conn, row, details: dict, channel_row) -> tuple[str, str, str]:
    item = conn.execute("""SELECT i.uuid,i.remote_object_id,i.bytes_uploaded,
            COALESCE(NULLIF(e.name,''),e.hostname) equipment_name,t.name target_name
        FROM cloud_sync_items i JOIN backups b ON b.id=i.backup_id
        JOIN equipment e ON e.id=b.equipment_id JOIN cloud_targets t ON t.id=i.target_id
        WHERE i.uuid=?""", (row["entity_id"],)).fetchone()
    timestamp = _display_time(channel_row, row["created_at"])
    if not item:
        return ("Backup enviado ao Google Drive", f"✅ Backup enviado via rclone\n🕒 Horário: {timestamp}",
                f"rclone:{row['entity_id'] or row['id']}")
    remote_path = str(item["remote_object_id"] or "")
    folder = remote_path.rsplit("/", 1)[0] if "/" in remote_path else remote_path
    message = (f"🖥️ Equipamento: {item['equipment_name']}\n"
               f"📄 Arquivo: Backup de configuração\n"
               f"☁️ Destino: {item['target_name']}\n"
               f"📁 Pasta: {folder or 'Pasta configurada'}\n"
               f"💾 Tamanho: {_display_size(item['bytes_uploaded'] or details.get('size'))}\n"
               f"🕒 Horário: {timestamp}")
    return "Backup enviado ao Google Drive", message, f"rclone:{item['uuid']}"


def _cleanup_notification(row, details: dict, channel_row, *, trash: bool) -> tuple[str, str, str] | None:
    removed = int(details.get("removed", 0) if trash else details.get("purged", details.get("moved", 0)) or 0)
    released = details.get("bytes", 0) if trash else details.get("purged_bytes", details.get("bytes", 0))
    timestamp = _display_time(channel_row, row["created_at"])
    if removed <= 0:
        return None
    title = "Limpeza da lixeira concluída" if trash else "Limpeza de backups expirados concluída"
    noun = "Itens" if trash else "Arquivos"
    return (title, f"📦 {noun} removidos: {removed}\n💾 Espaço liberado: {_display_size(released)}\n🕒 Horário: {timestamp}",
            f"cleanup:{'trash' if trash else 'retention'}:{row['id']}")


def _operational_notification(conn, row, definition, channel_row) -> tuple[str, str, str] | None:
    event_type, _severity, default_subject = definition
    details = _audit_details(row)
    if row["action"] == "backup.created_from_ftp":
        return _ftp_notification(conn, row, details, channel_row)
    if row["action"] == "backup.created_from_ssh":
        return _ssh_notification(conn, row, details, channel_row)
    if row["action"] == "cloud.rclone_upload_success":
        return _rclone_notification(conn, row, details, channel_row)
    if row["action"] == "lifecycle.trash_emptied":
        return _cleanup_notification(row, details, channel_row, trash=True)
    if row["action"] == "lifecycle.executed":
        return _cleanup_notification(row, details, channel_row, trash=False)
    timestamp = _display_time(channel_row, row["created_at"])
    return default_subject, f"🕒 Horário: {timestamp}", f"audit:{row['id']}"


def scan_audit_events(conn, now: datetime | None = None) -> tuple[int, int]:
    cursor = conn.execute("SELECT last_id FROM notification_cursors WHERE source='audit_log'").fetchone()[0]
    rows = conn.execute("SELECT * FROM audit_log WHERE id>? ORDER BY id LIMIT 500", (cursor,)).fetchall()
    notification_channel = channel(conn)
    queued = 0
    for row in rows:
        definition = AUDIT_EVENTS.get(row["action"])
        if definition:
            event_type, severity, subject = definition
            rendered = _operational_notification(conn, row, definition, notification_channel)
            if rendered:
                subject, message, stable_key = rendered
                before = conn.total_changes
                enqueue(conn, event_type=event_type, severity=severity, subject=subject, message=message,
                        dedup_key=f"operational:{event_type}:{stable_key}", source_entity=row["entity"],
                        source_entity_id=row["entity_id"], source_audit_id=row["id"], now=now, idempotent=True)
                queued += int(conn.total_changes > before)
    if rows:
        conn.execute("UPDATE notification_cursors SET last_id=?,updated_at=CURRENT_TIMESTAMP WHERE source='audit_log'", (rows[-1]["id"],))
    return len(rows), queued


def format_elapsed_hours(age_hours: float | None) -> str:
    if age_hours is None:
        return "tempo indisponível"
    seconds = max(0, round(age_hours * 3600))
    if seconds < 60:
        return f"{seconds} segundo{'s' if seconds != 1 else ''}"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minuto{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hora{'s' if hours != 1 else ''}"
    days, remaining_hours = divmod(hours, 24)
    description = f"{days} dia{'s' if days != 1 else ''}"
    if remaining_hours:
        description += f" e {remaining_hours} hora{'s' if remaining_hours != 1 else ''}"
    return description


def evaluate_conditions(conn, now: datetime | None = None) -> int:
    current = now or utcnow()
    snapshot = dashboard_snapshot(conn, current)
    queued = 0
    for item in snapshot["equipment"]:
        missing = item["last_backup"] is None
        late = item["age_hours"] is not None and item["age_hours"] > 24
        age_description = format_elapsed_hours(item["age_hours"])
        queued += record_condition(conn, key=f"equipment:{item['id']}:missing", active=missing,
            event_type="equipment_without_backup", severity="critical", subject="Equipamento sem backup",
            message=f"🖥️ Equipamento: {item['hostname']}\n🕒 Última verificação: {_local_now(channel(conn), current).strftime('%d/%m/%Y %H:%M')}",
            recovery_message=f"{item['hostname']} recebeu seu primeiro backup.", now=current)
        queued += record_condition(conn, key=f"equipment:{item['id']}:late", active=late and not missing,
            event_type="backup_late", severity="warning" if (item["age_hours"] or 0) <= 72 else "critical",
            subject="Backup atrasado", message=f"🖥️ Equipamento: {item['hostname']}\n🕒 Último backup: {_display_time(channel(conn), item['last_backup'])}",
            recovery_subject="Backup normalizado",
            recovery_message=f"🖥️ Equipamento: {item['hostname']}\n✅ Voltou a receber backups dentro do prazo\n🕒 Horário: {_local_now(channel(conn), current).strftime('%d/%m/%Y %H:%M')}",
            now=current)
    service_events = {"worker": "worker_offline", "scheduler": "scheduler_offline",
                      "ftp-importer": "ftp_offline", "pure-ftpd": "ftp_offline"}
    for item in snapshot["services"]:
        if item["key"] not in service_events: continue
        active = item["level"] == "red"
        queued += record_condition(conn, key=f"service:{item['key']}", active=active,
            event_type=service_events[item["key"]], severity="critical", subject=f"{item['label']} offline",
            message=f"{item['label']}: {item['summary']}.", recovery_message=f"{item['label']} recuperado.", now=current)
    percent = snapshot["storage"]["percent"]
    for threshold in (80, 90, 95):
        queued += record_condition(conn, key=f"storage:{threshold}", active=percent >= threshold,
            event_type=f"storage_{threshold}", severity="critical" if threshold == 95 else "warning",
            subject=f"Storage em {threshold}%", message=f"Uso atual do filesystem: {percent}%.",
            recovery_message=f"Uso do filesystem voltou abaixo de {threshold}% ({percent}%).", now=current)
    return queued


def _summary_message(conn, period: str, current: datetime) -> str:
    days = 1 if period == "daily" else 7
    values = {
        "backups": conn.execute("SELECT COUNT(*) FROM backups WHERE received_at>=datetime(?,'-' || ? || ' days')", (sql_time(current), days)).fetchone()[0],
        "failed": conn.execute("SELECT COUNT(*) FROM backup_job_runs WHERE status IN ('failed','timeout') AND created_at>=datetime(?,'-' || ? || ' days')", (sql_time(current), days)).fetchone()[0],
        "rejected": conn.execute("SELECT COUNT(*) FROM ftp_received_files WHERE status='rejected' AND created_at>=datetime(?,'-' || ? || ' days')", (sql_time(current), days)).fetchone()[0],
        "trash": conn.execute("SELECT COUNT(*) FROM backups WHERE backup_status='trashed'").fetchone()[0],
    }
    label = "diário" if period == "daily" else "semanal"
    return (f"Resumo operacional {label}\nBackups recebidos: {values['backups']}\nFalhas: {values['failed']}\n"
            f"Rejeitados FTP: {values['rejected']}\nNa lixeira: {values['trash']}\nGerado: {sql_time(current)} UTC")


def schedule_summaries(conn, now: datetime | None = None) -> int:
    current = now or utcnow()
    row = channel(conn); local = _local_now(row, current); queued = 0
    definitions = (("daily", row["daily_summary_enabled"], None, row["daily_summary_time"]),
                   ("weekly", row["weekly_summary_enabled"], row["weekly_summary_day"], row["weekly_summary_time"]))
    for period, enabled, weekday, scheduled in definitions:
        if not enabled or (weekday is not None and local.weekday() != weekday): continue
        target = time.fromisoformat(scheduled)
        if local.time().replace(tzinfo=None) < target: continue
        key = f"summary:{period}:{local.date().isoformat()}"
        if conn.execute("SELECT 1 FROM notification_queue WHERE dedup_key=?", (key,)).fetchone(): continue
        enqueue(conn, event_type=f"{period}_summary", severity="info", subject=f"Resumo {period}",
                message=_summary_message(conn, period, current), dedup_key=key, now=current)
        queued += 1
    return queued


def _notification_destination(conn, *, event_type: str = "", severity: str = "", equipment_id: int | None = None,
                              method: str = "", category: str = "alerts"):
    """Resolve the generic Telegram destination and its topic without conflating equipment groups and chats."""
    destination = conn.execute("""SELECT * FROM telegram_destinations
        WHERE is_active=1 AND deleted_at IS NULL AND use_for_alerts=1 ORDER BY id LIMIT 1""").fetchone()
    if not destination:
        return None, None
    thread_id = destination["default_thread_id"]
    if equipment_id is not None:
        from .telegram_backup import resolve_thread
        thread_id = resolve_thread(conn, destination["id"], equipment_id, method, category) or thread_id
    mapping = conn.execute("""SELECT thread_id FROM telegram_topic_mappings
        WHERE destination_id=? AND is_active=1 AND
          ((mapping_type='event' AND match_value=?) OR (mapping_type='category' AND match_value=?))
        ORDER BY CASE mapping_type WHEN 'event' THEN 0 ELSE 1 END,id DESC LIMIT 1""",
        (destination["id"], event_type, category)).fetchone()
    if mapping:
        thread_id = mapping["thread_id"]
    return destination, int(thread_id) if thread_id is not None else None


def queue_basic_test(conn, user_id=None, now: datetime | None = None) -> str:
    row = channel(conn)
    if not row["token_encrypted"] or not row["chat_id"]:
        raise ValueError("Configure token e Chat ID antes do teste.")
    if not row["is_enabled"]:
        raise ValueError("Habilite o canal antes do teste.")
    current = now or utcnow()
    try: local = current.astimezone(ZoneInfo(row["timezone"] or "UTC"))
    except ZoneInfoNotFoundError: local = current
    diagnostic = telegram_diagnostic(conn, row)
    destination, thread_id = _notification_destination(conn, event_type="telegram.basic_test", category="alerts")
    if not destination:
        raise ValueError("Nenhum destino Telegram ativo está configurado para alertas.")
    destination_name = diagnostic.get("telegram_diag_chat", "Telegram configurado")
    test_message = ("✅ <b>Integração Telegram funcionando</b>\n\n"
                    "Backup Manager Local conseguiu enviar esta mensagem.\n\n"
                    f"Destino: {tg_escape(destination_name)}\nData: {local:%d/%m/%Y às %H:%M}")
    queue_uuid = str(uuid.uuid4())
    conn.execute("""INSERT INTO notification_queue(uuid,channel_id,event_type,severity,subject,message,dedup_key,status,
                 source_entity,source_entity_id,next_attempt_at,destination_id,thread_id)
                 VALUES(?,?,'telegram.basic_test','info','Teste Telegram',?,
                 ?,'pending','notification_channel','telegram',?,?,?)""",
                 (queue_uuid, row["id"], test_message, f"basic-test:{uuid.uuid4()}", sql_time(current), destination["id"], thread_id))
    conn.execute("""INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address)
                 VALUES(?,'telegram.basic_test','notification_channel','telegram','{}','')""", (user_id,))
    return queue_uuid


queue_test = queue_basic_test


def check_health(conn) -> dict[str, object]:
    row = conn.execute("SELECT * FROM notification_channels WHERE channel_type='telegram'").fetchone()
    if not row:
        return {"enabled": 0, "missing_channel": 1, "overdue": 0, "failed": 0}
    overdue = conn.execute(
        """SELECT COUNT(*) FROM notification_queue WHERE status='pending'
                               AND next_attempt_at < datetime('now','-15 minutes')""",
    ).fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM notification_queue WHERE status='failed'").fetchone()[0]
    return {
        "enabled": int(row["is_enabled"]),
        "missing_channel": 0,
        "overdue": overdue,
        "failed": failed,
        "token_encrypted": int(bool(row["token_encrypted"])),
        "chat_id": int(bool(row["chat_id"])),
    }


def dispatch(conn, transport: Transport | None = None, now: datetime | None = None, limit: int = 5) -> tuple[int, int, int]:
    current = now or utcnow(); sender = transport or TelegramTransport(); sent = retried = failed = 0
    conn.execute("""UPDATE notification_queue SET status='pending',next_attempt_at=?,updated_at=CURRENT_TIMESTAMP
        WHERE status='processing' AND updated_at<datetime(?,'-15 minutes')""",
        (sql_time(current), sql_time(current)))
    row = channel(conn)
    due = conn.execute("""SELECT q.*,COALESCE(d.chat_id,?) delivery_chat_id,d.default_thread_id destination_thread_id
                       FROM notification_queue q LEFT JOIN telegram_destinations d ON d.id=q.destination_id
                       WHERE q.status='pending' AND q.next_attempt_at<=? ORDER BY CASE q.severity WHEN 'critical' THEN 0 ELSE 1 END,q.id LIMIT ?""",
                       (row["chat_id"], sql_time(current), limit)).fetchall()
    if not row["is_enabled"] or not row["token_encrypted"] or not row["chat_id"]:
        return 0, 0, 0
    token = decrypt_secret(row["token_encrypted"])
    for item in due:
        attempt = item["attempts"] + 1
        conn.execute("UPDATE notification_queue SET status='processing',attempts=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (attempt, item["id"]))
        # Do not hold SQLite's write lock while waiting for the Telegram API.
        # A stale processing lease is recovered at the beginning of dispatch.
        conn.commit()
        try:
            try:
                if item["event_type"] == "telegram.basic_test":
                    message = item["message"]
                else:
                    operational_icons = {
                        "ftp_received_file": "⚠️" if "sem identificação" in item["subject"].lower() else "✅",
                        "backup_completed": "✅",
                        "cloud.upload_completed": "✅",
                        "equipment_without_backup": "⚠️", "backup_late": "⚠️",
                        "lifecycle_trash_cleanup": "🧹", "lifecycle_retention_cleanup": "🧹",
                    }
                    icon = operational_icons.get(item["event_type"], "🟢" if item["severity"] == "recovery" else "🔴" if item["severity"] == "critical" else "🟡")
                    message = f"{icon} <b>{tg_escape(item['subject'])}</b>\n\n{tg_escape(item['message'])}"
                thread_id = item["thread_id"] or item["destination_thread_id"]
                code, _ = sender.send(token, item["delivery_chat_id"], message, thread_id, "HTML")
            except TypeError:
                try: code, _ = sender.send(token, item["delivery_chat_id"], message, thread_id)
                except TypeError: code, _ = sender.send(token, item["delivery_chat_id"], message)
            conn.execute("UPDATE notification_queue SET status='sent',sent_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (sql_time(current), item["id"]))
            conn.execute("""INSERT INTO notification_history(queue_id,channel_type,event_type,status,attempt,response_code)
                         VALUES(?,'telegram',?,'sent',?,?)""", (item["id"], item["event_type"], attempt, code))
            sent += 1
        except TelegramAPIError as exc:
            final = attempt >= item["max_attempts"]
            delay = exc.retry_after or min(3600, 60 * (2 ** (attempt - 1)))
            conn.execute("""UPDATE notification_queue SET status=?,next_attempt_at=?,updated_at=CURRENT_TIMESTAMP
                         WHERE id=?""", ("failed" if final else "pending", sql_time(current + timedelta(seconds=delay)), item["id"]))
            conn.execute("""INSERT INTO notification_history(queue_id,channel_type,event_type,status,attempt,error_code,
                         response_code,error_description,retry_after,migrate_to_chat_id)
                         VALUES(?,'telegram',?,?,?,?,?,?,?,?)""",
                         (item["id"], item["event_type"], "failed" if final else "retry", attempt, exc.safe_code,
                          exc.http_status, exc.description, exc.retry_after, exc.migrate_to_chat_id))
            conn.execute("UPDATE notification_channels SET last_status=?,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         ("retry_wait" if not final else "failed", exc.friendly_message, row["id"]))
            failed += int(final); retried += int(not final)
        except Exception:
            final = attempt >= item["max_attempts"]
            delay = min(3600, 60 * (2 ** (attempt - 1)))
            conn.execute("UPDATE notification_queue SET status=?,next_attempt_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         ("failed" if final else "pending", sql_time(current + timedelta(seconds=delay)), item["id"]))
            conn.execute("""INSERT INTO notification_history(queue_id,channel_type,event_type,status,attempt,error_code,error_description)
                         VALUES(?,'telegram',?,?,?,'telegram_delivery_failed','Falha interna sanitizada')""",
                         (item["id"], item["event_type"], "failed" if final else "retry", attempt))
            failed += int(final); retried += int(not final)
    status = "sent" if sent else "failed" if failed else row["last_status"]
    if sent:
        conn.execute("UPDATE notification_channels SET last_status=?,last_error='',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (status, row["id"]))
    if sent or retried or failed:
        conn.execute("""INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address)
                     VALUES(NULL,'notification.dispatch_completed','notification_channel','telegram',?,'')""",
                     (json.dumps({"sent": sent, "retried": retried, "failed": failed}, separators=(",", ":")),))
    return sent, retried, failed


def run_cycle(conn, transport: Transport | None = None, now: datetime | None = None) -> CycleResult:
    current = now or utcnow()
    scanned, queued_audit = scan_audit_events(conn, current)
    queued_conditions = evaluate_conditions(conn, current)
    from .telegram_summaries import process_summary_runs, schedule_summary_runs
    queued_summaries = schedule_summary_runs(conn, current)
    sent, retried, failed = dispatch(conn, transport, current)
    summary_result = process_summary_runs(conn, transport or TelegramTransport(), current)
    sent += summary_result["sent"]; retried += summary_result["retry_wait"]; failed += summary_result["failed"]
    suppressed = conn.execute("SELECT COUNT(*) FROM notification_queue WHERE status='suppressed' AND created_at=?",
                              (sql_time(current),)).fetchone()[0]
    return CycleResult(scanned, queued_audit + queued_conditions + queued_summaries, sent, retried, failed, suppressed)
