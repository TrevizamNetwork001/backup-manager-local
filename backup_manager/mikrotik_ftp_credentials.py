from __future__ import annotations

import secrets

from .ftp import create_account, generate_password
from .security import encrypt_secret, hash_password


def create_isolated_credential(conn, *, equipment_id: int, equipment_name: str, user_id: int | None):
    password = generate_password(28)
    username = f"bm-mt-{equipment_id}-{secrets.token_hex(2)}"
    account = create_account(
        conn,
        equipment_id=equipment_id,
        name=f"MikroTik FTP Push - {equipment_name}"[:120],
        username=username,
        password=password,
        permission_mode="upload_only",
        created_by_user_id=user_id,
        notes="Conta exclusiva gerenciada pela integração MikroTik FTP Push.",
    )
    return account, password, encrypt_secret(password)


def rotate_credential(conn, integration_id: int) -> tuple[object, str, str]:
    row = conn.execute(
        """SELECT i.*, a.username FROM mikrotik_ftp_integrations i
           JOIN ftp_accounts a ON a.id=i.ftp_account_id WHERE i.id=?""",
        (integration_id,),
    ).fetchone()
    if not row:
        raise ValueError("Integração FTP Push não encontrada.")
    password = generate_password(28)
    encrypted = encrypt_secret(password)
    conn.execute(
        """UPDATE ftp_accounts SET password_hash_or_secret_reference=?,is_active=1,
           sync_status='pending',updated_at=CURRENT_TIMESTAMP WHERE id=?""",
        (hash_password(password), row["ftp_account_id"]),
    )
    conn.execute(
        """UPDATE mikrotik_ftp_integrations SET encrypted_ftp_secret=?,is_active=1,
           credential_rotated_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
        (encrypted, integration_id),
    )
    return row, password, encrypted
