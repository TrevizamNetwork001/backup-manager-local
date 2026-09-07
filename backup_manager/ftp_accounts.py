from __future__ import annotations

from typing import Any


def list_accounts(conn, *, search: str = "", lifecycle: str = "current", active: str = "") -> dict[str, Any]:
    where, params = [], []
    if lifecycle == "deleted":
        where.append("ftp_accounts.deleted_at IS NOT NULL")
    elif lifecycle != "all":
        where.append("ftp_accounts.deleted_at IS NULL")
    if search:
        where.append("(ftp_accounts.name LIKE ? OR ftp_accounts.username LIKE ? OR equipment.hostname LIKE ?)")
        params.extend([f"%{search}%"] * 3)
    if active in {"0", "1"}:
        where.append("ftp_accounts.is_active=?")
        params.append(active)
    rows = conn.execute(
        f"""SELECT ftp_accounts.*,equipment.hostname,environments.name environment,
            equipment_groups.name group_name,vendors.name vendor,pops.name pop
            FROM ftp_accounts LEFT JOIN equipment ON equipment.id=ftp_accounts.equipment_id
            LEFT JOIN environments ON environments.id=equipment.environment_id
            LEFT JOIN equipment_groups ON equipment_groups.id=equipment.group_id
            LEFT JOIN vendors ON vendors.id=equipment.vendor_id LEFT JOIN pops ON pops.id=equipment.pop_id
            WHERE {' AND '.join(where)} ORDER BY ftp_accounts.name""",
        params,
    ).fetchall()
    equipment = conn.execute("SELECT id,hostname FROM equipment WHERE is_active=1 ORDER BY hostname").fetchall()
    return {"rows": rows, "equipment": equipment}


def account_detail(conn, account_id: int) -> dict[str, Any]:
    row = conn.execute(
        """SELECT ftp_accounts.*,equipment.hostname FROM ftp_accounts
           LEFT JOIN equipment ON equipment.id=ftp_accounts.equipment_id
           WHERE ftp_accounts.id=?""",
        (account_id,),
    ).fetchone()
    uploads = conn.execute(
        """SELECT ftp_received_files.*,backups.uuid backup_uuid,backups.backup_status
            FROM ftp_received_files LEFT JOIN backups ON backups.id=ftp_received_files.backup_id
            WHERE ftp_received_files.ftp_account_id=? ORDER BY ftp_received_files.id DESC LIMIT 100""",
        (account_id,),
    ).fetchall()
    server_row = conn.execute("SELECT value FROM settings WHERE key='ftp_public_ip'").fetchone()
    server = server_row[0] if server_row and server_row[0] else ""
    if not server:
        server_row = conn.execute("SELECT value FROM settings WHERE key='ftp_public_ipv6'").fetchone()
        server = server_row[0] if server_row and server_row[0] else ""
    return {
        "row": row,
        "uploads": uploads,
        "server": server or "IP do servidor",
    }
