import ast
import re
import sqlite3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SimplificationCharacterizationTests(unittest.TestCase):
    """Freeze the pre-refactor entry points and FTP state vocabulary."""

    def test_cli_legacy_commands_remain_discoverable_during_transition(self) -> None:
        source = (ROOT / "backup_manager" / "cli.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        choices = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant) or node.args[0].value != "command":
                continue
            choices_node = next((item.value for item in node.keywords if item.arg == "choices"), None)
            choices = ast.literal_eval(choices_node)
            break
        self.assertIsNotNone(choices)
        self.assertEqual(56, len(choices))
        for command in (
            "reset-admin-password", "integrity-check", "ftp-config-check",
            "ftp-accounts-reconcile", "ftp-scan-once", "ssh-backup",
            "lifecycle-simulate", "update-check", "update-apply",
        ):
            self.assertIn(command, choices)

    def test_current_ftp_state_vocabularies_are_explicitly_characterized(self) -> None:
        migrations = {
            "received": ("008_ftp_push.sql", "status IN"),
            "tests": ("023_mikrotik_test_lifecycle.sql", "status IN"),
            "uploads": ("019_mikrotik_ftp_push.sql", "status IN"),
            "backups": ("002_backups_storage.sql", "backup_status IN"),
        }
        expected = {
            "received": {"detected", "waiting_stable", "processing", "imported", "rejected", "failed", "duplicate"},
            "tests": {"pending", "running", "waiting_upload", "validating", "validated", "failed", "expired", "cancelled"},
            "uploads": {"receiving", "validating", "success", "failed", "timeout", "incomplete", "invalid_file", "duplicate"},
            "backups": {"receiving", "validating", "available", "quarantined", "failed", "trashed", "deleted"},
        }
        for name, (filename, marker) in migrations.items():
            source = (ROOT / "migrations" / filename).read_text(encoding="utf-8")
            fragments = [line for line in source.splitlines() if marker in line]
            values = set(re.findall(r"'([a-z_]+)'", " ".join(fragments)))
            if name == "uploads":
                values = {value for value in values if value in expected[name]}
            self.assertEqual(expected[name], values, name)

    def test_operation_and_artifact_state_reducers_are_separate_contracts(self) -> None:
        source = (ROOT / "backup_manager" / "artifacts.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        constants = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            if node.targets[0].id not in {"ARTIFACT_STATES", "OPERATION_STATES"}:
                continue
            call = node.value
            self.assertIsInstance(call, ast.Call)
            constants[node.targets[0].id] = set(ast.literal_eval(call.args[0]))
        self.assertEqual({"waiting_upload", "validating", "success", "failed", "expired"}, constants["OPERATION_STATES"])
        self.assertEqual(10, len(constants["ARTIFACT_STATES"]))
        self.assertNotEqual(constants["OPERATION_STATES"], constants["ARTIFACT_STATES"])

    def test_schema_declares_restrictive_ftp_history_relationships(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys=ON")
        # This characterization deliberately reads migration definitions rather
        # than applying them to the operational database.
        account_migration = (ROOT / "migrations" / "024_ftp_account_lifecycle.sql").read_text(encoding="utf-8")
        push_migration = (ROOT / "migrations" / "019_mikrotik_ftp_push.sql").read_text(encoding="utf-8")
        self.assertIn("ftp_account_id INTEGER NOT NULL REFERENCES ftp_accounts(id) ON DELETE RESTRICT", account_migration)
        self.assertIn("integration_id INTEGER NOT NULL REFERENCES mikrotik_ftp_integrations(id) ON DELETE RESTRICT", push_migration)
        self.assertIn("received_file_id INTEGER UNIQUE REFERENCES ftp_received_files(id) ON DELETE RESTRICT", push_migration)
        conn.close()

    def test_cli_does_not_own_domain_listing_sql(self) -> None:
        source = (ROOT / "backup_manager" / "cli.py").read_text(encoding="utf-8")
        for table in (
            "cloud_targets", "cloud_sync_policies", "cloud_sync_items",
            "telegram_destinations", "telegram_backup_items", "update_operations",
        ):
            self.assertNotIn(f"FROM {table}", source, table)


if __name__ == "__main__":
    unittest.main()
