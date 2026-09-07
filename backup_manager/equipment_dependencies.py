from __future__ import annotations

EQUIPMENT_DEPENDENCY_LABELS = {
    "backups": "backups registrados",
    "jobs": "agendamentos",
    "job_runs": "execuções de backup",
    "credentials": "credenciais SSH",
    "ftp_accounts": "contas FTP",
    "ftp_history": "arquivos recebidos por FTP",
    "ftp_push": "integrações FTP Push",
    "ftp_tests": "testes FTP Push",
    "ftp_uploads": "uploads FTP Push",
    "retention": "políticas de retenção",
    "cloud_policies": "políticas de sincronização externa",
    "cloud_items": "itens de sincronização externa",
    "telegram_policies": "políticas de envio Telegram",
    "telegram_items": "itens de envio Telegram",
    "telegram_topics": "mapeamentos Telegram",
}


def equipment_dependencies(conn, equipment_id: int) -> dict[str, object]:
    counts = {
        "backups": conn.execute("SELECT COUNT(*) FROM backups WHERE equipment_id=?", (equipment_id,)).fetchone()[0],
        "jobs": conn.execute("SELECT COUNT(*) FROM backup_jobs WHERE equipment_id=?", (equipment_id,)).fetchone()[0],
        "job_runs": conn.execute("SELECT COUNT(*) FROM backup_job_runs WHERE equipment_id=?", (equipment_id,)).fetchone()[0],
        "credentials": conn.execute("SELECT COUNT(*) FROM device_credentials WHERE equipment_id=?", (equipment_id,)).fetchone()[0],
        "ftp_accounts": conn.execute("SELECT COUNT(*) FROM ftp_accounts WHERE equipment_id=?", (equipment_id,)).fetchone()[0],
        "ftp_history": conn.execute("SELECT COUNT(*) FROM ftp_received_files WHERE equipment_id=?", (equipment_id,)).fetchone()[0],
        "ftp_push": conn.execute("SELECT COUNT(*) FROM mikrotik_ftp_integrations WHERE equipment_id=?", (equipment_id,)).fetchone()[0],
        "ftp_tests": conn.execute(
            """SELECT COUNT(*) FROM mikrotik_ftp_tests WHERE integration_id IN
            (SELECT id FROM mikrotik_ftp_integrations WHERE equipment_id=?)""",
            (equipment_id,),
        ).fetchone()[0],
        "ftp_uploads": conn.execute("SELECT COUNT(*) FROM mikrotik_ftp_uploads WHERE equipment_id=?", (equipment_id,)).fetchone()[0],
        "retention": conn.execute("SELECT COUNT(*) FROM retention_policies WHERE equipment_id=?", (equipment_id,)).fetchone()[0],
        "cloud_policies": conn.execute("SELECT COUNT(*) FROM cloud_sync_policies WHERE equipment_id=?", (equipment_id,)).fetchone()[0],
        "cloud_items": conn.execute("SELECT COUNT(*) FROM cloud_sync_items WHERE equipment_id=?", (equipment_id,)).fetchone()[0],
        "telegram_policies": conn.execute("SELECT COUNT(*) FROM telegram_backup_policies WHERE equipment_id=?", (equipment_id,)).fetchone()[0],
        "telegram_items": conn.execute("SELECT COUNT(*) FROM telegram_backup_items WHERE equipment_id=?", (equipment_id,)).fetchone()[0],
        "telegram_topics": conn.execute("SELECT COUNT(*) FROM telegram_topic_mappings WHERE equipment_id=?", (equipment_id,)).fetchone()[0],
    }
    accounts = conn.execute(
        """SELECT a.*,
        EXISTS(SELECT 1 FROM ftp_received_files r WHERE r.ftp_account_id=a.id) AS has_history,
        EXISTS(SELECT 1 FROM mikrotik_ftp_integrations i WHERE i.ftp_account_id=a.id) AS used_by_push
        FROM ftp_accounts a WHERE a.equipment_id=? ORDER BY a.id""",
        (equipment_id,),
    ).fetchall()
    integrations = conn.execute(
        """SELECT i.id,i.is_active,i.ftp_account_id,
        CASE WHEN a.id IS NULL OR a.deleted_at IS NOT NULL THEN 1 ELSE 0 END AS account_deleted
        FROM mikrotik_ftp_integrations i LEFT JOIN ftp_accounts a ON a.id=i.ftp_account_id
        WHERE i.equipment_id=? ORDER BY i.id""",
        (equipment_id,),
    ).fetchall()
    active_integrations = [row for row in integrations if row["is_active"]]
    active_accounts = [row for row in accounts if row["is_active"] and row["deleted_at"] is None]
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "accounts": accounts,
        "integrations": integrations,
        "active_accounts": active_accounts,
        "active_integrations": active_integrations,
        "account": accounts[-1] if accounts else None,
        "integration": active_integrations[0] if active_integrations else (integrations[-1] if integrations else None),
    }

