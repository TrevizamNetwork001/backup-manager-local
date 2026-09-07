from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .security import decrypt_secret
from .storage import load_config
from .telegram_formatting import SEPARATOR, pt_number, pt_size, tg_escape
from .version import __version__

MAX_TELEGRAM_TEXT = 3900

ADMIN_EVENT_CATALOG = {
    "user.created": "usuário criado",
    "user.updated": "usuário alterado",
    "equipment.created": "equipamento cadastrado",
    "created": "equipamento cadastrado",
    "equipment.updated": "equipamento alterado",
    "schedule.created": "agendamento criado",
    "schedule.updated": "agendamento alterado",
    "job.created": "agendamento criado",
    "job.updated": "agendamento alterado",
    "credential.updated": "credencial alterada",
    "credential.created": "credencial cadastrada",
    "ftp.account_created": "conta FTP criada",
    "lifecycle.policy_updated": "política de retenção alterada",
    "notification.settings_updated": "notificações alteradas",
    "notification.configuration_updated": "notificações alteradas",
    "https.updated": "HTTPS alterado",
    "https.enable": "HTTPS habilitado",
    "https.disable": "HTTPS desabilitado",
    "update.success": "atualização aplicada",
    "update.rollback_success": "restauração de versão aplicada",
}


@dataclass(frozen=True)
class SummaryPeriod:
    kind: str
    start_utc: datetime
    end_utc: datetime
    label: str


def _setting(conn, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else default


def installation_timezone(conn) -> ZoneInfo:
    name = _setting(conn, "timezone", "America/Sao_Paulo")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def summary_period(conn, kind: str, now: datetime | None = None) -> SummaryPeriod:
    current = (now or datetime.now(timezone.utc)).astimezone(installation_timezone(conn))
    if kind in {"daily", "executive"}:
        target = current.date() - timedelta(days=1)
        start = datetime.combine(target, time.min, current.tzinfo)
        end = datetime.combine(target, time.max, current.tzinfo)
        label = target.strftime("%d/%m/%Y")
    elif kind == "weekly":
        end_date = current.date() - timedelta(days=1)
        start_date = end_date - timedelta(days=6)
        start = datetime.combine(start_date, time.min, current.tzinfo)
        end = datetime.combine(end_date, time.max, current.tzinfo)
        label = f"{start_date.strftime('%d/%m/%Y')} a {end_date.strftime('%d/%m/%Y')}"
    else:
        raise ValueError("Tipo de resumo inválido.")
    return SummaryPeriod(kind, start.astimezone(timezone.utc), end.astimezone(timezone.utc), label)


def _sql_stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _count(conn, sql: str, params=()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _range(period: SummaryPeriod) -> tuple[str, str]:
    return _sql_stamp(period.start_utc), _sql_stamp(period.end_utc)


def relevant_admin_activity(conn, period: SummaryPeriod) -> dict[str, int]:
    start, end = _range(period)
    placeholders = ",".join("?" for _ in ADMIN_EVENT_CATALOG)
    rows = conn.execute(
        f"SELECT action,COUNT(*) total FROM audit_log WHERE created_at BETWEEN ? AND ? "
        f"AND action IN ({placeholders}) GROUP BY action ORDER BY action",
        (start, end, *ADMIN_EVENT_CATALOG),
    ).fetchall()
    return {row["action"]: int(row["total"]) for row in rows}


def _external_counts(conn, table: str, success: str, period: SummaryPeriod) -> tuple[int, int, int]:
    start, end = _range(period)
    try:
        completed_column = "sent_at" if table == "telegram_backup_items" else "finished_at"
        sent = _count(conn, f"SELECT COUNT(*) FROM {table} WHERE status=? AND COALESCE({completed_column},updated_at) BETWEEN ? AND ?", (success, start, end))
        failed = _count(conn, f"SELECT COUNT(*) FROM {table} WHERE status='failed' AND updated_at BETWEEN ? AND ?", (start, end))
        pending = _count(conn, f"SELECT COUNT(*) FROM {table} WHERE status IN ('queued','validating','preparing','uploading','retry_wait')")
        return sent, failed, pending
    except sqlite3.OperationalError:
        return 0, 0, 0


def collect_summary_data(conn, period: SummaryPeriod) -> dict[str, object]:
    start, end = _range(period)
    backups = _count(conn, "SELECT COUNT(*) FROM backups WHERE backup_status='available' AND received_at BETWEEN ? AND ?", (start, end))
    failed = _count(conn, "SELECT COUNT(*) FROM backup_job_runs WHERE status IN ('failed','timeout') AND created_at BETWEEN ? AND ?", (start, end))
    pending = _count(conn, "SELECT COUNT(*) FROM backup_job_runs WHERE status IN ('queued','running')")
    cancelled = _count(conn, "SELECT COUNT(*) FROM backup_job_runs WHERE status='cancelled' AND created_at BETWEEN ? AND ?", (start, end))
    by_method = {row["source_method"]: int(row["total"]) for row in conn.execute(
        "SELECT source_method,COUNT(*) total FROM backups WHERE backup_status='available' AND received_at BETWEEN ? AND ? GROUP BY source_method", (start, end)).fetchall()}
    equipment_total = _count(conn, "SELECT COUNT(*) FROM equipment WHERE is_active=1")
    equipment_with = _count(conn, "SELECT COUNT(DISTINCT equipment_id) FROM backups WHERE backup_status='available' AND received_at BETWEEN ? AND ?", (start, end))
    ftp = {row["status"]: int(row["total"]) for row in conn.execute(
        "SELECT status,COUNT(*) total FROM ftp_received_files WHERE created_at BETWEEN ? AND ? GROUP BY status", (start, end)).fetchall()}
    cloud_sent, cloud_failed, cloud_pending = _external_counts(conn, "cloud_sync_items", "synced", period)
    telegram_sent, telegram_failed, telegram_pending = _external_counts(conn, "telegram_backup_items", "sent", period)
    telegram_skipped = _count(conn, "SELECT COUNT(*) FROM telegram_backup_items WHERE status='skipped' AND updated_at BETWEEN ? AND ?", (start, end))
    storage = conn.execute("SELECT COALESCE(SUM(CASE WHEN backup_status='available' THEN file_size ELSE 0 END),0),COALESCE(SUM(CASE WHEN backup_status='trashed' THEN file_size ELSE 0 END),0) FROM backups").fetchone()
    disk = shutil.disk_usage(load_config(conn).storage_root)
    inactive = _count(conn, "SELECT COUNT(*) FROM equipment WHERE is_active=0")
    restored = _count(conn, "SELECT COUNT(*) FROM audit_log WHERE action='lifecycle.backup_restored' AND created_at BETWEEN ? AND ?", (start, end))
    cleanups = _count(conn, "SELECT COUNT(*) FROM audit_log WHERE action='lifecycle.executed' AND created_at BETWEEN ? AND ?", (start, end))
    recurrent = conn.execute("""SELECT e.hostname,COUNT(*) total FROM backup_job_runs r JOIN equipment e ON e.id=r.equipment_id
        WHERE r.status IN ('failed','timeout') AND r.created_at BETWEEN ? AND ? GROUP BY e.id HAVING COUNT(*)>1 ORDER BY total DESC,e.hostname LIMIT 5""", (start, end)).fetchall()
    return {
        "backups": backups, "failed": failed, "pending": pending, "cancelled": cancelled,
        "by_method": by_method, "equipment_total": equipment_total, "equipment_with": equipment_with,
        "equipment_without": max(0, equipment_total - equipment_with), "ftp": ftp,
        "cloud": (cloud_sent, cloud_failed, cloud_pending),
        "telegram": (telegram_sent, telegram_failed, telegram_pending, telegram_skipped),
        "backup_bytes": int(storage[0]), "trash_bytes": int(storage[1]),
        "period_bytes": _count(conn, "SELECT COALESCE(SUM(file_size),0) FROM backups WHERE backup_status='available' AND received_at BETWEEN ? AND ?", (start, end)),
        "disk_percent": round(100 * disk.used / disk.total, 1) if disk.total else 0.0, "disk_free": disk.free,
        "equipment_inactive": inactive, "restored": restored, "cleanups": cleanups,
        "recurrent": [(row["hostname"], int(row["total"])) for row in recurrent],
        "admin": relevant_admin_activity(conn, period),
    }


def _size(value: int) -> str:
    return pt_size(value)


def _state(data: dict[str, object]) -> tuple[str, str, str]:
    failures = int(data["failed"]) + int(data["cloud"][1]) + int(data["telegram"][1])
    storage = float(data["disk_percent"]); without = int(data["equipment_without"])
    if failures >= 2 or storage >= 90:
        details = []
        if failures: details.append(f"{failures} falhas relevantes")
        if storage >= 90: details.append(f"armazenamento em {pt_number(storage)}%")
        return "critical", "🔴 Sistema em estado crítico", " e ".join(details).capitalize() + "."
    if failures or without or int(data["ftp"].get("rejected", 0)) or data["cloud"][2] or data["telegram"][2] or storage >= 80:
        if without: phrase = f"{without} equipamento{'s estão' if without != 1 else ' está'} sem backup no período."
        elif failures: phrase = f"{failures} falha{'s requerem' if failures != 1 else ' requer'} atenção."
        elif storage >= 80: phrase = f"O armazenamento atingiu {pt_number(storage)}%."
        else: phrase = "Existem itens pendentes ou ocorrências que requerem atenção."
        return "warning", "🟡 Sistema requer atenção", phrase
    return "healthy", "🟢 Sistema normal", "Todos os indicadores do período estão normais."


def _admin_lines(activity: dict[str, int]) -> list[str]:
    return [f"• {count} × {tg_escape(ADMIN_EVENT_CATALOG[action])}" for action, count in activity.items()]


def _footer(conn, now: datetime | None = None) -> list[str]:
    local = (now or datetime.now(timezone.utc)).astimezone(installation_timezone(conn))
    return [SEPARATOR, f"Backup Manager Local {__version__}", f"Gerado em {local:%d/%m/%Y} às {local:%H:%M}"]


def render_daily_telegram_summary(conn, period: SummaryPeriod) -> str:
    data = collect_summary_data(conn, period); methods = data["by_method"]; ftp = data["ftp"]
    cloud = data["cloud"]; telegram = data["telegram"]
    _, state_title, state_phrase = _state(data)
    lines = ["📦 <b>Backup Manager Local</b>", f"📅 <b>Resumo diário — {period.label}</b>", "", f"<b>{state_title}</b>", state_phrase, ""]
    quick = []
    if data["backups"]: quick.append(f"✅ {data['backups']} backups concluídos")
    if data["failed"]: quick.append(f"❌ {data['failed']} {'falha' if data['failed'] == 1 else 'falhas'}")
    if data["equipment_without"]: quick.append(f"⚠️ {data['equipment_without']} {'equipamento' if data['equipment_without'] == 1 else 'equipamentos'} sem backup")
    if cloud[0]: quick.append(f"☁️ {cloud[0]} cópias externas enviadas")
    lines += quick or ["Nenhum backup foi executado no período."]
    lines += ["", SEPARATOR, "", "📡 <b>Equipamentos</b>", "", f"• Monitorados: {data['equipment_total']}",
              f"• Com backup no período: {data['equipment_with']}", f"• Sem backup no período: {data['equipment_without']}"]
    if data["equipment_inactive"]: lines.append(f"• Arquivados: {data['equipment_inactive']}")
    lines += ["", SEPARATOR, "", "💾 <b>Backups</b>", ""]
    if not any((data["backups"], data["failed"], data["pending"], data["cancelled"])):
        lines.append("Nenhum backup foi executado no período.")
    else:
        lines += [f"✅ Concluídos: {data['backups']}", f"❌ Falharam: {data['failed']}", f"⏳ Pendentes: {data['pending']}"]
        if data["cancelled"]: lines.append(f"🚫 Cancelados: {data['cancelled']}")
        method_lines = [f"• {label}: {methods.get(key, 0)}" for key, label in (("ssh", "SSH"), ("ftp", "FTP"), ("manual", "Manual")) if methods.get(key, 0)]
        if method_lines: lines += ["", "<b>Por método</b>", *method_lines]
    if ftp:
        lines += ["", SEPARATOR, "", "📥 <b>Recebimento por FTP</b>", "", f"• Detectados: {sum(ftp.values())}",
                  f"✅ Importados: {ftp.get('imported',0)}", f"⚠️ Rejeitados: {ftp.get('rejected',0)}", f"❌ Falhos: {ftp.get('failed',0)}"]
    if any(cloud) or any(telegram):
        lines += ["", SEPARATOR, "", "☁️ <b>Sincronização externa</b>", ""]
        if any(cloud): lines += ["<b>rclone</b>", f"✅ Enviados: {cloud[0]}", f"❌ Falhas: {cloud[1]}", f"⏳ Pendentes: {cloud[2]}", ""]
        if any(telegram): lines += ["<b>Telegram</b>", f"✅ Arquivos enviados: {telegram[0]}", f"❌ Falhas: {telegram[1]}", f"⏳ Pendentes: {telegram[2]}", f"⚠️ Ignorados: {telegram[3]}"]
    lines += ["", SEPARATOR, "", "🗄️ <b>Armazenamento</b>", "", f"• Utilizado: {pt_number(float(data['disk_percent']))}%",
              f"• Livre: {_size(data['disk_free'])}", f"• Backups: {_size(data['backup_bytes'])}", f"• Lixeira: {_size(data['trash_bytes'])}"]
    if data["recurrent"]:
        lines += ["", SEPARATOR, "", "⚠️ <b>Ocorrências importantes</b>", ""] + [f"• {tg_escape(name)}: {count} falhas" for name, count in data["recurrent"]]
    if data["admin"]:
        lines += ["", SEPARATOR, "", "👤 <b>Atividade administrativa</b>", ""] + _admin_lines(data["admin"])
    return "\n".join(lines + ["", *_footer(conn)])


def render_weekly_telegram_summary(conn, period: SummaryPeriod) -> str:
    data = collect_summary_data(conn, period); total = int(data["backups"]) + int(data["failed"])
    rate = (100 * int(data["backups"]) / total) if total else 100.0
    _, title, phrase = _state(data); cloud=data["cloud"]; telegram=data["telegram"]; ftp=data["ftp"]
    lines = ["📦 <b>Backup Manager Local</b>", "📊 <b>Resumo semanal</b>", tg_escape(period.label), "", f"<b>{title.replace('Sistema', 'Semana')}</b>", phrase, "",
             f"✅ Backups concluídos: {data['backups']}", f"❌ Falhas: {data['failed']}", f"📈 Taxa de sucesso: {pt_number(rate)}%", f"⚠️ Equipamentos sem backup: {data['equipment_without']}",
             "", SEPARATOR, "", "📥 <b>FTP</b>", "", f"• Importados: {ftp.get('imported',0)}", f"• Rejeitados: {ftp.get('rejected',0)}"]
    if any(cloud) or any(telegram):
        lines += ["", SEPARATOR, "", "☁️ <b>Cópias externas</b>", "", f"• rclone: {cloud[0]}", f"• Telegram: {telegram[0]}"]
    lines += ["", SEPARATOR, "", "🗄️ <b>Armazenamento</b>", "", f"• Crescimento na semana: {_size(data['period_bytes'])}", f"• Utilização atual: {pt_number(float(data['disk_percent']))}%", "", *_footer(conn)]
    return "\n".join(lines)


def render_executive_telegram_summary(conn, period: SummaryPeriod) -> str:
    activity = relevant_admin_activity(conn, period)
    if not activity:
        return ""
    return "\n".join(["📦 <b>Backup Manager Local</b>", f"👤 <b>Resumo executivo — {period.label}</b>", "", *_admin_lines(activity), "", "Nenhuma alteração operacional crítica.", "", *_footer(conn)])


def split_telegram_message(message: str, limit: int = MAX_TELEGRAM_TEXT) -> list[str]:
    if len(message) <= limit:
        return [message]
    heading = message.split("\n\n", 1)[0]
    payload_limit = max(80, limit - len(heading) - 40)
    chunks: list[str] = []
    current = ""
    for block in message.split("\n\n"):
        candidates = [block] if len(block) <= payload_limit else [block[i:i + payload_limit] for i in range(0, len(block), payload_limit)]
        for candidate in candidates:
            joined = candidate if not current else f"{current}\n\n{candidate}"
            if len(joined) > payload_limit and current:
                chunks.append(current); current = candidate
            else:
                current = joined
    if current:
        chunks.append(current)
    total = len(chunks)
    return [f"{heading}\nParte {index} de {total}\n\n{chunk}" if not chunk.startswith(heading) else f"Parte {index} de {total}\n\n{chunk}"
            for index, chunk in enumerate(chunks, 1)]


def _enabled(conn, kind: str) -> tuple[bool, str, int | None]:
    row = conn.execute("SELECT * FROM telegram_summary_settings WHERE id=1").fetchone()
    if kind == "daily": return bool(row["daily_enabled"]), row["daily_time"], None
    if kind == "weekly": return bool(row["weekly_enabled"]), row["weekly_time"], int(row["weekly_day"])
    return bool(row["executive_enabled"]), row["executive_time"], None


def schedule_summary_runs(conn, now: datetime | None = None, force_type: str | None = None) -> int:
    current = now or datetime.now(timezone.utc); local = current.astimezone(installation_timezone(conn)); created = 0
    kinds = (force_type,) if force_type else ("daily", "weekly", "executive")
    for kind in kinds:
        enabled, at, weekday = _enabled(conn, kind)
        if not force_type and (not enabled or (weekday is not None and local.weekday() != weekday) or local.time().replace(tzinfo=None) < time.fromisoformat(at)):
            continue
        period = summary_period(conn, kind, current)
        if kind == "executive" and not relevant_admin_activity(conn, period):
            continue
        flag = f"use_for_{kind}_summary"
        destinations = conn.execute(f"SELECT * FROM telegram_destinations WHERE deleted_at IS NULL AND is_active=1 AND {flag}=1 ORDER BY id").fetchall()
        for destination in destinations:
            logical = f"{kind}:{period.start_utc.date()}:{period.end_utc.date()}:{destination['uuid']}"
            try:
                conn.execute("""INSERT INTO telegram_summary_runs(uuid,summary_type,destination_id,period_start,period_end,logical_key)
                    VALUES(?,?,?,?,?,?)""", (str(uuid.uuid4()), kind, destination["id"], _sql_stamp(period.start_utc), _sql_stamp(period.end_utc), logical))
                created += 1
            except sqlite3.IntegrityError:
                pass
    return created


def schedule_summary_test(conn, kind: str, now: datetime | None = None) -> int:
    if kind not in {"daily", "weekly", "executive"}:
        raise ValueError("Tipo de resumo inválido.")
    current = now or datetime.now(timezone.utc); period = summary_period(conn, kind, current); created = 0
    flag = f"use_for_{kind}_summary"
    destinations = conn.execute(f"SELECT * FROM telegram_destinations WHERE deleted_at IS NULL AND is_active=1 AND {flag}=1 ORDER BY id").fetchall()
    for destination in destinations:
        conn.execute("""INSERT INTO telegram_summary_runs(uuid,summary_type,destination_id,period_start,period_end,logical_key)
                     VALUES(?,?,?,?,?,?)""", (str(uuid.uuid4()), kind, destination["id"], _sql_stamp(period.start_utc),
                     _sql_stamp(period.end_utc), f"test:{kind}:{uuid.uuid4()}"))
        created += 1
    if not created:
        raise ValueError("Nenhum destino ativo está configurado para este resumo.")
    return created


def process_summary_runs(conn, transport, now: datetime | None = None, limit: int = 10) -> dict[str, int]:
    current = now or datetime.now(timezone.utc); result = {"sent": 0, "retry_wait": 0, "failed": 0}
    channel = conn.execute("SELECT * FROM notification_channels WHERE channel_type='telegram'").fetchone()
    if not channel or not channel["is_enabled"] or not channel["token_encrypted"]:
        return result
    token = decrypt_secret(channel["token_encrypted"])
    rows = conn.execute("""SELECT r.*,d.chat_id,COALESCE((SELECT m.thread_id FROM telegram_topic_mappings m
            WHERE m.destination_id=d.id AND m.is_active=1 AND m.mapping_type='category' AND m.match_value='reports'
            ORDER BY m.id DESC LIMIT 1),d.default_thread_id) default_thread_id FROM telegram_summary_runs r
        JOIN telegram_destinations d ON d.id=r.destination_id WHERE r.status IN ('queued','retry_wait') AND r.next_attempt_at<=?
        AND d.is_active=1 AND d.deleted_at IS NULL ORDER BY r.created_at LIMIT ?""", (_sql_stamp(current), limit)).fetchall()
    renderers = {"daily": render_daily_telegram_summary, "weekly": render_weekly_telegram_summary, "executive": render_executive_telegram_summary}
    for row in rows:
        period = SummaryPeriod(row["summary_type"], datetime.strptime(row["period_start"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc), datetime.strptime(row["period_end"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc), "")
        local_start = period.start_utc.astimezone(installation_timezone(conn)); local_end = period.end_utc.astimezone(installation_timezone(conn))
        label = local_start.strftime("%d/%m/%Y") if row["summary_type"] != "weekly" else f"{local_start:%d/%m/%Y} a {local_end:%d/%m/%Y}"
        period = SummaryPeriod(period.kind, period.start_utc, period.end_utc, label)
        parts = split_telegram_message(renderers[row["summary_type"]](conn, period)); attempt = row["attempts"] + 1
        try:
            for part in parts:
                try: transport.send(token, row["chat_id"], part, row["default_thread_id"], "HTML")
                except TypeError:
                    try: transport.send(token, row["chat_id"], part, row["default_thread_id"])
                    except TypeError: transport.send(token, row["chat_id"], part)
            conn.execute("UPDATE telegram_summary_runs SET status='sent',parts=?,attempts=?,sent_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (len(parts), attempt, _sql_stamp(current), row["id"]))
            result["sent"] += 1
        except Exception:
            final = attempt >= 5; status = "failed" if final else "retry_wait"; delay = min(3600, 60 * 2 ** (attempt - 1))
            conn.execute("UPDATE telegram_summary_runs SET status=?,attempts=?,error_code='TELEGRAM_DELIVERY_FAILED',next_attempt_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, attempt, _sql_stamp(current + timedelta(seconds=delay)), row["id"]))
            result[status] += 1
    return result


def summary_stats(conn) -> dict[str, object]:
    rows = conn.execute("SELECT status,COUNT(*) total FROM telegram_summary_runs GROUP BY status").fetchall()
    data = {"queued": 0, "sent": 0, "retry_wait": 0, "failed": 0, "skipped": 0}
    data.update({row["status"]: int(row["total"]) for row in rows})
    last = conn.execute("SELECT sent_at FROM telegram_summary_runs WHERE status='sent' ORDER BY sent_at DESC LIMIT 1").fetchone()
    data["last_summary_at"] = last[0] if last else ""
    return data
