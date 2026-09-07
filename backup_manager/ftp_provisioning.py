from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from .db import connect
from .ftp import create_account, run_helper, validate_password
from .security import encrypt_secret


Helper = Callable[[str, int, str | None], tuple[bool, str]]


@dataclass(frozen=True)
class AccountResult:
    found: bool
    ok: bool
    account_id: int = 0
    account_uuid: str = ""
    equipment_id: int | None = None
    username: str = ""
    error: str = ""


@dataclass(frozen=True)
class IntegrationDeactivationResult:
    found: bool
    ok: bool
    equipment_id: int = 0
    integration_uuid: str = ""
    routeros_major_version: int = 7
    orphaned_account: bool = False
    error: str = ""


class FTPProvisioningService:
    """Own account/PureDB transitions; interfaces only render its result."""

    def __init__(self, *, connect_factory=connect, helper: Helper = run_helper):
        self._connect = connect_factory
        self._helper = helper

    @staticmethod
    def _audit(conn, *, user_id: int | None, action: str, entity: str,
               entity_id: str, details: dict, ip_address: str) -> None:
        conn.execute(
            """INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address)
               VALUES(?,?,?,?,?,?)""",
            (user_id, action, entity, entity_id,
             json.dumps(details, separators=(",", ":"), ensure_ascii=False), ip_address),
        )

    def create_account(self, *, equipment_id: int | None, name: str, username: str, password: str,
                       account_type: str = "backup",
                       permission_mode: str, allowed_source: str, quota_bytes: int | None,
                       max_files: int | None, notes: str, user_id: int | None,
                       ip_address: str = "", upload_subdirectory: str = "") -> AccountResult:
        with self._connect() as conn:
            account = create_account(
                conn, equipment_id=equipment_id, account_type=account_type,
                name=name, username=username, password=password,
                permission_mode=permission_mode, allowed_source=allowed_source, quota_bytes=quota_bytes,
                max_files=max_files, is_active=True, notes=notes, created_by_user_id=user_id,
                upload_subdirectory=upload_subdirectory,
            )
            conn.execute(
                "UPDATE ftp_accounts SET password_hash_or_secret_reference=? WHERE id=?",
                ("fernet:" + encrypt_secret(password), account["id"]),
            )
            self._audit(
                conn, user_id=user_id, action="ftp.account_created", entity="ftp_account",
                entity_id=account["uuid"],
                details={"equipment_id": account["equipment_id"], "username": account["username"]},
                ip_address=ip_address,
            )
            snapshot = dict(account)
        ok, detail = self._helper("create-or-update-account", snapshot["id"], password)
        with self._connect() as conn:
            if ok:
                conn.execute("UPDATE ftp_accounts SET sync_status='synced',sync_error='' WHERE id=?", (snapshot["id"],))
            else:
                conn.execute(
                    """UPDATE ftp_accounts SET is_active=0,deleted_at=CURRENT_TIMESTAMP,
                       sync_status='failed',sync_error=? WHERE id=?""",
                    (detail, snapshot["id"]),
                )
            self._audit(
                conn, user_id=user_id,
                action="ftp.account_synced" if ok else "ftp.account_create_failed",
                entity="ftp_account", entity_id=snapshot["uuid"],
                details={"status": "ok" if ok else "failed", "error": "" if ok else detail},
                ip_address=ip_address,
            )
        return AccountResult(True, ok, snapshot["id"], snapshot["uuid"], snapshot["equipment_id"],
                             snapshot["username"], "" if ok else detail)

    def reset_password(self, account_id: int, password: str, *, user_id: int | None,
                       ip_address: str = "") -> AccountResult:
        validate_password(password)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ftp_accounts WHERE id=? AND deleted_at IS NULL", (account_id,)
            ).fetchone()
            if not row:
                return AccountResult(False, False)
            snapshot = dict(row)
            if not snapshot["is_active"]:
                return AccountResult(True, False, account_id, snapshot["uuid"],
                                     snapshot["equipment_id"], snapshot["username"],
                                     "already_inactive")
            conn.execute(
                """UPDATE ftp_accounts SET password_hash_or_secret_reference=?,is_active=1,
                   sync_status='pending',updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                ("fernet:" + encrypt_secret(password), account_id),
            )
            self._audit(conn, user_id=user_id, action="ftp.account_password_reset",
                        entity="ftp_account", entity_id=row["uuid"], details={}, ip_address=ip_address)
        ok, detail = self._helper("create-or-update-account", account_id, password)
        with self._connect() as conn:
            conn.execute("UPDATE ftp_accounts SET sync_status=?,sync_error=? WHERE id=?",
                         ("synced" if ok else "failed", "" if ok else detail, account_id))
        return AccountResult(True, ok, account_id, snapshot["uuid"], snapshot["equipment_id"],
                             snapshot["username"], "" if ok else detail)

    def disable_account(self, account_id: int, *, delete: bool, user_id: int | None,
                        ip_address: str = "") -> AccountResult:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ftp_accounts WHERE id=?" + ("" if delete else " AND deleted_at IS NULL"),
                (account_id,),
            ).fetchone()
            if not row:
                return AccountResult(False, False)
            snapshot = dict(row)
            deleted_sql = ",deleted_at=CURRENT_TIMESTAMP" if delete else ""
            conn.execute(
                f"UPDATE ftp_accounts SET is_active=0,sync_status='pending'{deleted_sql},updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (account_id,),
            )
        ok, detail = self._helper("disable-account", account_id, None)
        with self._connect() as conn:
            if ok:
                conn.execute("UPDATE ftp_accounts SET sync_status='synced',sync_error='' WHERE id=?", (account_id,))
                action = "ftp.account_deleted" if delete else "ftp.account_disabled"
                self._audit(conn, user_id=user_id, action=action, entity="ftp_account",
                            entity_id=snapshot["uuid"], details={}, ip_address=ip_address)
            else:
                conn.execute(
                    """UPDATE ftp_accounts SET is_active=?,deleted_at=?,sync_status='failed',
                       sync_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (snapshot["is_active"], snapshot["deleted_at"], detail, account_id),
                )
                action = "ftp.account_delete_failed" if delete else "ftp.account_disable_failed"
                self._audit(conn, user_id=user_id, action=action, entity="ftp_account",
                            entity_id=snapshot["uuid"], details={"error": detail}, ip_address=ip_address)
        return AccountResult(True, ok, account_id, snapshot["uuid"], snapshot["equipment_id"],
                             snapshot["username"], "" if ok else detail)

    def deactivate_integration(self, integration_id: int, *, user_id: int | None,
                               ip_address: str = "") -> IntegrationDeactivationResult:
        with self._connect() as conn:
            integration = conn.execute(
                "SELECT * FROM mikrotik_ftp_integrations WHERE id=?", (integration_id,)
            ).fetchone()
            if not integration:
                return IntegrationDeactivationResult(False, False)
            integration = dict(integration)
            account = conn.execute(
                "SELECT * FROM ftp_accounts WHERE id=?", (integration["ftp_account_id"],)
            ).fetchone()
            account = dict(account) if account else None
            managed = bool(account and account["deleted_at"] is None and account["is_active"] and
                           account["notes"] == "Conta exclusiva gerenciada pela integração MikroTik FTP Push.")
            orphaned = not account or account["deleted_at"] is not None
            conn.execute("UPDATE mikrotik_ftp_integrations SET is_active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (integration_id,))
            if managed:
                conn.execute("""UPDATE ftp_accounts SET is_active=0,sync_status='pending',
                             updated_at=CURRENT_TIMESTAMP WHERE id=?""", (account["id"],))
        ok, detail = (self._helper("disable-account", account["id"], None) if managed else (True, ""))
        with self._connect() as conn:
            if not ok:
                conn.execute("UPDATE mikrotik_ftp_integrations SET is_active=1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (integration_id,))
                conn.execute("""UPDATE ftp_accounts SET is_active=1,sync_status='failed',sync_error=?,
                             updated_at=CURRENT_TIMESTAMP WHERE id=?""", (detail, account["id"]))
                self._audit(conn, user_id=user_id, action="mikrotik_ftp.deactivation_failed",
                            entity="mikrotik_ftp_integration", entity_id=integration["uuid"],
                            details={"error": detail}, ip_address=ip_address)
            else:
                self._audit(conn, user_id=user_id, action="mikrotik_ftp.deactivated",
                            entity="mikrotik_ftp_integration", entity_id=integration["uuid"],
                            details={"orphaned_account": orphaned}, ip_address=ip_address)
        return IntegrationDeactivationResult(
            True, ok, integration["equipment_id"], integration["uuid"],
            integration["routeros_major_version"], orphaned, "" if ok else detail,
        )
