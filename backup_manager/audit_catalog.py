from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class EventDefinition:
    title: str
    description: str
    category: str
    severity: str = "info"


EVENT_CATALOG = {
    "schedule.created": EventDefinition("Agendamento de backup criado", "Um novo agendamento foi configurado.", "Agendamentos"),
    "schedule.updated": EventDefinition("Agendamento de backup alterado", "A programação de backup foi modificada.", "Agendamentos"),
    "job.created": EventDefinition("Agendamento de backup criado", "Um novo agendamento foi configurado.", "Agendamentos"),
    "job.updated": EventDefinition("Agendamento de backup alterado", "A programação de backup foi modificada.", "Agendamentos"),
    "job.run_failed": EventDefinition("Falha ao executar o backup", "A execução terminou com falha.", "Execuções", "critical"),
    "job.run_success": EventDefinition("Backup executado com sucesso", "A execução criou ou validou o backup.", "Execuções"),
    "ftp.upload_imported": EventDefinition("Arquivo FTP importado", "O arquivo recebido por FTP foi validado e importado.", "FTP"),
    "ftp.upload_rejected": EventDefinition("Arquivo FTP rejeitado", "O arquivo recebido não passou pelas validações.", "FTP", "warning"),
    "cloud.sync_success": EventDefinition("Cópia externa concluída", "A sincronização externa foi concluída.", "Backup externo"),
    "cloud.rclone_upload_success": EventDefinition("Cópia enviada ao rclone", "O arquivo foi enviado ao rclone.", "Backup externo"),
    "telegram.destination_created": EventDefinition("Destino Telegram criado", "Um destino foi cadastrado sem duplicar o token do bot.", "Telegram"),
    "telegram.destination_updated": EventDefinition("Destino Telegram alterado", "As opções do destino foram modificadas.", "Telegram"),
    "telegram.backup_policy_created": EventDefinition("Política de cópia Telegram criada", "Uma política de envio de arquivos foi cadastrada.", "Telegram"),
    "telegram.backup_policy_updated": EventDefinition("Política de cópia Telegram alterada", "A política de envio foi modificada.", "Telegram"),
    "telegram.backup_queued": EventDefinition("Cópia Telegram enfileirada", "Um backup local validado entrou na fila Telegram.", "Telegram"),
    "telegram.backup_started": EventDefinition("Envio ao Telegram iniciado", "O worker iniciou o envio da cópia secundária.", "Telegram"),
    "telegram.backup_sent": EventDefinition("Cópia enviada ao Telegram", "O documento foi enviado e o backup local foi preservado.", "Telegram"),
    "telegram.backup_failed": EventDefinition("Falha ao enviar cópia ao Telegram", "A falha definitiva não alterou o backup local.", "Telegram", "critical"),
    "telegram.backup_skipped": EventDefinition("Cópia Telegram ignorada", "Uma limitação técnica ou regra impediu o anexo.", "Telegram", "warning"),
    "telegram.backup_cancelled": EventDefinition("Cópia Telegram cancelada", "O item de fila foi cancelado sem apagar o backup.", "Telegram", "warning"),
    "telegram.topic_mapping_updated": EventDefinition("Mapeamento de tópico atualizado", "Uma regra de roteamento por tópico foi salva.", "Telegram"),
}


def definition(action: str) -> EventDefinition:
    return EVENT_CATALOG.get(action, EventDefinition(action.replace("_", " ").replace(".", " — ").capitalize(), "Evento administrativo registrado.", "Sistema"))


def resolve_entity_name(conn, entity: str, entity_id: str) -> str:
    if not entity_id:
        return "-"
    queries = {
        "equipment": ("SELECT COALESCE(name,hostname) FROM equipment WHERE CAST(id AS TEXT)=?", 1),
        "backup": ("SELECT original_filename FROM backups WHERE CAST(id AS TEXT)=? OR uuid=?", 2),
        "backup_job": ("SELECT 'Agendamento ' || method || ' — ' || COALESCE(schedule_time,'') FROM backup_jobs WHERE CAST(id AS TEXT)=? OR uuid=?", 2),
        "telegram_backup": ("SELECT e.hostname || ' → ' || d.name FROM telegram_backup_items i JOIN equipment e ON e.id=i.equipment_id JOIN telegram_destinations d ON d.id=i.destination_id WHERE i.uuid=?", 1),
        "telegram_backup_policy": ("SELECT d.name FROM telegram_backup_policies p JOIN telegram_destinations d ON d.id=p.destination_id WHERE p.uuid=?", 1),
        "telegram_topic_mapping": ("SELECT d.name FROM telegram_topic_mappings m JOIN telegram_destinations d ON d.id=m.destination_id WHERE m.uuid=?", 1),
    }
    selected = queries.get(entity)
    if not selected:
        return entity.replace("_", " ").capitalize()
    sql, count = selected
    try:
        row = conn.execute(sql, (entity_id,) * count).fetchone()
        return str(row[0]) if row and row[0] else entity.replace("_", " ").capitalize()
    except Exception:
        return entity.replace("_", " ").capitalize()


def safe_technical_details(raw: str) -> str:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return "Detalhes indisponíveis."
    if not isinstance(data, dict):
        return "Detalhes indisponíveis."
    forbidden = ("token", "password", "secret", "authorization", "path", "credential")
    safe = {key: value for key, value in data.items() if not any(marker in key.lower() for marker in forbidden)}
    return json.dumps(safe, ensure_ascii=False, sort_keys=True)[:1500]
