import hashlib
import re
import sqlite3
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backup_manager.artifact_validators import MAX_TEXT_SCAN_BYTES, RouterOSBinaryBackupValidator, RouterOSExportValidator
from backup_manager.artifacts import ARTIFACT_STATES, OPERATION_STATES, ArtifactSnapshot, reduce_operation_state

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
MIGRATION = MIGRATIONS / "028_backup_operations_artifacts.sql"


def schema(through: str = "999") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,applied_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name > through:
            break
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES(?)", (path.stem,))
    return conn


class BackupArtifactMigrationTests(unittest.TestCase):
    def test_migration_from_clean_database_and_integrity(self):
        conn = schema()
        try:
            self.assertTrue(conn.execute("SELECT 1 FROM sqlite_master WHERE name='backup_operations'").fetchone())
            self.assertTrue(conn.execute("SELECT 1 FROM sqlite_master WHERE name='backup_operation_artifacts'").fetchone())
            self.assertEqual("0", conn.execute("SELECT value FROM settings WHERE key='backup_artifacts_v2_enabled'").fetchone()[0])
            self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
        finally:
            conn.close()

    def test_python_and_database_states_are_exactly_equivalent(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        artifact_check = re.search(r"CHECK\(state IN \((.*?)\)\)", sql, re.DOTALL)
        operation_check = re.search(r"CHECK\(status IN \((.*?)\)\)", sql, re.DOTALL)
        self.assertIsNotNone(artifact_check)
        self.assertIsNotNone(operation_check)
        parse = lambda match: frozenset(re.findall(r"'([^']+)'", match.group(1)))
        self.assertEqual(ARTIFACT_STATES, parse(artifact_check))
        self.assertEqual(OPERATION_STATES, parse(operation_check))

    def test_migration_over_current_schema_preserves_existing_rows_and_has_no_backfill(self):
        conn = schema("027_telegram_destination_schema_reconcile.sql")
        try:
            equipment = conn.execute("INSERT INTO equipment(hostname,ip_address) VALUES('legacy','192.0.2.1')").lastrowid
            backup_uuid = str(uuid.uuid4())
            conn.execute("""INSERT INTO backups(uuid,equipment_id,original_filename,stored_filename,relative_path,
                         file_size,sha256,backup_status) VALUES(?,?,'legacy.backup','legacy.backup','legacy',1,?,'available')""",
                         (backup_uuid, equipment, "a" * 64))
            before = conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0]
            conn.executescript(MIGRATION.read_text(encoding="utf-8"))
            self.assertEqual(before, conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM backup_operations").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM backup_operation_artifacts").fetchone()[0])
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
        finally:
            conn.close()

    def test_migration_rolls_back_transactionally_on_failure(self):
        conn = schema("027_telegram_destination_schema_reconcile.sql")
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript("BEGIN IMMEDIATE;\n" + MIGRATION.read_text(encoding="utf-8")
                                   + "\nINSERT INTO table_that_does_not_exist VALUES(1);\nCOMMIT;")
            conn.rollback()
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='backup_operations'").fetchone())
            self.assertFalse(conn.execute("SELECT 1 FROM settings WHERE key='backup_artifacts_v2_enabled'").fetchone())
        finally:
            conn.close()

    def test_operation_and_artifact_uniqueness_all_states_and_optional_backup_link(self):
        conn = schema()
        try:
            equipment = conn.execute("INSERT INTO equipment(hostname,ip_address) VALUES('router','192.0.2.2')").lastrowid
            operation_uuid = str(uuid.uuid4())
            operation = conn.execute("""INSERT INTO backup_operations
                (uuid,provider_key,equipment_id,correlation_key,deadline_at)
                VALUES(?,'mikrotik_ftp',?,'router.2026-07-16.120000','2026-07-16 12:05:00')""",
                (operation_uuid, equipment)).lastrowid
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT INTO backup_operations
                    (uuid,provider_key,equipment_id,correlation_key,deadline_at)
                    VALUES(?,'mikrotik_ftp',?,'router.2026-07-16.120000','2026-07-16 12:05:00')""",
                    (str(uuid.uuid4()), equipment))
            for index, state in enumerate(sorted(ARTIFACT_STATES)):
                conn.execute("""INSERT INTO backup_operation_artifacts
                    (uuid,operation_id,artifact_type,is_required,state)
                    VALUES(?,?,?,?,?)""", (str(uuid.uuid4()), operation, f"type-{index}", index % 2, state))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT INTO backup_operation_artifacts
                    (uuid,operation_id,artifact_type,state) VALUES(?,?,?,'expected')""",
                    (str(uuid.uuid4()), operation, "type-0"))
            backup = conn.execute("""INSERT INTO backups(uuid,equipment_id,original_filename,stored_filename,
                relative_path,file_size,sha256,backup_status) VALUES(?,?,'x.rsc','x.rsc','x',1,?,'available')""",
                (str(uuid.uuid4()), equipment, "b" * 64)).lastrowid
            conn.execute("UPDATE backup_operation_artifacts SET backup_id=? WHERE operation_id=? AND artifact_type='type-0'",
                         (backup, operation))
            self.assertEqual(len(ARTIFACT_STATES), conn.execute(
                "SELECT COUNT(*) FROM backup_operation_artifacts WHERE operation_id=?", (operation,)).fetchone()[0])
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
        finally:
            conn.close()


class OperationReducerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
        self.deadline = self.now + timedelta(minutes=5)

    def reduce(self, *items, now=None):
        return reduce_operation_state(items, deadline=self.deadline, now=now or self.now)

    def test_required_optional_matrix_and_arrival_order(self):
        binary = ArtifactSnapshot("routeros_backup", True, "valid")
        export = ArtifactSnapshot("routeros_export", True, "valid")
        optional = ArtifactSnapshot("diagnostic", False, "expected")
        self.assertEqual("success", self.reduce(binary, export, optional).status)
        self.assertEqual("success", self.reduce(export, optional, binary).status)
        self.assertEqual("success", self.reduce(binary, optional).status)
        self.assertEqual("success", self.reduce(optional).status)
        self.assertEqual("success", self.reduce().status)
        self.assertEqual("waiting_upload", self.reduce(binary, ArtifactSnapshot("routeros_export", True, "waiting")).status)
        self.assertEqual("validating", self.reduce(binary, ArtifactSnapshot("routeros_export", True, "received")).status)

    def test_deadline_invalid_suspicious_duplicate_expired_and_late(self):
        for state in ("invalid", "suspicious", "duplicate"):
            with self.subTest(state=state):
                result = self.reduce(ArtifactSnapshot("routeros_export", True, state))
                self.assertEqual(("failed", "required_artifact_rejected"), (result.status, result.code))
        for state in ("expired", "late"):
            with self.subTest(state=state):
                self.assertEqual("expired", self.reduce(ArtifactSnapshot("routeros_export", True, state)).status)
        after = self.deadline + timedelta(seconds=1)
        self.assertEqual("expired", self.reduce(ArtifactSnapshot("routeros_export", True, "waiting"), now=after).status)
        self.assertEqual("expired", self.reduce(ArtifactSnapshot("routeros_export", True, "validating"), now=after).status)

    def test_all_states_are_accepted_and_reprocessing_is_idempotent(self):
        for state in ARTIFACT_STATES:
            ArtifactSnapshot("artifact", False, state)
        items = (ArtifactSnapshot("routeros_backup", True, "valid"),
                 ArtifactSnapshot("routeros_export", True, "valid"))
        self.assertEqual(self.reduce(*items), self.reduce(*items))
        with self.assertRaises(ValueError):
            ArtifactSnapshot("artifact", True, "unknown")
        with self.assertRaises(ValueError):
            reduce_operation_state(items, deadline=self.deadline.replace(tzinfo=None), now=self.now)


class ArtifactValidatorTests(unittest.TestCase):
    def validate_bytes(self, validator, content: bytes, **kwargs):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "artifact"
            path.write_bytes(content)
            return validator.validate(path, **kwargs)

    def test_routeros_export_valid_empty_html_login_error_and_incompatible(self):
        validator = RouterOSExportValidator()
        valid = self.validate_bytes(validator, b"# jul/16/2026 by RouterOS 7.15\n/interface bridge\nadd name=bridge1\n")
        self.assertEqual("valid", valid.state)
        cases = (
            (b"", "empty_output"),
            (b"<!doctype html><html><body>error</body></html>", "html_content"),
            (b"Username: admin\nPassword:\nLogin", "login_page"),
            (b"# RouterOS\nerror: authentication failed", "command_error"),
            (b"ordinary text without router configuration", "incompatible_export"),
        )
        for content, code in cases:
            with self.subTest(code=code):
                self.assertEqual(code, self.validate_bytes(validator, content).code)

    def test_routeros_export_sensitive_patterns_are_suspicious_and_redacted(self):
        secret = "NeverReturnThisSecret"
        content = ("# RouterOS 7\n/user add name=admin group=full password=" + secret
                   + "\n/system script add name=x source=y\n/tool fetch url=https://example.invalid token=" + secret).encode()
        result = self.validate_bytes(RouterOSExportValidator(), content)
        self.assertEqual(("suspicious", "sensitive_commands"), (result.state, result.code))
        rendered = repr(result)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("/user add", rendered)
        self.assertEqual({"sensitive_pattern_count", "scanned_bytes"}, set(result.metadata))

    def test_binary_empty_small_html_text_valid_and_hash(self):
        validator = RouterOSBinaryBackupValidator(minimum_bytes=128)
        self.assertEqual("empty_backup", self.validate_bytes(validator, b"").code)
        self.assertEqual("backup_too_small", self.validate_bytes(validator, b"\x00" * 32).code)
        self.assertEqual("html_content", self.validate_bytes(validator, b"<html>login</html>" + b" " * 200).code)
        self.assertEqual("text_content", self.validate_bytes(validator, b"RouterOS textual export\n" * 20).code)
        binary = bytes(range(256)) * 4
        valid = self.validate_bytes(validator, binary)
        self.assertEqual(("valid", hashlib.sha256(binary).hexdigest()), (valid.state, valid.sha256))
        mismatch = self.validate_bytes(validator, binary, expected_sha256="0" * 64)
        self.assertEqual("sha256_mismatch", mismatch.code)
        matched = self.validate_bytes(validator, binary, expected_sha256=hashlib.sha256(binary).hexdigest())
        self.assertEqual("valid", matched.state)

    def test_validation_is_idempotent_and_results_do_not_contain_content(self):
        content = b"# RouterOS\n/interface ethernet\nset [find default-name=ether1] disabled=no\n"
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "config.rsc"
            path.write_bytes(content)
            validator = RouterOSExportValidator()
            first = validator.validate(path)
            second = validator.validate(path)
        self.assertEqual(first, second)
        self.assertNotIn(content.decode(), repr(first))

    def test_text_scan_memory_is_bounded_while_hash_covers_the_entire_file(self):
        content = b"# RouterOS\n/interface bridge\n" + b"# filler\n" * (MAX_TEXT_SCAN_BYTES // 4)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "large.rsc"
            path.write_bytes(content)
            result = RouterOSExportValidator().validate(path)
        self.assertEqual("valid", result.state)
        self.assertEqual(MAX_TEXT_SCAN_BYTES, result.metadata["scanned_bytes"])
        self.assertTrue(result.metadata["truncated_scan"])
        self.assertEqual(hashlib.sha256(content).hexdigest(), result.sha256)


if __name__ == "__main__":
    unittest.main()
