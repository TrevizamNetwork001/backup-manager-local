from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from .db import (DB_PATH, TEMP_ADMIN_PASSWORD, MigrationError, SchemaCompatibilityError, connect,
                 integrity_check, migrate, release_schema_version, require_schema_current, schema_problems)
from .jobs import check_health as jobs_check_health, due_jobs, run_once, stats
from .ssh import ERROR_MESSAGES, SSHBackupError, perform_backup, test_connection
from .storage import check_storage as storage_check_report, format_size, storage_stats as storage_stats_report
from .ftp_importer import scan_once
from .ftp_diagnostics import (check_account_directories, check_ftp_config, ftp_statistics,
                              reconcile_accounts, sync_account, sync_all_accounts)
from .lifecycle import CONFIRMATION, execute as lifecycle_execute, health_check as lifecycle_health_check, simulate as lifecycle_simulate
from .cloud_sync import (cancel_item as cloud_cancel_item, enqueue_backup as cloud_enqueue_backup,
    list_policies as cloud_list_policies, list_queue as cloud_list_queue,
    list_targets as cloud_list_targets, retry_item as cloud_retry_item,
    run_once as cloud_run_once, stats as cloud_sync_stats)
from .telegram_backup import (TelegramDocumentTransport, cancel_item as telegram_cancel_item,
    create_test_file as telegram_create_test_file, enqueue_backup as telegram_enqueue_backup,
    list_queue as telegram_list_backup_queue,
    retry_item as telegram_retry_item, run_once as telegram_backup_run_once,
    sanitize_chat_id, stats as telegram_backup_stats, telegram_api_base,
    test_delivery_config as telegram_test_delivery_config)
from .telegram_summaries import process_summary_runs, schedule_summary_runs, summary_stats as telegram_summary_stats
from .telegram_destinations import (cancel_pending as telegram_cancel_pending,
    destination_by_uuid, disable_destination as telegram_disable_destination,
    list_destinations as telegram_list_destinations,
    pending_counts as telegram_pending_counts)
from .notifications import (TelegramAPIError, TelegramTransport, check_health as notification_check_health,
                            current_telegram_config_hash, queue_basic_test)
from .security import decrypt_secret
from .version import __build_id__, __channel__, __version__
from .updater import (UpdateError, list_operations as update_list_operations,
                      ready_operation as update_ready_operation, validate_package)
from .update_repository import RepositoryError, check_updates, run_download_once
from .admin_password_reset import (
    AdminPasswordResetError,
    CONFIRMATION as ADMIN_RESET_CONFIRMATION,
    reset_admin_password,
    temp_password_message,
)


def ui_integrity_check() -> int:
    root = Path(__file__).resolve().parents[1]
    files = [root / "backup_manager/app.py", root / "static/app.css"]
    asset = root / "static/assets/fundo.png"
    if asset.exists(): files.append(asset)
    for path in files:
        if not path.is_file():
            print(f"missing={path}")
            return 1
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"{path.relative_to(root)} {digest}")
    return 0


def storage_check(include_hash: bool = False) -> int:
    with connect() as conn:
        report = storage_check_report(conn, include_hash=include_hash)
        problems = report["problems"]
        conn.execute(
            """
            INSERT INTO audit_log(user_id, action, entity, entity_id, details, ip_address)
            VALUES (NULL, 'storage.check_executed', 'storage', '', ?, '')
            """,
            (f"problems={len(problems)} include_hash={int(include_hash)}",),
        )
    if problems:
        print("storage-check encontrou problemas:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("storage-check ok")
    return 0


def storage_stats() -> int:
    with connect() as conn:
        data = storage_stats_report(conn)
    print(f"backups_disponiveis={data['available_count']}")
    print(f"tamanho_backups={format_size(data['available_size'])}")
    print(f"backups_lixeira={data['trash_count']}")
    print(f"tamanho_lixeira={format_size(data['trash_size'])}")
    print(f"arquivos_ausentes={data['missing']}")
    print(f"arquivos_orfaos={data['orphan']}")
    print(f"filesystem_total={format_size(data['disk_total'])}")
    print(f"filesystem_livre={format_size(data['disk_free'])}")
    return 0


def lifecycle_simulation() -> int:
    with connect() as conn:
        report = lifecycle_simulate(conn)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def lifecycle_cleanup(confirmation: str, automatic: bool = False) -> int:
    with connect() as conn:
        report = lifecycle_execute(conn, confirmation=confirmation, automatic=automatic)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def lifecycle_health(include_hash: bool = True) -> int:
    with connect() as conn:
        report = lifecycle_health_check(conn, include_hash=include_hash)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def jobs_due() -> int:
    with connect() as conn:
        rows = due_jobs(conn)
    if not rows:
        print("nenhum job vencido")
        return 0
    for row in rows:
        print(f"{row['uuid']} equipment={row['equipment_id']} method={row['method']} next_run_at={row['next_run_at']}")
    return 0


def jobs_run_once(force_failure: bool = False) -> int:
    with connect() as conn:
        result = run_once(conn, force_failure=force_failure)
    print(f"queued={result.queued_runs}")
    print(f"processed={result.processed_runs}")
    print(f"success={result.success_runs}")
    print(f"failed={result.failed_runs}")
    print(f"timeout={result.timeout_runs}")
    print(f"skipped={result.skipped_runs}")
    return 0


def jobs_stats() -> int:
    with connect() as conn:
        data = stats(conn)
    for key, value in data.items():
        print(f"{key}={value}")
    return 0


def jobs_check() -> int:
    with connect() as conn:
        data = jobs_check_health(conn)
    if data["running_expirados"] or data["queued_atrasados"]:
        print(f"jobs-check problemas: running_expirados={data['running_expirados']} queued_atrasados={data['queued_atrasados']}")
        return 1
    print("jobs-check ok")
    return 0


def notification_check() -> int:
    with connect() as conn:
        data = notification_check_health(conn)
    if data["missing_channel"]:
        print("notification-check: canal Telegram ausente")
        return 1
    if data["enabled"] and (not data["token_encrypted"] or not data["chat_id"]):
        print("notification-check: canal habilitado sem credenciais completas")
        return 1
    if data["overdue"]:
        print(f"notification-check problemas: pendentes_atrasadas={data['overdue']} falhas_historicas={data['failed']}")
        return 1
    print(f"notification-check ok enabled={data['enabled']} failed_history={data['failed']}")
    return 0


def schema_check() -> int:
    with connect() as conn:
        problems = schema_problems(conn)
    if problems:
        print(f"SCHEMA_OUTDATED missing={','.join(problems)}")
        return 1
    print("SCHEMA_OK")
    return 0


def telegram_diagnose(send_test: bool = False, transport=None) -> int:
    try:
        require_schema_current()
    except SchemaCompatibilityError as exc:
        print(exc.code)
        return 3
    sender = transport or TelegramTransport()
    with connect() as conn:
        row = conn.execute("SELECT token_encrypted,chat_id,is_enabled FROM notification_channels WHERE channel_type='telegram'").fetchone()
        if not row or not row["is_enabled"] or not row["token_encrypted"] or not row["chat_id"]:
            print("DIAGNÓSTICO FALHOU: Telegram não está configurado e habilitado.")
            return 1
        try:
            result = sender.diagnose(decrypt_secret(row["token_encrypted"]), row["chat_id"], send_test)
        except TelegramAPIError as exc:
            print(f"DIAGNÓSTICO FALHOU: {exc.friendly_message}")
            print(f"HTTP={exc.http_status or '-'} TELEGRAM_ERROR={exc.error_code or '-'}")
            if exc.description: print(f"DESCRIÇÃO={exc.description}")
            if exc.retry_after is not None: print(f"NOVA_TENTATIVA_EM={exc.retry_after}s")
            if exc.migrate_to_chat_id: print(f"GRUPO_MIGRADO_NOVO_CHAT_ID={exc.migrate_to_chat_id}")
            return 1
        # Refuse to attach a network result to credentials changed while diagnosis was running.
        current = conn.execute("SELECT * FROM notification_channels WHERE channel_type='telegram'").fetchone()
        config_hash = current_telegram_config_hash(current)
        if not config_hash or current["token_encrypted"] != row["token_encrypted"] or current["chat_id"] != row["chat_id"]:
            print("DIAGNÓSTICO DESCARTADO: a configuração foi alterada durante a verificação.")
            return 1
        values = {"telegram_diag_bot": f"@{result['username']}" if result['username'] else str(result['bot_id']),
                  "telegram_diag_chat": result['chat_title'] or result['chat_type'],
                  "telegram_diag_chat_type": result['chat_type'], "telegram_diag_is_forum": str(int(result['is_forum'])),
                  "telegram_diag_member": result['member_status'], "telegram_diag_can_send": str(int(result['can_send'])),
                  "telegram_diag_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                  "telegram_diag_config_hash": config_hash}
        for key, value in values.items():
            conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP", (key, value))
        conn.execute("UPDATE notification_channels SET last_status='verified',last_error='' WHERE channel_type='telegram'")
    print(f"GETME OK bot_id={result['bot_id']} username=@{result['username'] or '-'}")
    print(f"GETCHAT OK destino={result['chat_title'] or '-'} tipo={result['chat_type'] or '-'} forum={'sim' if result['is_forum'] else 'não'}")
    print(f"GETCHATMEMBER OK status={result['member_status']} pode_enviar={'sim' if result['can_send'] else 'não'}")
    print(f"SENDMESSAGE {'OK message_id=' + result['sent_message_id'] if send_test else 'NÃO EXECUTADO (use --send-test)'}")
    return 0


def ssh_test(equipment_id: int) -> int:
    with connect() as conn:
        try:
            test_connection(conn, equipment_id)
        except SSHBackupError as exc:
            print(f"ssh-test failed code={exc.code} message={ERROR_MESSAGES.get(exc.code, str(exc))}")
            return 1
    print("ssh-test ok")
    return 0


def ssh_backup(equipment_id: int) -> int:
    with connect() as conn:
        try:
            backup, _ = perform_backup(conn, equipment_id, backup_reason="manual")
        except SSHBackupError as exc:
            print(f"ssh-backup failed code={exc.code} message={ERROR_MESSAGES.get(exc.code, str(exc))}")
            return 1
    print(f"ssh-backup ok backup_uuid={backup['uuid']} size={backup['file_size']} filename={backup['original_filename']}")
    return 0


def ftp_stats() -> int:
    with connect() as conn:
        values = ftp_statistics(conn)
    for key, value in values.items(): print(f"{key}={value}")
    return 0


def ftp_permissions_check(fix: bool = False) -> int:
    with connect() as conn:
        results = check_account_directories(conn, fix=fix)
    problems = sum(item["status"] != "ok" for item in results)
    labels = {"ok": "OK", "missing": "MISSING", "bad_mode": "BAD_MODE"}
    for item in results:
        suffix = f" {item['path']}" if item["status"] == "missing" else ""
        print(f"{labels[item['status']]} {item['username']} {item['directory']}{suffix}")
    return 1 if problems else 0


def ftp_accounts_reconcile(fix: bool = False) -> int:
    with connect() as conn:
        report = reconcile_accounts(conn)
    if report["warning"]:
        print(f"WARN {report['warning']}")
    for item in report["accounts"]:
        print(f"{' / '.join(item['issues']) if item['issues'] else 'OK'} {item['username']}")
    return 1 if report["problems"] else 0


def ftp_scan_once() -> int:
    with connect() as conn:
        result = scan_once(conn)
    print(f"detected={result.detected}")
    print(f"waiting={result.waiting}")
    print(f"imported={result.imported}")
    print(f"rejected={result.rejected}")
    print(f"failed={result.failed}")
    return 1 if result.failed else 0


def ftp_config_check() -> int:
    with connect() as conn:
        report = check_ftp_config(conn)
    if not report["enabled"]:
        print("ftp-config-check: FTP nao habilitado")
        return 0
    if report["problems"]:
        print("ftp-config-check encontrou problemas:")
        for problem in report["problems"]: print(f"- {problem}")
        return 1
    print("ftp-config-check ok")
    return 0


def ftp_account_sync(account_id: int) -> int:
    with connect() as conn:
        result = sync_account(conn, account_id)
    print(f"status={'ok' if result['ok'] else 'failed'} detail={result['detail']}")
    return 0 if result["ok"] else 1


def ftp_accounts_sync_all() -> int:
    with connect() as conn:
        report = sync_all_accounts(conn)
    for result in report["results"]:
        print(f"status={'ok' if result['ok'] else 'failed'} detail={result['detail']}")
    print(f"accounts={report['accounts']} failures={report['failures']}")
    return 1 if report["failures"] else 0


def update_check_command(public_key: str) -> int:
    try:
        with connect() as conn:
            release = check_updates(conn, public_key=public_key)
    except RepositoryError as exc:
        if exc.code == "REPOSITORY_DISABLED":
            print("REPOSITORY_DISABLED")
            return 0
        print(f"CHECK_FAILED {exc.code}")
        return 1
    except sqlite3.Error:
        print("CHECK_FAILED DATABASE_ERROR")
        return 1
    print(f"UPDATE_AVAILABLE {release['version']}" if release else "UP_TO_DATE")
    return 0


COMMAND_ALIASES = {
    ("admin", "reset-password"): "reset-admin-password",
    ("database", "migrate"): "migrate",
    ("database", "check"): "integrity-check",
    ("system", "version"): "version",
    ("system", "diagnose"): "ui-integrity-check",
    ("ftp", "diagnose"): "ftp-config-check",
    ("ftp", "stats"): "ftp-stats",
    ("ftp", "permissions"): "ftp-permissions-check",
    ("ftp", "reconcile"): "ftp-accounts-reconcile",
    ("ftp", "scan"): "ftp-scan-once",
    ("ftp", "sync"): "ftp-account-sync",
    ("ftp", "sync-all"): "ftp-accounts-sync-all",
    ("backups", "reconcile"): "storage-check",
    ("retention", "simulate"): "lifecycle-simulate",
    ("retention", "run"): "lifecycle-cleanup",
    ("jobs", "run"): "jobs-run-once",
    ("telegram", "diagnose"): "telegram-diagnose",
    ("updates", "check"): "update-check",
    ("updates", "apply"): "update-apply",
}


def normalize_command_argv(argv: list[str]) -> list[str]:
    """Translate hierarchical public commands to temporary legacy aliases."""
    if len(argv) < 3:
        return list(argv)
    command = COMMAND_ALIASES.get((argv[1], argv[2]))
    return [argv[0], command, *argv[3:]] if command else list(argv)


def domain_commands(domain: str) -> tuple[str, ...]:
    return tuple(sorted(name for (group, name) in COMMAND_ALIASES if group == domain))


def main() -> None:
    if len(os.sys.argv) >= 2 and domain_commands(os.sys.argv[1]) and (
            len(os.sys.argv) == 2 or os.sys.argv[2] in {"-h", "--help"}):
        print(f"Uso: backup-manager {os.sys.argv[1]} <comando>")
        print("Comandos: " + ", ".join(domain_commands(os.sys.argv[1])))
        raise SystemExit(0)
    argv = normalize_command_argv(os.sys.argv)
    if len(argv) >= 2 and argv[1] == "telegram-diagnose":
        if any(arg != "--send-test" for arg in argv[2:]):
            raise SystemExit("Uso: telegram-diagnose [--send-test]")
        raise SystemExit(telegram_diagnose("--send-test" in argv[2:]))
    if len(argv) == 2 and argv[1] == "schema-check":
        raise SystemExit(schema_check())
    if len(argv) == 2 and argv[1] == "schema-release-version":
        try:
            print(release_schema_version())
        except MigrationError as exc:
            print(exc.code)
            raise SystemExit(1)
        raise SystemExit(0)
    parser = argparse.ArgumentParser(description="Backup Manager Local")
    parser.add_argument("command", choices=["migrate", "integrity-check", "ui-integrity-check", "temp-password", "reset-admin-password", "storage-check", "storage-stats", "lifecycle-simulate", "lifecycle-cleanup", "lifecycle-auto", "lifecycle-health", "jobs-due", "jobs-run-once", "jobs-stats", "jobs-check", "notification-check", "ssh-test", "ssh-backup", "ftp-config-check", "ftp-stats", "ftp-permissions-check", "ftp-accounts-reconcile", "ftp-scan-once", "ftp-account-sync", "ftp-accounts-sync-all", "cloud-targets", "cloud-policies", "cloud-queue", "cloud-stats", "cloud-enqueue", "cloud-run-once", "cloud-retry", "cloud-cancel", "telegram-destinations", "telegram-destination-pending", "telegram-destination-disable", "telegram-destination-cancel-pending", "telegram-test-message", "telegram-test-file", "telegram-summary-run", "telegram-summary-stats", "telegram-backup-queue", "telegram-backup-stats", "telegram-backup-enqueue", "telegram-backup-run-once", "telegram-backup-retry", "telegram-backup-cancel", "version", "update-check", "update-download-run", "update-history", "update-validate", "update-plan", "update-apply", "update-status", "update-rollback"])
    parser.add_argument("--equipment-id", type=int, default=0, help="ID do equipamento para comandos SSH")
    parser.add_argument("--hash", action="store_true", help="verifica SHA-256 dos arquivos no storage-check")
    parser.add_argument("--simulate-failure", action="store_true", help="forca falha dry-run no jobs-run-once")
    parser.add_argument("--account-id", type=int, default=0, help="ID da conta FTP")
    parser.add_argument("--backup-id", type=int, default=0, help="ID do backup")
    parser.add_argument("--item-id", type=int, default=0, help="ID do item de sincronização")
    parser.add_argument("--type", choices=("daily", "weekly", "executive", "test", "summary", "alert", "backup_file", "all"), help="tipo de resumo ou item Telegram")
    parser.add_argument("--destination-id", type=int, default=0, help="ID do destino Telegram")
    parser.add_argument("--destination", default="", help="UUID do destino Telegram")
    parser.add_argument("--fix", action="store_true", help="corrige apenas permissões seguras")
    parser.add_argument("--confirm", default="", help=f"confirmacao exigida: {CONFIRMATION}")
    parser.add_argument("--package", default="", help="pacote .bmu local")
    parser.add_argument("--operation", default="", help="UUID da operação de atualização")
    parser.add_argument("--public-key", default="/etc/backup-manager-local/update-public-key.pem", help=argparse.SUPPRESS)
    parser.add_argument("--to-version", type=int, help="versão-alvo explícita para migrate")
    parser.add_argument("--lock-timeout", type=float, default=10.0, help="timeout do lock de migration em segundos")
    admin_selector = parser.add_mutually_exclusive_group()
    admin_selector.add_argument("--username", default="", help="administrador a redefinir")
    admin_selector.add_argument("--user-id", type=int, default=0, help="ID do administrador a redefinir")
    parser.add_argument("--generate", action="store_true", help="gera senha aleatória segura")
    args = parser.parse_args(argv[1:])
    if args.command == "version":
        print("Backup Manager Local")
        print(f"Versão instalada: {__version__}")
        print(f"Canal: {__channel__}")
        print(f"Build: {__build_id__}")
    elif args.command == "update-check":
        raise SystemExit(update_check_command(args.public_key))
    elif args.command == "update-download-run":
        with connect() as conn: processed = run_download_once(conn, public_key=args.public_key)
        print(f"PROCESSED {processed}" if processed else "IDLE")
    elif args.command == "update-validate":
        if not args.package: parser.error("--package e obrigatorio")
        try:
            result = validate_package(args.package, args.public_key)
        except UpdateError as exc:
            print(f"INVALID {exc.code}")
            raise SystemExit(1)
        print("VALID")
        print(f"sha256={result.package_sha256}")
        print(f"version={result.manifest['version']}")
    elif args.command in {"update-history", "update-plan", "update-status"}:
        if args.command != "update-history" and not args.operation:
            parser.error("--operation e obrigatorio")
        with connect() as conn:
            rows = update_list_operations(conn, operation="" if args.command == "update-history" else args.operation)
        if not rows: print("NOT_FOUND"); raise SystemExit(1)
        for row in rows:
            data = dict(row)
            if args.command == "update-plan":
                manifest = json.loads(data.pop("manifest_json_sanitized"))
                data["files"] = manifest.get("files", [])
                data["migrations"] = manifest.get("migrations", [])
                data["services_to_restart"] = manifest.get("services_to_restart", [])
                data["release_notes"] = manifest.get("release_notes", "")
            else:
                data.pop("manifest_json_sanitized", None)
            print(json.dumps(data, ensure_ascii=False))
    elif args.command in {"update-apply", "update-rollback"}:
        if not args.operation: parser.error("--operation e obrigatorio")
        if os.geteuid() != 0:
            print("INVALID ROOT_REQUIRED"); raise SystemExit(1)
        if args.command == "update-apply":
            with connect() as conn: selected = update_ready_operation(conn, args.operation)
            if not selected or selected["status"] != "ready":
                print("INVALID OPERATION_NOT_READY"); raise SystemExit(1)
            if args.confirm != f"ATUALIZAR PARA {selected['to_version']}":
                print("INVALID CONFIRMATION_REQUIRED"); raise SystemExit(1)
        if args.command == "update-rollback" and args.confirm != "RESTAURAR_VERSAO_ANTERIOR":
            print("INVALID CONFIRMATION_REQUIRED"); raise SystemExit(1)
        helper = Path(__file__).resolve().parents[1] / "scripts/update-helper.py"
        command = [str(helper), "apply" if args.command == "update-apply" else "rollback", "--operation", args.operation]
        raise SystemExit(subprocess.run(command, check=False).returncode)
    elif args.command == "migrate":
        if args.to_version is None:
            parser.error("--to-version e obrigatorio para migrate")
        try:
            result = migrate(args.to_version, lock_timeout=args.lock_timeout)
        except MigrationError as exc:
            print(exc.code)
            raise SystemExit(1)
        print(f"migrations_aplicadas={len(result.applied)} versao={result.to_version} backup={result.backup_path or '-'}")
    elif args.command == "integrity-check":
        print(integrity_check())
    elif args.command == "ui-integrity-check":
        raise SystemExit(ui_integrity_check())
    elif args.command == "temp-password":
        reveal, warning = temp_password_message(DB_PATH)
        print(warning)
        if reveal:
            print(TEMP_ADMIN_PASSWORD)
        else:
            raise SystemExit(2)
    elif args.command == "reset-admin-password":
        try:
            diagnostic = reset_admin_password(
                DB_PATH, username=args.username, user_id=args.user_id,
            )
        except AdminPasswordResetError as exc:
            print(f"RESET_CANCELADO {exc.code}: {exc}")
            raise SystemExit(2)
        summary = diagnostic.summary
        print(f"banco={summary.path}")
        print(f"tamanho={summary.size} schema={summary.schema_version} usuarios={summary.users}")
        print(
            f"equipamentos={summary.equipment} jobs={summary.jobs} backups={summary.backups} "
            f"ftp={summary.ftp_accounts} telegram={summary.telegram_destinations}"
        )
        if args.confirm != ADMIN_RESET_CONFIRMATION:
            print(f"DRY_RUN usuario={diagnostic.username} confirmação exigida: --confirm {ADMIN_RESET_CONFIRMATION}")
        else:
            try:
                result = reset_admin_password(
                    DB_PATH, confirmation=args.confirm, username=args.username,
                    user_id=args.user_id, generate=args.generate,
                )
            except AdminPasswordResetError as exc:
                print(f"RESET_CANCELADO {exc.code}: {exc}")
                raise SystemExit(2)
            if result.generated_password:
                print(f"NOVA_SENHA_GERADA={result.generated_password}")
            print(f"RESET_OK usuario={result.username} backup={result.backup_path}")
            print(f"sessoes_invalidadas={result.sessions_invalidated} integridade=ok fk=ok contagens_operacionais=preservadas")
            print("A nova senha deverá ser trocada no primeiro login.")
    elif args.command == "storage-check":
        raise SystemExit(storage_check(args.hash))
    elif args.command == "storage-stats":
        raise SystemExit(storage_stats())
    elif args.command == "lifecycle-simulate":
        raise SystemExit(lifecycle_simulation())
    elif args.command == "lifecycle-cleanup":
        raise SystemExit(lifecycle_cleanup(args.confirm))
    elif args.command == "lifecycle-auto":
        raise SystemExit(lifecycle_cleanup("", automatic=True))
    elif args.command == "lifecycle-health":
        raise SystemExit(lifecycle_health(args.hash))
    elif args.command == "jobs-due":
        raise SystemExit(jobs_due())
    elif args.command == "jobs-run-once":
        raise SystemExit(jobs_run_once(args.simulate_failure))
    elif args.command == "jobs-stats":
        raise SystemExit(jobs_stats())
    elif args.command == "jobs-check":
        raise SystemExit(jobs_check())
    elif args.command == "notification-check":
        raise SystemExit(notification_check())
    elif args.command == "ssh-test":
        if not args.equipment_id:
            parser.error("--equipment-id e obrigatorio para ssh-test")
        raise SystemExit(ssh_test(args.equipment_id))
    elif args.command == "ssh-backup":
        if not args.equipment_id:
            parser.error("--equipment-id e obrigatorio para ssh-backup")
        raise SystemExit(ssh_backup(args.equipment_id))
    elif args.command == "ftp-config-check":
        raise SystemExit(ftp_config_check())
    elif args.command == "ftp-stats":
        raise SystemExit(ftp_stats())
    elif args.command == "ftp-permissions-check":
        raise SystemExit(ftp_permissions_check(args.fix))
    elif args.command == "ftp-accounts-reconcile":
        raise SystemExit(ftp_accounts_reconcile(args.fix))
    elif args.command == "ftp-scan-once":
        raise SystemExit(ftp_scan_once())
    elif args.command == "ftp-account-sync":
        if not args.account_id: parser.error("--account-id e obrigatorio")
        raise SystemExit(ftp_account_sync(args.account_id))
    elif args.command == "ftp-accounts-sync-all":
        raise SystemExit(ftp_accounts_sync_all())
    elif args.command == "cloud-targets":
        with connect() as conn: rows = cloud_list_targets(conn)
        for row in rows: print(f"id={row['id']} uuid={row['uuid']} name={row['name']} provider={row['provider']} mode={row['mode']} active={row['is_active']} destination={row['destination_label']}")
    elif args.command == "cloud-policies":
        with connect() as conn: rows = cloud_list_policies(conn)
        for row in rows: print(f"id={row['id']} uuid={row['uuid']} target={row['target_id']} scope={row['scope_type']} equipment={row['equipment_id'] or '-'} enabled={row['enabled']} auto={row['sync_new_backups']}")
    elif args.command == "cloud-queue":
        with connect() as conn: rows = cloud_list_queue(conn)
        for row in rows: print(f"id={row['id']} uuid={row['uuid']} target={row['target_id']} backup={row['backup_id']} status={row['status']} attempt={row['attempt']}/{row['max_attempts']} bytes={row['bytes_total']} next={row['next_attempt_at'] or '-'}")
    elif args.command == "cloud-stats":
        with connect() as conn: data = cloud_sync_stats(conn)
        for key, value in data.items(): print(f"{key}={value}")
    elif args.command == "cloud-enqueue":
        if not args.backup_id: parser.error("--backup-id e obrigatorio")
        with connect() as conn: created = cloud_enqueue_backup(conn, args.backup_id)
        print(f"queued={len(created)}")
        raise SystemExit(0 if created else 1)
    elif args.command == "cloud-run-once":
        with connect() as conn: data = cloud_run_once(conn, simulate_failure=args.simulate_failure)
        for key, value in data.items(): print(f"{key}={value}")
        raise SystemExit(1 if data["failed"] else 0)
    elif args.command == "cloud-retry":
        if not args.item_id: parser.error("--item-id e obrigatorio")
        with connect() as conn: ok = cloud_retry_item(conn, args.item_id)
        print(f"retried={int(ok)}")
        raise SystemExit(0 if ok else 1)
    elif args.command == "cloud-cancel":
        if not args.item_id: parser.error("--item-id e obrigatorio")
        with connect() as conn: ok = cloud_cancel_item(conn, args.item_id)
        print(f"cancelled={int(ok)}")
        raise SystemExit(0 if ok else 1)
    elif args.command == "telegram-destinations":
        with connect() as conn: rows = telegram_list_destinations(conn)
        for row in rows:
            print(f"id={row['id']} uuid={row['uuid']} name={row['name']} chat={sanitize_chat_id(row['chat_id'])} type={row['destination_type']} thread={row['default_thread_id'] or '-'} active={row['is_active']}")
    elif args.command == "telegram-destination-pending":
        if not args.destination: parser.error("--destination e obrigatorio")
        with connect() as conn:
            row = destination_by_uuid(conn, args.destination)
            if not row: raise SystemExit("Destino não encontrado.")
            data = telegram_pending_counts(conn, row["id"])
        for key, value in data.items(): print(f"{key}={value}")
    elif args.command == "telegram-destination-disable":
        if not args.destination: parser.error("--destination e obrigatorio")
        with connect() as conn: data = telegram_disable_destination(conn, args.destination)
        print("disabled=1 pending=" + str(data["all"]))
    elif args.command == "telegram-destination-cancel-pending":
        if not args.destination or args.type not in {"test", "summary", "alert", "backup_file", "all"}: parser.error("--destination e --type sao obrigatorios")
        confirmation = args.confirm == "CANCELAR TODOS OS ITENS TELEGRAM"
        with connect() as conn: total = telegram_cancel_pending(conn, args.destination, args.type, confirm_all=confirmation)
        print(f"cancelled={total} type={args.type}")
    elif args.command == "telegram-test-message":
        with connect() as conn: queued = queue_basic_test(conn)
        print(f"queued={queued}")
    elif args.command == "telegram-test-file":
        path = None
        try:
            with connect() as conn:
                destination, channel = telegram_test_delivery_config(conn, args.destination_id)
                if not destination or not channel or not channel['is_enabled'] or not channel['token_encrypted']:
                    print("FAILED TELEGRAM_NOT_CONFIGURED"); raise SystemExit(1)
                path = telegram_create_test_file(conn)
                message_id, _ = TelegramDocumentTransport().send_document(decrypt_secret(channel['token_encrypted']), destination['chat_id'], destination['default_thread_id'], path, "backup-manager-test.txt", "Arquivo fictício de teste — sem dados reais.", telegram_api_base(conn))
            print(f"SENT message_id={message_id}")
        finally:
            if path is not None: path.unlink(missing_ok=True)
    elif args.command == "telegram-summary-run":
        if not args.type: parser.error("--type e obrigatorio")
        with connect() as conn:
            queued = schedule_summary_runs(conn, force_type=args.type)
            result = process_summary_runs(conn, TelegramTransport())
        print(f"queued={queued} sent={result['sent']} retry_wait={result['retry_wait']} failed={result['failed']}")
        raise SystemExit(1 if result['failed'] else 0)
    elif args.command == "telegram-summary-stats":
        with connect() as conn: data = telegram_summary_stats(conn)
        for key, value in data.items(): print(f"{key}={value}")
    elif args.command == "telegram-backup-queue":
        with connect() as conn: rows = telegram_list_backup_queue(conn)
        for row in rows: print(f"id={row['id']} uuid={row['uuid']} backup={row['backup_id']} destination={row['destination_id']} thread={row['thread_id'] or '-'} status={row['status']} attempt={row['attempt']}/{row['max_attempts']} bytes={row['bytes_total']} error={row['error_code'] or '-'}")
    elif args.command == "telegram-backup-stats":
        with connect() as conn: data = telegram_backup_stats(conn)
        for key, value in data.items(): print(f"{key}={value}")
    elif args.command == "telegram-backup-enqueue":
        if not args.backup_id: parser.error("--backup-id e obrigatorio")
        with connect() as conn: created = telegram_enqueue_backup(conn, args.backup_id, destination_id=args.destination_id or None)
        print(f"queued={len(created)}"); raise SystemExit(0 if created else 1)
    elif args.command == "telegram-backup-run-once":
        with connect() as conn: data = telegram_backup_run_once(conn)
        for key, value in data.items(): print(f"{key}={value}")
        raise SystemExit(1 if data['failed'] else 0)
    elif args.command == "telegram-backup-retry":
        if not args.item_id: parser.error("--item-id e obrigatorio")
        with connect() as conn: ok = telegram_retry_item(conn, args.item_id)
        print(f"retried={int(ok)}"); raise SystemExit(0 if ok else 1)
    elif args.command == "telegram-backup-cancel":
        if not args.item_id: parser.error("--item-id e obrigatorio")
        with connect() as conn: ok = telegram_cancel_item(conn, args.item_id)
        print(f"cancelled={int(ok)}"); raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
