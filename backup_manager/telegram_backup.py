from __future__ import annotations

import gzip
from html import escape
import http.client
import json
import mimetypes
import os
import sqlite3
import ssl
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .security import decrypt_secret
from .storage import load_config, resolve_inside, safe_filename, sha256_file
from .telegram_formatting import pt_size

COMPRESSED_SUFFIXES = {".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst"}
TRANSIENT_HTTP = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    code: str = ""
    message: str = "Backup elegível para cópia no Telegram."


def list_queue(conn, *, limit: int = 200):
    return conn.execute(
        """SELECT id,uuid,backup_id,destination_id,thread_id,status,attempt,max_attempts,
                  next_attempt_at,bytes_total,error_code
           FROM telegram_backup_items ORDER BY queued_at DESC LIMIT ?""", (limit,)
    ).fetchall()


def test_delivery_config(conn, destination_id: int = 0):
    destination = conn.execute(
        """SELECT * FROM telegram_destinations
           WHERE id=COALESCE(NULLIF(?,0),id) AND is_active=1 AND deleted_at IS NULL
           ORDER BY id LIMIT 1""", (destination_id,)
    ).fetchone()
    channel = conn.execute(
        """SELECT token_encrypted,is_enabled FROM notification_channels
           WHERE channel_type='telegram'"""
    ).fetchone()
    return destination, channel


class TelegramBackupError(RuntimeError):
    def __init__(self, code: str, message: str, *, transient: bool = False, retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.transient = transient
        self.retry_after = retry_after


def _setting(conn, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else default


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _audit(conn, action: str, *, item=None, user_id=None, details=None) -> None:
    safe = dict(details or {})
    if item is not None:
        safe.update({"item_uuid": item["uuid"], "backup_id": item["backup_id"],
                     "destination_id": item["destination_id"], "status": item["status"], "attempt": item["attempt"]})
    conn.execute("INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address) VALUES(?,?,?,?,?,'')",
                 (user_id, action, "telegram_backup", item["uuid"] if item is not None else "",
                  json.dumps(safe, ensure_ascii=True, separators=(",", ":"))))


def sanitize_chat_id(chat_id: str) -> str:
    value = str(chat_id)
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


def transport_limit(conn) -> int:
    mode = _setting(conn, "telegram_api_mode", "public")
    key = "telegram_local_max_file_bytes" if mode == "local" else "telegram_public_max_file_bytes"
    try:
        return max(1, int(_setting(conn, key, "52428800")))
    except ValueError:
        return 52428800


def telegram_api_base(conn) -> str:
    mode = _setting(conn, "telegram_api_mode", "public")
    if mode == "public":
        return "https://api.telegram.org"
    base = _setting(conn, "telegram_local_api_base_url", "").strip().rstrip("/")
    if not base.startswith("https://"):
        raise TelegramBackupError("TELEGRAM_LOCAL_API_INVALID", "Bot API local deve usar HTTPS validado.")
    return base


class TelegramDocumentTransport:
    """Transporte substituível em testes; nunca inclui token em exceções públicas."""

    def send_document(self, token: str, chat_id: str, thread_id: int | None, path: Path,
                      filename: str, caption: str, base_url: str) -> tuple[str, int]:
        boundary = f"----BackupManager{uuid.uuid4().hex}"
        fields = [("chat_id", chat_id), ("caption", caption), ("parse_mode", "HTML")]
        if thread_id:
            fields.append(("message_thread_id", str(thread_id)))
        chunks = []
        for name, value in fields:
            chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode())
        prefix = b"".join(chunks); suffix = f"\r\n--{boundary}--\r\n".encode(); length = len(prefix) + path.stat().st_size + len(suffix)
        endpoint = urlsplit(base_url)
        connection = http.client.HTTPSConnection(endpoint.hostname, endpoint.port or 443, timeout=120, context=ssl.create_default_context())
        try:
            connection.putrequest("POST", f"{endpoint.path}/bot{token}/sendDocument")
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(length)); connection.endheaders(); connection.send(prefix)
            with path.open("rb") as source:
                while block := source.read(1024 * 1024): connection.send(block)
            connection.send(suffix); response = connection.getresponse(); raw = response.read(65536)
            try: payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError): payload = {}
            if response.status == 200 and payload.get("ok"):
                return str(payload.get("result", {}).get("message_id", "")), path.stat().st_size
            retry_after = None
            try:
                retry_after = payload.get("parameters", {}).get("retry_after")
            except Exception:
                pass
            description = str(payload.get("description", "")).lower()
            if "message thread not found" in description:
                messages = {response.status: ("TELEGRAM_THREAD_NOT_FOUND", "O tópico informado não existe.")}
            elif "topic_closed" in description or "topic closed" in description:
                messages = {response.status: ("TELEGRAM_TOPIC_CLOSED", "O tópico informado está fechado.")}
            elif "not enough rights" in description or "have no rights" in description:
                messages = {response.status: ("TELEGRAM_FORBIDDEN", "O bot não possui permissão para enviar no tópico.")}
            else:
                messages = {400: ("TELEGRAM_BAD_REQUEST", "Destino, tópico ou documento inválido."),
                        401: ("TELEGRAM_UNAUTHORIZED", "Bot não autorizado."),
                        403: ("TELEGRAM_FORBIDDEN", "Bot sem permissão no destino."),
                        404: ("TELEGRAM_CHAT_NOT_FOUND", "Chat ou endpoint não encontrado."),
                        409: ("TELEGRAM_CONFLICT", "Conflito temporário na Bot API."),
                        429: ("TELEGRAM_RATE_LIMIT", "Limite de mensagens atingido.")}
            code, message = messages.get(response.status, ("TELEGRAM_HTTP_ERROR", "Falha HTTP ao enviar documento."))
            raise TelegramBackupError(code, message, transient=response.status in TRANSIENT_HTTP, retry_after=retry_after)
        except TelegramBackupError:
            raise
        except (http.client.HTTPException, TimeoutError, OSError, ValueError):
            raise TelegramBackupError("TELEGRAM_CONNECTION_FAILED", "Falha de rede ao enviar documento.", transient=True) from None
        finally:
            connection.close()


def destination(conn, destination_id: int):
    return conn.execute("SELECT * FROM telegram_destinations WHERE id=? AND deleted_at IS NULL", (destination_id,)).fetchone()


def create_destination(conn, *, name: str, chat_id: str, destination_type: str = "private",
                       default_thread_id: int | None = None, flags: dict[str, bool] | None = None,
                       notes: str = "", user_id=None) -> int:
    if destination_type not in {"private", "group", "supergroup", "channel"}:
        raise ValueError("Tipo de destino inválido.")
    value = str(chat_id).strip()
    if not value or len(value) > 24 or not value.lstrip("-").isdigit():
        raise ValueError("Chat ID inválido.")
    if default_thread_id is not None and int(default_thread_id) <= 0:
        raise ValueError("Tópico inválido.")
    from .telegram_destinations import validate_destination_name
    name = validate_destination_name(conn, name)
    enabled = flags or {}
    cursor = conn.execute("""INSERT INTO telegram_destinations(uuid,name,chat_id,destination_type,default_thread_id,is_active,
        use_for_alerts,use_for_daily_summary,use_for_weekly_summary,use_for_executive_summary,use_for_backup_files,notes)
        VALUES(?,?,?,?,?,1,?,?,?,?,?,?)""", (str(uuid.uuid4()), name.strip()[:120], value, destination_type, default_thread_id,
        int(enabled.get("alerts", False)), int(enabled.get("daily", False)), int(enabled.get("weekly", False)),
        int(enabled.get("executive", False)), int(enabled.get("backup_files", False)), notes.strip()[:500]))
    _audit(conn, "telegram.destination_created", user_id=user_id, details={"destination_id": cursor.lastrowid, "name": name})
    return int(cursor.lastrowid)


def resolve_thread(conn, destination_id: int, equipment_id: int | None = None, method: str = "", category: str = "backup_files") -> int | None:
    equipment = conn.execute("SELECT group_id FROM equipment WHERE id=?", (equipment_id,)).fetchone() if equipment_id is not None else None
    group_id = equipment[0] if equipment else None
    checks = []
    if equipment_id is not None:
        checks.extend((("equipment", equipment_id, None, ""), ("group", None, group_id, "")))
    if method:
        checks.append(("method", None, None, method))
    if category:
        checks.append(("category", None, None, category))
    for mapping_type, equipment_value, group_value, match in checks:
        row = conn.execute("""SELECT thread_id FROM telegram_topic_mappings WHERE destination_id=? AND mapping_type=?
            AND COALESCE(equipment_id,0)=COALESCE(?,0) AND COALESCE(group_id,0)=COALESCE(?,0) AND match_value=? AND is_active=1
            ORDER BY id DESC LIMIT 1""", (destination_id, mapping_type, equipment_value, group_value, match)).fetchone()
        if row:
            return int(row[0])
    row = destination(conn, destination_id)
    return int(row["default_thread_id"]) if row and row["default_thread_id"] else None


def applicable_policies(conn, backup_id: int, *, automatic=False):
    backup = conn.execute("SELECT equipment_id,source_method FROM backups WHERE id=?", (backup_id,)).fetchone()
    if not backup:
        return []
    equipment = conn.execute("SELECT group_id FROM equipment WHERE id=?", (backup["equipment_id"],)).fetchone()
    specific = conn.execute("""SELECT p.* FROM telegram_backup_policies p
        WHERE p.scope_type='equipment' AND p.equipment_id=? AND p.deleted_at IS NULL ORDER BY p.id DESC""",
        (backup["equipment_id"],)).fetchall()
    if specific:
        candidates = specific if any(row["enabled"] and row["active"] for row in specific) else []
    else:
        candidates = conn.execute("""SELECT p.* FROM telegram_backup_policies p
            WHERE p.deleted_at IS NULL AND p.active=1 AND p.enabled=1
            AND (p.scope_type='global' OR (p.scope_type='group' AND p.group_id=?))
            ORDER BY CASE p.scope_type WHEN 'group' THEN 0 ELSE 1 END,p.id""",
            (equipment[0] if equipment else None,)).fetchall()
    rows = []
    for policy in candidates:
        target = destination(conn, policy["destination_id"])
        if target and target["is_active"] and target["use_for_backup_files"]:
            rows.append(policy)
    chosen = {}
    for row in rows:
        if automatic and not row["send_new_backups"]:
            continue
        chosen.setdefault(row["destination_id"], row)
    return list(chosen.values())


def is_backup_eligible(conn, backup_id: int, destination_id: int, policy_id: int, *, check_duplicate=True) -> Eligibility:
    backup = conn.execute("SELECT * FROM backups WHERE id=?", (backup_id,)).fetchone()
    policy = conn.execute("SELECT * FROM telegram_backup_policies WHERE id=?", (policy_id,)).fetchone()
    target = destination(conn, destination_id)
    if not backup or backup["deleted_at"] or backup["backup_status"] == "deleted": return Eligibility(False, "BACKUP_DELETED", "Backup excluído.")
    if backup["backup_status"] == "trashed" or backup["trash_relative_path"]: return Eligibility(False, "BACKUP_TRASHED", "Backup está na lixeira.")
    if backup["backup_status"] == "quarantined": return Eligibility(False, "BACKUP_QUARANTINED", "Backup está em quarentena.")
    if backup["backup_status"] != "available": return Eligibility(False, "BACKUP_NOT_AVAILABLE", "Backup local indisponível.")
    if not target or not target["is_active"] or not target["use_for_backup_files"]: return Eligibility(False, "DESTINATION_DISABLED", "Destino Telegram inativo.")
    if not policy or policy["deleted_at"] or not policy["enabled"] or not policy["active"] or policy["destination_id"] != destination_id: return Eligibility(False, "POLICY_DISABLED", "Política Telegram inativa.")
    suffix = Path(backup["original_filename"]).suffix.lower().lstrip(".")
    selected = {value.strip().lower().lstrip(".") for value in (policy["file_types"] or "").split(",") if value.strip()}
    if not policy["all_artifacts"] and (not suffix or suffix not in selected):
        return Eligibility(False, "FILE_TYPE_NOT_SELECTED", "Tipo de arquivo não selecionado pela política.")
    if check_duplicate and conn.execute("SELECT 1 FROM telegram_backup_items WHERE backup_id=? AND destination_id=? AND status!='cancelled'", (backup_id, destination_id)).fetchone():
        return Eligibility(False, "DUPLICATE_TELEGRAM_ITEM", "Cópia já enfileirada para este destino.")
    try:
        path = resolve_inside(load_config(conn).backup_directory, backup["relative_path"])
    except (ValueError, OSError): return Eligibility(False, "BACKUP_FILE_MISSING", "Arquivo local ausente ou inválido.")
    try:
        if path.is_symlink() or not path.is_file(): return Eligibility(False, "BACKUP_FILE_MISSING", "Arquivo local ausente ou inválido.")
        if path.stat().st_size != backup["file_size"]: return Eligibility(False, "BACKUP_SIZE_MISMATCH", "Tamanho local não confere.")
        if not backup["sha256"] or sha256_file(path) != backup["sha256"]: return Eligibility(False, "BACKUP_HASH_MISMATCH", "SHA-256 local não confere.")
    except OSError:
        return Eligibility(False, "BACKUP_FILE_UNREADABLE", "Sem acesso de leitura ao arquivo local.")
    return Eligibility(True)


def enqueue_backup(conn, backup_id: int, *, destination_id=None, user_id=None, automatic=False) -> list[str]:
    if _setting(conn, "telegram_backup_enabled", "0") != "1":
        return []
    backup = conn.execute("SELECT * FROM backups WHERE id=?", (backup_id,)).fetchone(); created = []
    for policy in applicable_policies(conn, backup_id, automatic=automatic):
        if destination_id is not None and policy["destination_id"] != destination_id: continue
        item_uuid = str(uuid.uuid4()); thread = policy["thread_id"] or resolve_thread(conn, policy["destination_id"], backup["equipment_id"], backup["source_method"])
        check = is_backup_eligible(conn, backup_id, policy["destination_id"], policy["id"])
        if not check.eligible:
            if check.code == "FILE_TYPE_NOT_SELECTED":
                _audit(conn, "telegram_backup_skipped_policy", user_id=user_id,
                       details={"backup_id": backup_id, "destination_id": policy["destination_id"], "reason": check.code})
            continue
        identity = f"{backup_id}:{policy['destination_id']}:{thread or 0}:{backup['sha256']}"
        try:
            configured_limit = max(1, int(_setting(conn, "telegram_backup_max_file_bytes", str(transport_limit(conn)))))
        except ValueError:
            configured_limit = transport_limit(conn)
        status = "queued" if backup["file_size"] <= min(configured_limit, transport_limit(conn)) else "skipped"
        error_code = "" if status == "queued" else "TELEGRAM_FILE_TOO_LARGE"
        error_message = "" if status == "queued" else "Backup concluído e armazenado, mas não enviado ao Telegram porque excede o limite configurado."
        try:
            conn.execute("""INSERT INTO telegram_backup_items(uuid,backup_id,equipment_id,destination_id,policy_id,thread_id,status,
                max_attempts,bytes_total,local_sha256,idempotency_key,error_code,error_message,finished_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,CASE WHEN ?='skipped' THEN CURRENT_TIMESTAMP ELSE NULL END)""",
                (item_uuid, backup_id, backup["equipment_id"], policy["destination_id"], policy["id"], thread, status,
                 policy["retry_count"], backup["file_size"], backup["sha256"], identity, error_code, error_message, status))
        except sqlite3.IntegrityError:
            continue
        _audit(conn, "telegram_backup_enqueued" if status == "queued" else "telegram_backup_skipped_size",
               user_id=user_id, details={"item_uuid": item_uuid, "backup_id": backup_id, "destination_id": policy["destination_id"]})
        created.append(item_uuid)
    return created


def enqueue_new_backup(conn, backup_id: int) -> list[str]:
    return enqueue_backup(conn, backup_id, automatic=True)


def _item(conn, item_id: int):
    return conn.execute("""SELECT i.*,b.relative_path,b.original_filename,b.source_method,b.file_size,b.sha256,
        e.hostname,g.name group_name,d.chat_id,d.name destination_name,d.is_active destination_active,d.deleted_at destination_deleted,
        p.enabled policy_enabled,p.active policy_active,p.deleted_at policy_deleted,p.compression_mode,p.compression_level,p.retry_base_seconds
        FROM telegram_backup_items i JOIN backups b ON b.id=i.backup_id JOIN equipment e ON e.id=i.equipment_id
        LEFT JOIN equipment_groups g ON g.id=e.group_id JOIN telegram_destinations d ON d.id=i.destination_id
        JOIN telegram_backup_policies p ON p.id=i.policy_id WHERE i.id=?""", (item_id,)).fetchone()


def _prepared_file(conn, item, source: Path) -> tuple[Path, str, bool]:
    mode = item["compression_mode"]
    if mode == "none" or source.suffix.lower() in COMPRESSED_SUFFIXES:
        return source, safe_filename(item["original_filename"]), False
    config = load_config(conn); Path(config.temporary_directory).mkdir(parents=True, exist_ok=True)
    base = Path(safe_filename(item["original_filename"])).stem[:120] or "backup"
    if mode == "zip":
        fd, raw = tempfile.mkstemp(prefix="telegram-", suffix=".zip", dir=config.temporary_directory); os.close(fd); target = Path(raw)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=item["compression_level"]) as archive:
            archive.write(source, arcname=safe_filename(item["original_filename"]))
        return target, f"{base}.zip", True
    fd, raw = tempfile.mkstemp(prefix="telegram-", suffix=".gz", dir=config.temporary_directory); os.close(fd); target = Path(raw)
    with source.open("rb") as incoming, gzip.open(target, "wb", compresslevel=item["compression_level"]) as outgoing:
        while block := incoming.read(1024 * 1024): outgoing.write(block)
    return target, f"{base}.gz", True


def _caption(item, filename: str, size: int) -> str:
    return render_telegram_backup_caption(item, filename, size)


def _field(item, key):
    try: value = item[key]
    except (KeyError, IndexError, TypeError): return ""
    return "" if value is None else str(value).strip()


def render_telegram_backup_caption(item, filename: str, size: int, *, now=None, timezone_name="America/Sao_Paulo") -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("America/Sao_Paulo")
    current = current.astimezone(zone)
    x = lambda value: escape(str(value), quote=False)
    lines = ["✅ Backup concluído"]
    if group := _field(item, "group_name"):
        lines.append(f"🏷️ Grupo: {x(group)}")
    lines.extend((
        f"🖥️ Equipamento: {x(_field(item, 'hostname') or 'Equipamento')}",
        f"📄 Arquivo: {x(filename)}",
        f"🕒 Enviado em: {current:%d/%m/%Y %H:%M}",
    ))
    return "\n".join(lines)


def render_telegram_backup_failure(item, filename, size, reason="Falha temporária de comunicação com o Telegram."):
    x=lambda v: escape(str(v), quote=False)
    return f"⚠️ <b>Cópia não enviada ao Telegram</b>\n\n🖥️ <b>Equipamento:</b> {x(_field(item,'hostname'))}\n📁 <b>Arquivo:</b> {x(filename)}\n📦 <b>Tamanho:</b> {pt_size(size)}\n\n❌ <b>Motivo:</b>\n{x(reason)}\n\n✅ O backup local permanece salvo e validado.\n🔄 Uma nova tentativa será realizada automaticamente."


def render_telegram_backup_too_large(item, filename, size):
    x=lambda v: escape(str(v), quote=False)
    return f"⚠️ <b>Backup não anexado</b>\n\n🖥️ <b>Equipamento:</b> {x(_field(item,'hostname'))}\n📁 <b>Arquivo:</b> {x(filename)}\n📦 <b>Tamanho:</b> {pt_size(size)}\n\n❌ <b>Motivo:</b>\nO arquivo excede o limite técnico do transporte Telegram configurado.\n\n✅ A cópia local permanece disponível."


def render_telegram_mikrotik_bundle_caption(item, artifacts):
    x=lambda v: escape(str(v), quote=False); names=[a if isinstance(a,str) else a.get('filename','') for a in artifacts]
    return "\n".join(["📦 <b>Backup recebido</b>","","🔵 <b>Roteador/Core</b>",x(_field(item,'hostname')),"","────────────","","📁 <b>Artefatos</b>",""]+[f"✅ <code>{x(n)}</code>" for n in names if n]+["","✅ <b>Validação:</b> Ambos os arquivos validados","","✅ <b>BACKUP VALIDADO</b>","","📎 Arquivos anexados abaixo"])


def _failure_alert(conn, item, reason: str, *, too_large: bool = False) -> None:
    try:
        from .notifications import enqueue
        subject = "Backup não anexado ao Telegram" if too_large else "Falha ao enviar cópia ao Telegram"
        message = (f"{subject}.\n\nEquipamento: {item['hostname']}\nBackup: {item['original_filename']}\n"
                   f"Motivo: {reason}\nA cópia local permanece disponível.")
        enqueue(conn, event_type="telegram_backup_skipped" if too_large else "telegram_backup_failed",
                severity="warning" if too_large else "critical", subject=subject, message=message,
                dedup_key=f"telegram-backup-alert:{item['uuid']}:{'large' if too_large else 'failed'}",
                source_entity="telegram_backup", source_entity_id=item["uuid"])
    except Exception:
        # O alerta é melhor esforço e nunca interfere na preservação/estado do backup.
        return


def process_item(conn, item_id: int, *, transport=None, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc); item = _item(conn, item_id)
    if not item or item["status"] not in {"queued", "retry_wait"}: return "skipped"
    check = is_backup_eligible(conn, item["backup_id"], item["destination_id"], item["policy_id"], check_duplicate=False)
    if not check.eligible:
        conn.execute("UPDATE telegram_backup_items SET status='skipped',error_code=?,error_message=?,finished_at=?,safe_log='Validação recusou o envio; backup local preservado.',updated_at=CURRENT_TIMESTAMP WHERE id=?", (check.code, check.message, _stamp(current), item_id))
        _audit(conn, "telegram.backup_skipped", item=_item(conn, item_id)); return "skipped"
    attempt = item["attempt"] + 1; started = time.monotonic(); temporary = False; prepared = None
    conn.execute("UPDATE telegram_backup_items SET status='validating',attempt=?,started_at=?,last_attempt_at=?,error_code='',error_message='',updated_at=CURRENT_TIMESTAMP WHERE id=?", (attempt, _stamp(current), _stamp(current), item_id))
    try:
        config = load_config(conn); source = resolve_inside(config.backup_directory, item["relative_path"])
        conn.execute("UPDATE telegram_backup_items SET status='preparing',updated_at=CURRENT_TIMESTAMP WHERE id=?", (item_id,))
        prepared, filename, temporary = _prepared_file(conn, item, source); final_size = prepared.stat().st_size
        if final_size > transport_limit(conn):
            conn.execute("""UPDATE telegram_backup_items SET status='skipped',bytes_total=?,finished_at=?,duration_ms=?,error_code='TELEGRAM_FILE_TOO_LARGE',
                error_message='Arquivo excede o limite técnico do transporte Telegram.',safe_log='Arquivo não enviado; backup local preservado.',updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (final_size, _stamp(current), int((time.monotonic()-started)*1000), item_id))
            _audit(conn, "telegram.backup_skipped", item=_item(conn, item_id), details={"error_code": "TELEGRAM_FILE_TOO_LARGE", "size": final_size})
            _failure_alert(conn, _item(conn, item_id), "o arquivo excede o limite técnico do transporte Telegram configurado", too_large=True)
            return "skipped"
        channel = conn.execute("SELECT token_encrypted,is_enabled FROM notification_channels WHERE channel_type='telegram'").fetchone()
        if not channel or not channel["is_enabled"] or not channel["token_encrypted"]:
            raise TelegramBackupError("TELEGRAM_NOT_CONFIGURED", "Bot Telegram não configurado.")
        sender = transport or TelegramDocumentTransport(); token = decrypt_secret(channel["token_encrypted"])
        conn.execute("UPDATE telegram_backup_items SET status='uploading',bytes_total=?,destination_snapshot=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (final_size, sanitize_chat_id(item["chat_id"]), item_id)); _audit(conn, "telegram.backup_started", item=_item(conn, item_id))
        # Uploads can approach the systemd ten-minute timeout. Persist the
        # lease and release SQLite before the network transfer.
        conn.commit()
        message_id, bytes_sent = sender.send_document(token, item["chat_id"], item["thread_id"], prepared, filename, render_telegram_backup_caption(item, filename, final_size, now=now, timezone_name=_setting(conn, "timezone", "America/Sao_Paulo")), telegram_api_base(conn))
        conn.execute("""UPDATE telegram_backup_items SET status='sent',telegram_message_id=?,bytes_sent=?,sent_at=?,finished_at=?,duration_ms=?,
            safe_log='Documento enviado ao Telegram; backup local preservado.',updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (str(message_id)[:80], min(int(bytes_sent), final_size), _stamp(current), _stamp(current), int((time.monotonic()-started)*1000), item_id))
        _audit(conn, "telegram.backup_sent", item=_item(conn, item_id)); return "sent"
    except TelegramBackupError as exc:
        final = not exc.transient or attempt >= item["max_attempts"]; status = "failed" if final else "retry_wait"
        delay = exc.retry_after if exc.retry_after is not None else item["retry_base_seconds"] * (2 ** (attempt - 1)); delay = max(1, min(int(delay), 86400))
        conn.execute("""UPDATE telegram_backup_items SET status=?,next_attempt_at=?,finished_at=?,duration_ms=?,error_code=?,error_message=?,
            safe_log='Falha segura no envio; token omitido e backup local preservado.',updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (status, _stamp(current + timedelta(seconds=delay)), _stamp(current) if final else None,
             int((time.monotonic()-started)*1000), exc.code, exc.safe_message, item_id))
        _audit(conn, "telegram.backup_failed" if final else "telegram.backup_retry_scheduled", item=_item(conn, item_id), details={"error_code": exc.code})
        if final: _failure_alert(conn, _item(conn, item_id), exc.safe_message)
        return status
    except Exception:
        exc = TelegramBackupError("TELEGRAM_INTERNAL_ERROR", "Falha interna segura ao preparar o envio.", transient=True)
        final = attempt >= item["max_attempts"]; status = "failed" if final else "retry_wait"
        conn.execute("UPDATE telegram_backup_items SET status=?,next_attempt_at=?,error_code=?,error_message=?,safe_log='Falha interna sanitizada; backup local preservado.',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (status, _stamp(current + timedelta(seconds=item["retry_base_seconds"])), exc.code, exc.safe_message, item_id))
        _audit(conn, "telegram.backup_failed" if final else "telegram.backup_retry_scheduled", item=_item(conn, item_id), details={"error_code": exc.code})
        if final: _failure_alert(conn, _item(conn, item_id), exc.safe_message)
        return status
    finally:
        if temporary and prepared is not None:
            prepared.unlink(missing_ok=True)


def run_once(conn, *, transport=None, now: datetime | None = None, limit: int = 5) -> dict[str, int]:
    current = now or datetime.now(timezone.utc); result = {"processed": 0, "sent": 0, "retry_wait": 0, "failed": 0, "skipped": 0}
    conn.execute("""UPDATE telegram_backup_items SET status='retry_wait',next_attempt_at=?,
        error_code='TELEGRAM_WORKER_INTERRUPTED',error_message='Envio anterior interrompido; nova tentativa agendada.',
        safe_log='Worker interrompido; backup local preservado.',updated_at=CURRENT_TIMESTAMP
        WHERE status IN ('validating','preparing','uploading') AND updated_at<datetime(?,'-11 minutes')""",
        (_stamp(current), _stamp(current)))
    due = conn.execute("SELECT id FROM telegram_backup_items WHERE status='queued' OR (status='retry_wait' AND next_attempt_at<=?) ORDER BY queued_at,id LIMIT ?", (_stamp(current), limit)).fetchall()
    for row in due:
        status = process_item(conn, row["id"], transport=transport, now=current); result[status] = result.get(status, 0) + 1; result["processed"] += 1
    conn.execute("INSERT INTO settings(key,value,updated_at) VALUES('telegram_backup_last_worker_at',?,CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP", (_stamp(current),))
    return result


def retry_item(conn, item_id: int, user_id=None) -> bool:
    row = _item(conn, item_id)
    if not row or row["status"] not in {"failed", "skipped"}: return False
    conn.execute("UPDATE telegram_backup_items SET status='queued',next_attempt_at=CURRENT_TIMESTAMP,error_code='',error_message='',finished_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (item_id,))
    _audit(conn, "telegram.backup_queued", item=_item(conn, item_id), user_id=user_id); return True


def cancel_item(conn, item_id: int, user_id=None) -> bool:
    row = _item(conn, item_id)
    if not row or row["status"] not in {"queued", "retry_wait"}: return False
    conn.execute("UPDATE telegram_backup_items SET status='cancelled',finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?", (item_id,))
    _audit(conn, "telegram.backup_cancelled", item=_item(conn, item_id), user_id=user_id); return True


def stats(conn) -> dict[str, int | str]:
    result = {"queued": 0, "validating": 0, "preparing": 0, "uploading": 0, "sent": 0, "retry_wait": 0, "failed": 0, "skipped": 0, "cancelled": 0}
    result.update({row["status"]: int(row["total"]) for row in conn.execute("SELECT status,COUNT(*) total FROM telegram_backup_items GROUP BY status")})
    result["bytes_sent"] = int(conn.execute("SELECT COALESCE(SUM(bytes_sent),0) FROM telegram_backup_items WHERE status='sent'").fetchone()[0])
    last = conn.execute("SELECT sent_at FROM telegram_backup_items WHERE status='sent' ORDER BY sent_at DESC LIMIT 1").fetchone()
    result["last_file_at"] = last[0] if last else ""
    return result


def create_test_file(conn) -> Path:
    directory = Path(load_config(conn).temporary_directory); directory.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix="telegram-test-", suffix=".txt", dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("Teste de envio de arquivo do Backup Manager Local.\n")
    return Path(raw)


def queue_test_file(conn, destination_id: int, user_id=None) -> str:
    target = destination(conn, destination_id)
    if not target or not target["is_active"]:
        raise ValueError("Destino Telegram inválido ou inativo.")
    item_uuid = str(uuid.uuid4())
    conn.execute("INSERT INTO telegram_test_items(uuid,destination_id,test_type,created_by_user_id) VALUES(?,?,'file',?)",
                 (item_uuid, destination_id, user_id))
    _audit(conn, "telegram.destination_tested", user_id=user_id,
           details={"destination_id": destination_id, "test": "file", "status": "queued"})
    return item_uuid


def process_test_files(conn, *, transport=None, limit: int = 2) -> dict[str, int]:
    result = {"test_sent": 0, "test_failed": 0}
    rows = conn.execute("""SELECT t.*,d.chat_id,d.default_thread_id FROM telegram_test_items t
        JOIN telegram_destinations d ON d.id=t.destination_id WHERE t.status='queued' AND d.is_active=1
        AND d.deleted_at IS NULL ORDER BY t.id LIMIT ?""", (limit,)).fetchall()
    if not rows:
        return result
    channel = conn.execute("SELECT token_encrypted,is_enabled FROM notification_channels WHERE channel_type='telegram'").fetchone()
    for row in rows:
        path = None
        try:
            if not channel or not channel["is_enabled"] or not channel["token_encrypted"]:
                raise TelegramBackupError("TELEGRAM_NOT_CONFIGURED", "Bot Telegram não configurado.")
            path = create_test_file(conn); sender = transport or TelegramDocumentTransport()
            message_id, _ = sender.send_document(decrypt_secret(channel["token_encrypted"]), row["chat_id"], row["default_thread_id"],
                path, "arquivo-teste-backup-manager.txt", render_telegram_backup_caption({"hostname":"Equipamento de teste","source_method":"teste"}, "arquivo-teste-backup-manager.txt", path.stat().st_size, timezone_name=_setting(conn, "timezone", "America/Sao_Paulo")), telegram_api_base(conn))
            conn.execute("UPDATE telegram_test_items SET status='sent',message_id=?,finished_at=CURRENT_TIMESTAMP WHERE id=?", (str(message_id)[:80], row["id"]))
            result["test_sent"] += 1
        except Exception:
            conn.execute("UPDATE telegram_test_items SET status='failed',error_code='TELEGRAM_TEST_FAILED',finished_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
            result["test_failed"] += 1
        finally:
            if path is not None: path.unlink(missing_ok=True)
    return result
