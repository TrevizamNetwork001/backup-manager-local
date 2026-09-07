from __future__ import annotations

import os
import hashlib
import multiprocessing
import shutil
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("BACKUP_MANAGER_DATA", tempfile.mkdtemp(prefix="bm-mt-data-"))
os.environ.setdefault("BACKUP_MANAGER_DB", os.path.join(os.environ["BACKUP_MANAGER_DATA"], "test.sqlite3"))
os.environ.setdefault("BACKUP_MANAGER_STORAGE_ROOT", tempfile.mkdtemp(prefix="bm-mt-storage-"))
os.environ.setdefault("BACKUP_MANAGER_SECRET_KEY", os.path.join(os.environ["BACKUP_MANAGER_DATA"], "secret.key"))

from backup_manager.db import DB_PATH, connect, migrate  # noqa: E402
from backup_manager.ftp import account_paths  # noqa: E402
from backup_manager.ftp_importer import scan_once  # noqa: E402
from backup_manager.mikrotik_ftp_credentials import rotate_credential  # noqa: E402
from backup_manager.mikrotik_ftp_scripts import (  # noqa: E402
    ScriptConfig,
    ScriptError,
    build_execution_id,
    config_fingerprint,
    generate_install_script,
    generate_removal_script,
    generate_test_import,
    generate_test_script,
    normalize_clock_date,
    routeros_quote,
    routeros_source_quote,
    sanitize_device_name,
)
from backup_manager.mikrotik_ftp_service import (  # noqa: E402
    create_integration,
    create_test,
    expire_test,
    integration_config,
    is_mikrotik_equipment,
    validate_ftp_directory,
)
from backup_manager.storage import load_config  # noqa: E402
from backup_manager.ssh import (  # noqa: E402
    SSHBackupError,
    _routeros_command,
    _routeros_major,
    _routeros_preflight,
    install_routeros_script,
    parse_routeros_output,
    run_routeros_ftp_test,
    run_routeros_managed_backup,
)


def concurrent_scan_worker(iso_now: str, queue) -> None:
    try:
        result = scan_once(now=datetime.fromisoformat(iso_now))
        queue.put(("ok", result.imported, result.failed))
    except Exception as exc:  # pragma: no cover - asserted in parent process
        queue.put(("error", type(exc).__name__, str(exc)[:80]))


def config(**changes) -> ScriptConfig:
    values = dict(device_id=42, device_name="Roteador São Paulo / Core", routeros_version=7,
                  backup_format="both", schedule_mode="scheduled", schedule_frequency="daily",
                  schedule_time="02:00", ftp_host="10.0.0.10", ftp_port=21,
                  ftp_username="bm-mt-42-abcd", ftp_password='Forte$e\"Segura\\2026', ftp_directory="/")
    values.update(changes)
    return ScriptConfig(**values)


class RouterOSScriptTest(unittest.TestCase):
    def test_preflight_classifies_all_object_states(self) -> None:
        cfg = config()
        marker = f"# backup-manager-version=5.8 # backup-manager-config={config_fingerprint(cfg)}"
        cases = [
            (("0", "0", "", ""), "not_installed"),
            (("1", "0", marker, ""), "partial"),
            (("0", "1", "", 'interval=1d on-event="/system script run backup-manager"'), "partial"),
            (("2", "1", "", ""), "duplicated"),
            (("1", "1", "old source", 'interval=2d on-event="other"'), "divergent"),
            (("1", "1", marker, 'interval=1d disabled=no policy=ftp,read,write,policy,test,sensitive on-event="/system script run backup-manager"'), "installed_valid"),
        ]
        for outputs, expected in cases:
            script_count, scheduler_count, script_detail, scheduler_detail = outputs
            def response(_client, command, *_args, **_kwargs):
                if "script print count" in command: return script_count
                if "scheduler print count" in command: return scheduler_count
                if "script print detail" in command: return script_detail
                return scheduler_detail
            with self.subTest(expected=expected), mock.patch("backup_manager.ssh._routeros_command", side_effect=response):
                self.assertEqual(_routeros_preflight(object(), cfg, 30)["state"], expected)

    def test_routeros_v7_date_normalization_snapshots(self) -> None:
        self.assertEqual(normalize_clock_date("2026-07-14"), "2026-07-14")
        self.assertEqual(normalize_clock_date("jul/14/2026"), "2026-07-14")
        self.assertEqual(normalize_clock_date("jul/4/2026"), "2026-07-04")
        for invalid in ("-14-01--0", "2026-7-4", "xyz/14/2026", "jul/32/2026"):
            with self.subTest(invalid=invalid), self.assertRaises(ScriptError):
                normalize_clock_date(invalid)
        script = generate_install_script(config(routeros_version=7))
        self.assertIn('[:pick \\$bmDateOriginal 4 5] = \\"-\\"', script)
        self.assertIn('backup-manager: data original:', script)
        self.assertIn('backup-manager: data normalizada:', script)

    def test_explicit_legacy_vendor_and_generic_recognition(self) -> None:
        self.assertTrue(is_mikrotik_equipment({"ssh_backup_driver": "mikrotik_routeros"}))
        self.assertTrue(is_mikrotik_equipment({"ssh_backup_driver": "routeros"}))
        self.assertTrue(is_mikrotik_equipment({"ssh_backup_driver": "generic_ssh"}, "MikroTik"))
        self.assertFalse(is_mikrotik_equipment({"ssh_backup_driver": "generic_ssh"}, "Cisco"))
        self.assertFalse(is_mikrotik_equipment({"ssh_backup_driver": "ssh"}, "Generic Router"))
        self.assertEqual(validate_ftp_directory("/routeros/backups"), "/routeros/backups")
        for invalid in ("relative", "/../private", "/bad path", "/bad\npath"):
            with self.assertRaises(ValueError):
                validate_ftp_directory(invalid)

    def test_v6_v7_formats_manual_and_daily(self) -> None:
        for version in (6, 7):
            for backup_format in ("backup", "rsc", "both"):
                script = generate_install_script(config(routeros_version=version, backup_format=backup_format))
                self.assertIn(f"RouterOS v{version}", script)
                self.assertEqual("/system backup save" in script, backup_format in {"backup", "both"})
                self.assertEqual("/export" in script, backup_format in {"rsc", "both"})
                self.assertNotIn("show-sensitive", script)
                self.assertIn('bmSafe . \\".\\" . \\$bmDateNormalized', script)
                self.assertNotRegex(script, r"x[a-f0-9]{12}-")
                self.assertIn("backupManagerFtpSequence", script)
                self.assertIn("backupManagerFtpLastBase", script)
                self.assertNotIn("dont-encrypt=yes", script)
                self.assertNotIn(":tolower", script)
                self.assertNotIn("bmUpper", script)
                self.assertIn('bmSafe \\"roteador-sao-paulo-core\\"', script)
                self.assertEqual(script.count('scheduler add name="backup-manager"'), 1)
        manual = generate_install_script(config(schedule_mode="manual"))
        self.assertIn('scheduler add name="backup-manager"', manual)
        self.assertIn('policy=ftp,read,write,policy,test,sensitive', manual)
        self.assertIn('disabled=yes', manual)

    def test_v6_identity_is_sanitized_before_script_generation(self) -> None:
        script = generate_install_script(config(routeros_version=6), include_secret=True)
        self.assertNotIn(":tolower", script)
        self.assertNotIn("bmUpper", script)
        self.assertNotIn("bmDoubleDash", script)
        self.assertIn(':local bmSafe \\"roteador-sao-paulo-core\\"', script)
        self.assertIn("backup-manager: identity configurada:", script)

    def test_two_same_second_executions_have_distinct_ids(self) -> None:
        started = datetime(2026, 7, 16, 10, 55, tzinfo=timezone.utc)
        first = build_execution_id("abcdef123456", started, 41)
        second = build_execution_id("abcdef123456", started, 42)
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^x[a-f0-9]{12}-\d{14}-\d+$")

    def test_short_filename_is_readable_and_keeps_sequence(self) -> None:
        script = generate_install_script(config(device_name="MK-TESTE", routeros_version=6), include_secret=True)
        self.assertIn(':local bmSafe \\"mk-teste\\"', script)
        self.assertIn('\\$bmDateNormalized . \\"-\\"', script)
        self.assertIn('\\".\\" . \\$backupManagerFtpSequence', script)

    def test_sanitization_escaping_and_validation(self) -> None:
        self.assertEqual(sanitize_device_name(" Roteador São/Paulo! "), "roteador-sao-paulo")
        self.assertEqual(sanitize_device_name("ROUTER-BASE-MELHOR DO ABC"), "router-base-melhor-do-abc")
        self.assertEqual(sanitize_device_name(""), "mikrotik-0")
        self.assertEqual(sanitize_device_name("???"), "mikrotik-0")
        quoted = routeros_quote('u$er"\\name')
        self.assertEqual(quoted, '"u\\$er\\"\\\\name"')
        source = routeros_source_quote(':local password "p$ss"\n:put {ok}')
        self.assertNotIn("\n", source)
        self.assertIn(r"\r\n", source)
        script = generate_install_script(config())
        self.assertIn('bmSafe . \\".\\" . \\$bmDateNormalized', script)
        self.assertIn("backupManagerFtpRunning", script)
        self.assertNotIn("backupManagerFtpTestToken", script)
        self.assertNotIn("test-", script)
        self.assertNotIn(config().ftp_password, script)
        with self.assertRaises(ScriptError):
            generate_install_script(config(schedule_time="29:99"))
        with self.assertRaises(ScriptError):
            routeros_quote("bad\nvalue")

    def test_owned_objects_test_trigger_and_restricted_removal(self) -> None:
        script = generate_install_script(config())
        self.assertIn('name="backup-manager"', script)
        self.assertNotIn('backup-manager-ftp-test', script)
        trigger = generate_test_script(config(), include_secret=True)
        self.assertIn("password=", trigger)
        self.assertNotIn("backupManagerFtpTestToken", trigger)
        self.assertNotIn("/system script", trigger)
        self.assertNotIn("/system scheduler", trigger)
        self.assertIn("backup-manager-test-42", trigger)
        self.assertIn('.rsc', trigger)
        self.assertNotIn("/system backup save", trigger)
        self.assertNotIn("show-sensitive", trigger)
        self.assertNotIn("bmToken", trigger)
        removal = generate_removal_script(7)
        self.assertEqual(removal.count("/system script remove"), 1)
        self.assertEqual(removal.count("/system scheduler remove"), 1)
        self.assertNotIn("remove [find]", removal)

    def test_invalid_ports_and_routeros_errors_fail_fast(self) -> None:
        for port in (0, 65536, "21", None):
            with self.subTest(port=port), self.assertRaises(ScriptError):
                generate_install_script(config(ftp_port=port))

        class Stream:
            def __init__(self, value=b""): self.value = value
            def read(self): return self.value

        class RejectedClient:
            def exec_command(self, command, timeout=60):
                return None, Stream(), Stream(b"expected closing brace")

        with self.assertRaises(SSHBackupError) as caught:
            _routeros_command(RejectedClient(), "/safe-placeholder", 5, step="Instalação do script")
        self.assertEqual(caught.exception.code, "ROUTEROS_SYNTAX_ERROR")
        self.assertIn("Instalação do script", str(caught.exception))
        detail = 'Flags: I - invalid\n 0 name="backup-manager" source="# Backup Manager Local"'
        self.assertIn('name="backup-manager"', parse_routeros_output(detail, step="Validação"))

    def test_file_validation_cleanup_logs_and_destinations(self) -> None:
        empty = generate_install_script(config(ftp_directory="/"), include_secret=True)
        nested = generate_install_script(config(ftp_directory="/routeros/backups/"), include_secret=True)
        self.assertIn('backup-manager: identity original:', empty)
        self.assertIn('backup-manager: identity configurada:', empty)
        self.assertIn('backup-manager: data original:', empty)
        self.assertIn('backup-manager: data normalizada:', empty)
        self.assertIn('backup-manager: nome-base gerado:', empty)
        self.assertIn('backup-manager: upload do backup iniciado:', empty)
        self.assertIn('backup-manager: upload do backup concluido', empty)
        self.assertIn('backup-manager: upload do export iniciado:', empty)
        self.assertIn('backup-manager: upload do export concluido', empty)
        self.assertIn('backup-manager: limpeza local iniciada', empty)
        self.assertIn('backup-manager: limpeza local concluida', empty)
        self.assertIn(r':local bmRemoteBackup \$bmBackup', empty)
        self.assertNotIn(r'"/" . \$bmBackup', empty)
        self.assertIn(r'\"routeros/backups\"', nested)
        self.assertIn(r'\$bmRemoteDir . \"/\" . \$bmBackup', nested)
        self.assertIn(r':local bmBackupIds [/file find where name=\$bmBackup]', empty)
        self.assertIn(r':if ([:len \$bmBackupIds] != 1)', empty)
        self.assertIn(r'[/file get \$bmBackupIds size]', empty)
        self.assertNotIn('/file get [find', empty)
        for message in (
            "iniciando rotina", "criando arquivos", "upload do backup iniciado", "upload do backup concluido",
            "upload do export iniciado", "upload do export concluido", "rotina concluida", "falha na rotina",
        ):
            self.assertIn(f'backup-manager: {message}', empty)
        failure = empty.split('} on-error={', 1)[1]
        self.assertIn('backup-manager: falha original:', failure)
        self.assertIn('backup-manager: limpeza de erro iniciada', failure)
        self.assertIn(r':foreach bmFileId in=\$bmCleanupBackup do={ /file remove \$bmFileId }', failure)
        self.assertIn(r':foreach bmFileId in=\$bmCleanupRsc do={ /file remove \$bmFileId }', failure)
        self.assertIn('backup-manager: limpeza de erro concluida', failure)
        self.assertIn(':set backupManagerFtpRunning false', failure)
        self.assertIn(r':error (\"backup-manager: falha na rotina: \" . \$bmOriginalError)', failure)
        self.assertIn(r':set bmPassword \"\"', failure)

    def test_port_is_unquoted_integer_and_test_import_is_isolated(self) -> None:
        permanent = generate_install_script(config(ftp_port=2121), include_secret=True)
        self.assertIn(':local bmPort 2121', permanent)
        self.assertNotIn(':local bmPort "2121"', permanent)
        temporary = generate_test_import(config())
        self.assertIn('/export file=$bmBase', temporary)
        self.assertNotIn('/file add', temporary)
        self.assertNotIn('backupManagerFtpRunning', temporary)
        self.assertNotIn('backupManagerFtpTestToken', temporary)
        self.assertNotIn('/system script run backup-manager', temporary)
        self.assertIn('/file remove $bmFileIds', temporary)
        self.assertIn('duration=2m idle-timeout=15s', permanent)
        self.assertIn('duration=2m idle-timeout=15s', temporary)
        routeros_v6 = generate_install_script(config(routeros_version=6), include_secret=True)
        self.assertNotIn('duration=2m', routeros_v6)
        self.assertNotIn('idle-timeout=15s', routeros_v6)

    def test_routeros_version_selection_and_minimums(self) -> None:
        for raw, expected in (("6.43 (stable)", 6), ("6.49.17", 6), ("7.0", 7), ("7.21.4 (stable)", 7)):
            with self.subTest(raw=raw):
                self.assertEqual(_routeros_major(raw), expected)
        for raw in ("6.42.12 (stable)", "5.26", "RouterOS unknown"):
            with self.subTest(raw=raw), self.assertRaises(SSHBackupError) as raised:
                _routeros_major(raw)
            self.assertEqual(raised.exception.code, "ROUTEROS_VERSION_UNSUPPORTED")

    def test_unsupported_routeros_is_blocked_before_preflight_or_sftp(self) -> None:
        class Stream:
            def __init__(self, value=b""): self.value = value
            def read(self): return self.value

        class Client:
            def __init__(self): self.commands = []; self.sftp_opened = False
            def connect(self, **_kwargs): pass
            def exec_command(self, command, timeout=60):
                self.commands.append(command)
                return None, Stream(b"6.42.12 (stable)\n"), Stream()
            def open_sftp(self):
                self.sftp_opened = True
                raise AssertionError("SFTP não deveria ser aberto")
            def close(self): pass

        client = Client()
        equipment = {"ssh_backup_driver": "mikrotik_routeros", "ip_address": "192.0.2.42", "ssh_port": 22}
        credential = {"username": "admin", "port": 22}
        with mock.patch("backup_manager.ssh._connect_client", return_value=client), self.assertRaises(SSHBackupError) as raised:
            install_routeros_script(equipment, credential, config(), 5, 30)
        self.assertEqual(raised.exception.code, "ROUTEROS_VERSION_UNSUPPORTED")
        self.assertEqual(client.commands, [":put [/system resource get version]"])
        self.assertFalse(client.sftp_opened)

    def test_managed_backup_executes_complete_script_after_validation(self) -> None:
        client = mock.Mock()
        client.close = mock.Mock()
        with mock.patch("backup_manager.ssh._connect_client", return_value=client), \
             mock.patch("backup_manager.ssh._routeros_command", side_effect=["7.21.4", "", "mk-test.2026-07-19-16-25-00.9"]) as command, \
             mock.patch("backup_manager.ssh._routeros_preflight", return_value={"state": "installed_valid"}) as preflight:
            result = run_routeros_managed_backup({}, {}, config(), 5, 30)
        self.assertTrue(result["executed"])
        self.assertEqual("mk-test.2026-07-19-16-25-00.9", result["expected_base"])
        self.assertIn('/system script run "backup-manager"', [call.args[1] for call in command.call_args_list])
        preflight.assert_called_once()
        self.assertTrue(client.close.called)

    def test_installer_accepts_routeros_6_disk_prefixed_sftp_file(self) -> None:
        client = mock.Mock()
        client.open_sftp.return_value = mock.Mock()
        preflight_before = {"state": "divergent", "script_valid": False, "scheduler_valid": False}
        preflight_after = {"state": "installed_valid", "script_valid": True, "scheduler_valid": True}
        with mock.patch("backup_manager.ssh._connect_client", return_value=client), \
             mock.patch("backup_manager.ssh._routeros_preflight", side_effect=[preflight_before, preflight_after]), \
             mock.patch("backup_manager.ssh._routeros_command", side_effect=["6.49.6", "0", "1", "", "", ""] ) as command:
            result = install_routeros_script(
                {"ssh_backup_driver": "mikrotik_routeros", "ip_address": "192.0.2.6", "ssh_port": 22},
                {"username": "admin", "port": 22}, config(routeros_version=6), 5, 30, repair=True,
            )
        imported = [call.args[1] for call in command.call_args_list if call.args[1].startswith('/import')]
        self.assertEqual(1, len(imported))
        self.assertIn('file-name="disk/backup-manager-install-', imported[0])
        self.assertTrue(result["changed"])

    def test_ftp_test_removes_root_and_routeros_6_disk_temporary_files(self) -> None:
        client = mock.Mock()
        client.open_sftp.return_value = mock.Mock()
        with mock.patch("backup_manager.ssh._connect_client", return_value=client), \
             mock.patch("backup_manager.ssh._routeros_preflight", return_value={"state": "installed_valid"}), \
             mock.patch("backup_manager.ssh._routeros_command", side_effect=["6.49.6", "", "", ""]) as command:
            result = run_routeros_ftp_test({}, {}, config(routeros_version=6), 5, 30)
        commands = [call.args[1] for call in command.call_args_list]
        cleanup = [value for value in commands if value.startswith('/file remove')]
        self.assertEqual(2, len(cleanup))
        self.assertTrue(any('name="backup-manager-ftp-test-' in value for value in cleanup))
        self.assertTrue(any('name="disk/backup-manager-ftp-test-' in value for value in cleanup))
        self.assertTrue(result["command_executed"])

    def test_installer_waits_for_routeros_file_index_after_sftp(self) -> None:
        client = mock.Mock()
        client.open_sftp.return_value = mock.Mock()
        before = {"state": "divergent", "script_valid": False, "scheduler_valid": False}
        after = {"state": "installed_valid", "script_valid": True, "scheduler_valid": True}
        outputs = ["6.49.6", "0", "0", "", "0", "1", "", "", ""]
        with mock.patch("backup_manager.ssh._connect_client", return_value=client), \
             mock.patch("backup_manager.ssh._routeros_preflight", side_effect=[before, after]), \
             mock.patch("backup_manager.ssh._routeros_command", side_effect=outputs) as command:
            result = install_routeros_script(
                {"ssh_backup_driver": "mikrotik_routeros", "ip_address": "192.0.2.6", "ssh_port": 22},
                {"username": "admin", "port": 22}, config(routeros_version=6), 5, 30, repair=True,
            )
        commands = [call.args[1] for call in command.call_args_list]
        self.assertIn(":delay 1s", commands)
        self.assertTrue(any(value.startswith('/import file-name="disk/') for value in commands))
        self.assertTrue(result["changed"])

    def test_managed_backup_preserves_routeros_execution_error(self) -> None:
        client = mock.Mock()
        failure = SSHBackupError("ROUTEROS_SYNTAX_ERROR", "Execução completa do backup: failure: disk full")
        with mock.patch("backup_manager.ssh._connect_client", return_value=client), \
             mock.patch("backup_manager.ssh._routeros_command", side_effect=["7.21.4", failure]), \
             mock.patch("backup_manager.ssh._routeros_preflight", return_value={"state": "installed_valid"}), \
             self.assertRaisesRegex(SSHBackupError, "disk full"):
            run_routeros_managed_backup({}, {}, config(), 5, 30)

    def test_routeros_parser_prioritizes_error_over_positive_message(self) -> None:
        with self.assertRaises(SSHBackupError):
            parse_routeros_output(
                "expected closing brace\nbackup-manager: ftp commands completed",
                step="Importação",
            )

    def test_routeros_parser_reports_ftp_authentication_failure_safely(self) -> None:
        with self.assertRaisesRegex(SSHBackupError, "Verifique o usuário e a senha FTP") as raised:
            parse_routeros_output(
                "failure: invalid user name or password",
                step="Comando FTP", secret=True,
            )
        self.assertEqual("FTP_AUTH_FAILED", raised.exception.code)


class MikroTikUploadTest(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(DB_PATH.parent, ignore_errors=True)
        shutil.rmtree(os.environ["BACKUP_MANAGER_STORAGE_ROOT"], ignore_errors=True)
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        Path(os.environ["BACKUP_MANAGER_STORAGE_ROOT"]).mkdir(parents=True, exist_ok=True)
        os.environ["BACKUP_MANAGER_SECRET_KEY"] = str(DB_PATH.parent / "secret.key")
        migrate(31)
        with connect() as conn:
            vendor = conn.execute("SELECT id FROM vendors WHERE name='MikroTik'").fetchone()[0]
            conn.execute("""INSERT INTO equipment(id,name,hostname,ip_address,vendor_id,is_active,ssh_backup_driver,ssh_port)
                          VALUES(42,'Router Core','router-core','192.0.2.42',?,1,'mikrotik_routeros',22)""", (vendor,))
            self.integration, self.account, self.password = create_integration(
                conn, equipment_id=42, routeros_version=7, backup_format="both", schedule_mode="scheduled",
                schedule_time="02:00", ftp_host="192.0.2.10", ftp_port=21, user_id=1, retention_days=30,
            )
            conn.execute("UPDATE settings SET value='0' WHERE key='ftp_stable_seconds'")

    def _write(self, name: str, content: bytes = b"/system identity set name=test\n") -> None:
        with connect() as conn:
            path = account_paths(conn, self.account["uuid"]).incoming
            path.mkdir(parents=True, exist_ok=True)
            (path / name).write_bytes(content)

    def _scan_twice(self, now: datetime | None = None):
        scan_once(now=now)
        return scan_once(now=(now + timedelta(seconds=1)) if now else None)

    def _enable_artifacts(self, backup_format: str = "both") -> None:
        with connect() as conn:
            conn.execute("UPDATE settings SET value='1' WHERE key='backup_artifacts_v2_enabled'")
            conn.execute("UPDATE mikrotik_ftp_integrations SET backup_format=? WHERE id=?",
                         (backup_format, self.integration["id"]))

    @staticmethod
    def _binary(seed: int = 0) -> bytes:
        return bytes((index * 37 + seed) % 256 for index in range(512))

    def test_artifacts_v2_disabled_creates_no_aggregate_rows(self) -> None:
        self._write("backup.router-core.2026-07-13.020000.rsc")
        self._write("backup.router-core.2026-07-13.020000.backup", b"binary backup")
        self._scan_twice(datetime.now(timezone.utc))
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backup_operations").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backup_operation_artifacts").fetchone()[0], 0)

    def test_artifacts_v2_legacy_name_has_conservative_correlation(self) -> None:
        self._enable_artifacts("both")
        now = datetime.now(timezone.utc)
        base = "backup.router-core.2026-07-13.020030"
        content = b"# RouterOS 7\n/system identity set name=legacy\n"
        self._write(base + ".rsc", content)
        self._scan_twice(now)
        with connect() as conn:
            operation = conn.execute("SELECT * FROM backup_operations").fetchone()
            self.assertEqual(operation["correlation_key"], f"i{self.integration['id']}:legacy-20260713020030")
            self.assertEqual(operation["status"], "waiting_upload")
        self._write(base + ".rsc", content)
        self._scan_twice(now + timedelta(seconds=2))
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backup_operations").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT state FROM backup_operation_artifacts WHERE artifact_type='rsc'").fetchone()[0], "valid")

    def test_artifact_interface_is_read_only_sanitized_and_opt_in(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "backup_manager" / "equipment_detail_panels.py").read_text(encoding="utf-8")
        panel = source[source.index('artifact_rows = "".join'):source.index("return f", source.index('artifact_rows = "".join'))]
        self.assertIn("if artifacts_v2", panel)
        self.assertNotIn("sha256", panel)
        self.assertNotIn("relative_path", panel)
        self.assertNotIn("final_path", panel)
        self.assertNotIn("<form", panel)
        self.assertNotIn("validation_metadata_json", panel)

    def test_artifacts_v2_both_arrival_orders_and_aggregate_notification(self) -> None:
        self._enable_artifacts("both")
        now = datetime.now(timezone.utc)
        base = "backup.router-core.2026-07-13.020000.xabcdef123456-20260713020000-1"
        self._write(base + ".rsc", b"# RouterOS 7\n/system identity set name=test\n")
        with mock.patch("backup_manager.cloud_sync.enqueue_new_backup") as cloud_enqueue, \
             mock.patch("backup_manager.telegram_backup.enqueue_new_backup") as telegram_enqueue:
            first = self._scan_twice(now)
            self.assertEqual((cloud_enqueue.call_count, telegram_enqueue.call_count), (0, 0))
            with connect() as conn:
                operation = conn.execute("SELECT * FROM backup_operations").fetchone()
                states = dict(conn.execute("SELECT artifact_type,state FROM backup_operation_artifacts"))
                self.assertEqual(operation["status"], "waiting_upload")
                self.assertEqual(states, {"backup": "expected", "rsc": "valid"})
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='backup.created_from_ftp'").fetchone()[0], 0)
            self._write(base + ".backup", self._binary())
            second = self._scan_twice(now + timedelta(seconds=2))
            self.assertEqual((cloud_enqueue.call_count, telegram_enqueue.call_count), (2, 2))
            cloud_ids = {call.args[1] for call in cloud_enqueue.call_args_list}
            telegram_ids = {call.args[1] for call in telegram_enqueue.call_args_list}
            self.assertEqual(cloud_ids, telegram_ids)
        self.assertEqual(first.imported, 1)
        self.assertEqual(second.imported, 1)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM backup_operations").fetchone()[0], "success")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='backup.created_from_ftp'").fetchone()[0], 2)

    def test_artifacts_v2_single_formats_validate(self) -> None:
        for backup_format, suffix, content in (
            ("backup", "backup", self._binary()),
            ("rsc", "rsc", b"# RouterOS 6\n/ip address add address=192.0.2.1/32\n"),
        ):
            with self.subTest(backup_format=backup_format):
                self._enable_artifacts(backup_format)
                base = f"backup.router-core.2026-07-13.02000{1 if suffix == 'backup' else 2}.xabcdef123456-2026071302000{1 if suffix == 'backup' else 2}-1"
                self._write(base + "." + suffix, content)
                self._scan_twice(datetime.now(timezone.utc))
                with connect() as conn:
                    self.assertEqual(conn.execute("SELECT status FROM backup_operations ORDER BY id DESC LIMIT 1").fetchone()[0], "success")
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM backup_operation_artifacts WHERE operation_id=(SELECT MAX(id) FROM backup_operations)").fetchone()[0], 1)

    def test_artifacts_v2_rejects_suspicious_and_html_without_storing(self) -> None:
        self._enable_artifacts("rsc")
        cases = (
            ("020010", b"# RouterOS 7\n/user add name=admin password=secret\n", "suspicious"),
            ("020011", b"<html><body>login</body></html>", "invalid"),
        )
        for clock, content, expected in cases:
            base = f"backup.router-core.2026-07-13.{clock}.xabcdef123456-20260713{clock}-1"
            self._write(base + ".rsc", content)
            self._scan_twice(datetime.now(timezone.utc))
            with connect() as conn:
                row = conn.execute("SELECT state,validation_message FROM backup_operation_artifacts ORDER BY id DESC LIMIT 1").fetchone()
                self.assertEqual(row["state"], expected)
                self.assertNotIn("secret", row["validation_message"].lower())
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0], 0)

    def test_artifacts_v2_idempotent_retry_does_not_duplicate_or_fail(self) -> None:
        self._enable_artifacts("backup")
        now = datetime.now(timezone.utc)
        name = "backup.router-core.2026-07-13.020020.xabcdef123456-20260713020020-1.backup"
        content = self._binary(3)
        self._write(name, content)
        self._scan_twice(now)
        self._write(name, content)
        with mock.patch("backup_manager.cloud_sync.enqueue_new_backup") as cloud_enqueue, \
             mock.patch("backup_manager.telegram_backup.enqueue_new_backup") as telegram_enqueue:
            self._scan_twice(now + timedelta(seconds=2))
            self.assertEqual((cloud_enqueue.call_count, telegram_enqueue.call_count), (0, 0))
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT status FROM backup_operations").fetchone()[0], "success")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ftp_received_files WHERE status='duplicate'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='backup.created_from_ftp'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='ftp.upload_idempotent'").fetchone()[0], 0)

    def test_artifacts_v2_conflict_preserves_original_backup(self) -> None:
        self._enable_artifacts("backup")
        now = datetime.now(timezone.utc)
        name = "backup.router-core.2026-07-13.020021.xabcdef123456-20260713020021-1.backup"
        self._write(name, self._binary(4))
        self._scan_twice(now)
        with connect() as conn:
            backup = conn.execute("SELECT id,relative_path,sha256 FROM backups").fetchone()
            original = backup["relative_path"]
        self._write(name, self._binary(5))
        self._scan_twice(now + timedelta(seconds=2))
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT status FROM backup_operations").fetchone()[0], "failed")
            artifact = conn.execute("SELECT state,sha256,backup_id,validation_code FROM backup_operation_artifacts").fetchone()
            self.assertEqual((artifact["state"], artifact["sha256"], artifact["backup_id"]),
                             ("suspicious", backup["sha256"], backup["id"]))
            self.assertEqual(artifact["validation_code"], "artifact_content_conflict")
            config = load_config(conn)
            self.assertTrue((config.backup_directory / original).exists())

    def test_artifacts_v2_same_hash_other_operation_is_valid(self) -> None:
        self._enable_artifacts("rsc")
        now = datetime.now(timezone.utc)
        content = b"# RouterOS 7\n/system identity set name=unchanged\n"
        for clock in ("020022", "020023"):
            name = f"backup.router-core.2026-07-13.{clock}.xabcdef123456-20260713{clock}-1.rsc"
            self._write(name, content)
            self._scan_twice(now)
            now += timedelta(seconds=2)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backup_operations").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backup_operation_artifacts WHERE state='valid'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backup_operations WHERE status='success'").fetchone()[0], 2)

    def test_artifacts_v2_both_accepts_unchanged_rsc_in_new_operation(self) -> None:
        self._enable_artifacts("both")
        now = datetime.now(timezone.utc)
        rsc = b"# RouterOS 7\n/system identity set name=unchanged\n"
        for sequence in (1, 2):
            base = f"backup.router-core.2026-07-13.020026.xabcdef123456-20260713020026-{sequence}"
            self._write(base + ".rsc", rsc)
            self._write(base + ".backup", self._binary(10 + sequence))
            self._scan_twice(now)
            now += timedelta(seconds=2)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backup_operations WHERE status='success'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(DISTINCT correlation_key) FROM backup_operations").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backup_operation_artifacts WHERE state='duplicate'").fetchone()[0], 0)

    def test_artifacts_v2_rsc_is_byte_identical_after_storage(self) -> None:
        self._enable_artifacts("rsc")
        content = b"# RouterOS 7\r\n/interface bridge\r\nadd name=lab\r\n"
        name = "backup.router-core.2026-07-13.020027.xabcdef123456-20260713020027-1.rsc"
        before = hashlib.sha256(content).hexdigest()
        self._write(name, content)
        self._scan_twice(datetime.now(timezone.utc))
        with connect() as conn:
            backup = conn.execute("SELECT * FROM backups").fetchone()
            stored = (load_config(conn).backup_directory / backup["relative_path"]).read_bytes()
            artifact = conn.execute("SELECT sha256 FROM backup_operation_artifacts").fetchone()[0]
        self.assertEqual(stored, content)
        self.assertEqual((hashlib.sha256(stored).hexdigest(), artifact), (before, before))

    def test_artifacts_v2_late_is_stored_without_reopening_operation(self) -> None:
        self._enable_artifacts("both")
        now = datetime.now(timezone.utc)
        base = "backup.router-core.2026-07-13.020024.xabcdef123456-20260713020024-1"
        self._write(base + ".rsc", b"# RouterOS 7\n/system identity set name=test\n")
        self._scan_twice(now)
        with connect() as conn:
            conn.execute("UPDATE backup_operations SET deadline_at=?", ((now - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"),))
            conn.execute("UPDATE backup_operation_artifacts SET deadline_at=?", ((now - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"),))
        scan_once(now=now + timedelta(seconds=2))
        self._write(base + ".backup", self._binary(7))
        self._scan_twice(now + timedelta(seconds=3))
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM backup_operations").fetchone()[0], "expired")
            states = dict(conn.execute("SELECT artifact_type,state FROM backup_operation_artifacts"))
            self.assertEqual(states, {"backup": "late", "rsc": "valid"})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='backup.created_from_ftp'").fetchone()[0], 0)

    def test_artifacts_v2_two_importer_processes_are_idempotent(self) -> None:
        self._enable_artifacts("backup")
        now = datetime.now(timezone.utc)
        name = "backup.router-core.2026-07-13.020025.xabcdef123456-20260713020025-1.backup"
        self._write(name, self._binary(8))
        scan_once(now=now)
        context = multiprocessing.get_context("fork")
        queue = context.Queue()
        processes = [context.Process(target=concurrent_scan_worker, args=((now + timedelta(seconds=2)).isoformat(), queue))
                     for _ in range(2)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        outcomes = [queue.get(timeout=2) for _ in processes]
        self.assertTrue(all(item[0] == "ok" for item in outcomes), outcomes)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backup_operations").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backup_operation_artifacts").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0], 1)

    def test_real_upload_stabilizes_hashes_moves_and_completes(self) -> None:
        self._write("backup.router-core.2026-07-13.020000.rsc")
        self._write("backup.router-core.2026-07-13.020000.backup", b"binary backup")
        result = self._scan_twice(datetime.now(timezone.utc))
        self.assertEqual(result.imported, 2)
        with connect() as conn:
            upload = conn.execute("SELECT * FROM mikrotik_ftp_uploads").fetchone()
            backup = conn.execute("SELECT * FROM backups").fetchone()
            self.assertEqual((upload["status"], len(upload["sha256"]), backup["backup_status"]), ("success", 64, "available"))
            self.assertTrue((Path(os.environ["BACKUP_MANAGER_STORAGE_ROOT"]) / upload["final_path"]).exists() is False)
            self.assertTrue((Path(os.environ["BACKUP_MANAGER_STORAGE_ROOT"]) / "backups" / backup["relative_path"]).exists())

    def test_short_v51_filename_is_imported(self) -> None:
        self._write("router-core.2026-07-19-12-27-11.1.rsc")
        self._write("router-core.2026-07-19-12-27-11.1.backup", b"binary backup")
        result = self._scan_twice(datetime.now(timezone.utc))
        self.assertEqual(result.imported, 2)
        with connect() as conn:
            names = {row[0] for row in conn.execute("SELECT original_filename FROM backups")}
        self.assertEqual(names, {"router-core.2026-07-19-12-27-11.1.rsc", "router-core.2026-07-19-12-27-11.1.backup"})

    def test_test_token_success_expiry_and_no_backup(self) -> None:
        now = datetime.now(timezone.utc)
        with connect() as conn:
            test = create_test(conn, self.integration["id"], now=now)
        self._write("backup-manager-test-42.rsc")
        self._scan_twice(now)
        with connect() as conn:
            state = conn.execute("SELECT status FROM mikrotik_ftp_tests WHERE id=?", (test["id"],)).fetchone()[0]
            self.assertEqual(state, "validated")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0], 0)
            late = create_test(conn, self.integration["id"], now=now - timedelta(hours=1))
            expired = expire_test(conn, late["id"], now=now)
            self.assertEqual(expired["status"], "expired")
            self.assertEqual(expired["error_code"], "upload_timeout")
            self.assertIsNotNone(expired["completed_at"])

    def test_second_test_for_same_equipment_is_blocked(self) -> None:
        with connect() as conn:
            create_test(conn, self.integration["id"])
            with self.assertRaisesRegex(ValueError, "Já existe um teste FTP"):
                create_test(conn, self.integration["id"])

    def test_test_arrives_before_operation_is_visible_and_missing_token_retries(self) -> None:
        now = datetime.now(timezone.utc)
        self._write("backup-manager-test-42.rsc")
        first = scan_once(now=now)
        self.assertEqual(first.detected, 1)
        with connect() as conn:
            test = create_test(conn, self.integration["id"], now=now + timedelta(seconds=1))
        result = scan_once(now=now + timedelta(seconds=2))
        self.assertEqual((result.ignored_unmanaged, result.rejected), (1, 0))

    def test_invalid_names_wrong_device_token_empty_large_and_duplicate(self) -> None:
        cases = [
            ("../evil.rsc", b"x", "ignored_unmanaged"),
            ("backup.BadIdentity.2026-07-13.020000.rsc", b"x", "filename_invalid"),
            ("backup.router.2026-07-13.020001.exe", b"x", "invalid_filename"),
        ]
        for name, content, code in cases:
            safe_name = Path(name).name
            self._write(safe_name, content)
            self._scan_twice()
            with connect() as conn:
                row = conn.execute("SELECT error_code FROM ftp_received_files ORDER BY id DESC LIMIT 1").fetchone()
                self.assertEqual(row["error_code"], code)
        with connect() as conn:
            conn.execute("UPDATE settings SET value='2' WHERE key='ftp_max_upload_size'")
        self._write("backup.router.2026-07-13.020002.rsc", b"too big")
        self._scan_twice()
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT error_code FROM ftp_received_files ORDER BY id DESC LIMIT 1").fetchone()[0], "file_too_large")

    def test_expired_token_account_mismatch_malformed_date_and_pair_wait(self) -> None:
        now = datetime.now(timezone.utc)
        with connect() as conn:
            expired = create_test(conn, self.integration["id"], now=now - timedelta(hours=1))
        self._write("backup-manager-test-42.rsc")
        self._scan_twice(now)
        with connect() as conn:
            row = conn.execute("SELECT error_code FROM ftp_received_files ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(row["error_code"], "late_test_file")
            self.assertEqual(conn.execute("SELECT status FROM mikrotik_ftp_tests WHERE id=?", (expired["id"],)).fetchone()[0], "expired")
            conn.execute("INSERT INTO equipment(id,name,hostname,ip_address,vendor_id,is_active) SELECT 99,'Other','other','192.0.2.99',vendor_id,1 FROM equipment WHERE id=42")
        self._write("backup-manager-test-99.rsc")
        self._scan_twice(now + timedelta(seconds=2))
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT error_code FROM ftp_received_files ORDER BY id DESC LIMIT 1").fetchone()[0], "account_mismatch")

        self._write("backup.router.-14-01--0.144114.rsc")
        self._scan_twice(now + timedelta(seconds=4))
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT error_code FROM ftp_received_files ORDER BY id DESC LIMIT 1").fetchone()[0], "date_invalid")

        self._write("backup.router.2026-07-14.144115.rsc")
        scan_once(now=now + timedelta(seconds=6))
        waiting = scan_once(now=now + timedelta(seconds=7))
        self.assertEqual((waiting.waiting_pair, waiting.imported, waiting.rejected), (1, 0, 0))

    def test_rotation_changes_ciphertext_without_logging_password(self) -> None:
        with connect() as conn:
            before = conn.execute("SELECT encrypted_ftp_secret FROM mikrotik_ftp_integrations WHERE id=?", (self.integration["id"],)).fetchone()[0]
            _, password, after = rotate_credential(conn, self.integration["id"])
            self.assertNotEqual(before, after)
            self.assertNotIn(password, str(conn.execute("SELECT details FROM audit_log").fetchall()))
            cfg = integration_config(conn, conn.execute("SELECT * FROM mikrotik_ftp_integrations WHERE id=?", (self.integration["id"],)).fetchone(), include_secret=True)
            self.assertEqual(cfg.ftp_password, password)

    def test_importer_expires_active_tests_idempotently_and_releases_equipment(self) -> None:
        now = datetime.now(timezone.utc)
        with connect() as conn:
            old = create_test(conn, self.integration["id"], now=now - timedelta(hours=1))
        first = scan_once(now=now)
        second = scan_once(now=now + timedelta(seconds=1))
        self.assertEqual((first.expired_tests, second.expired_tests), (1, 0))
        with connect() as conn:
            row = conn.execute("SELECT * FROM mikrotik_ftp_tests WHERE id=?", (old["id"],)).fetchone()
            self.assertEqual((row["status"], row["error_code"]), ("expired", "upload_timeout"))
            replacement = create_test(conn, self.integration["id"], now=now)
            self.assertEqual(replacement["expected_filename"], "backup-manager-test-42.rsc")

    def test_repeated_identical_test_files_validate_and_are_cleaned(self) -> None:
        for _ in range(2):
            now = datetime.now(timezone.utc)
            with connect() as conn:
                test = create_test(conn, self.integration["id"], now=now - timedelta(seconds=1))
            self._write("backup-manager-test-42.rsc")
            result = self._scan_twice(now)
            self.assertEqual(result.validated_test, 1)
            with connect() as conn:
                row = conn.execute("SELECT status FROM mikrotik_ftp_tests WHERE id=?", (test["id"],)).fetchone()
                self.assertEqual(row["status"], "validated")
                path = account_paths(conn, self.account["uuid"]).processing
                self.assertFalse(any(path.iterdir()))


if __name__ == "__main__":
    unittest.main()
