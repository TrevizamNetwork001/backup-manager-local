import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from backup_manager import app
from backup_manager.db import (
    MigrationError,
    SchemaCompatibilityError,
    SchemaVersionStatus,
    _migration_lock,
    _sqlite_backup,
    check_schema_version,
    migrate,
    require_schema_current,
)

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def open_db(path, **kwargs):
    conn = sqlite3.connect(path, **kwargs)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def manifest(directory: Path, sql: dict[int, str], *, release: int | None = None) -> Path:
    rows = []
    for version, body in sorted(sql.items()):
        name = f"{version:03d}_test_{version}.sql"
        (directory / name).write_text(body, encoding="utf-8")
        rows.append({"version": version, "name": name,
                     "sha256": hashlib.sha256(body.encode()).hexdigest()})
    path = directory / "manifest.json"
    path.write_text(json.dumps({"release_version": release or max(sql), "migrations": rows}), encoding="utf-8")
    return path


def request(path: str, *, accept="application/json") -> tuple[int, bytes]:
    captured = {}
    def start(status, headers):
        captured["status"] = int(status.split()[0]); captured["headers"] = headers
    body = b"".join(app.application({"REQUEST_METHOD": "GET", "PATH_INFO": path,
                                      "QUERY_STRING": "", "HTTP_ACCEPT": accept,
                                      "wsgi.input": io.BytesIO(b"")}, start))
    return captured["status"], body


class SchemaReadOnlyTests(unittest.TestCase):
    def test_unittest_process_never_resolves_the_production_database(self):
        production_db = ROOT / "data" / "backup_manager.sqlite3"
        env = os.environ.copy()
        env.update({
            "BACKUP_MANAGER_HOME": str(ROOT),
            "BACKUP_MANAGER_DATA": str(ROOT / "data"),
            "BACKUP_MANAGER_DB": str(production_db),
        })
        probe = subprocess.run(
            [sys.executable, "-c", "from backup_manager.db import DB_PATH; print(DB_PATH)", "unittest-probe"],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        resolved = Path(probe.stdout.strip())
        self.assertNotEqual(production_db.resolve(), resolved.resolve())
        self.assertTrue(resolved.name == "test.sqlite3" and resolved.parent.name.startswith("backup-manager-unittest-"))

    def test_current_schema_checks_and_noop_migrate_do_not_modify_database(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); db = root / "db.sqlite3"
            migrate(31, _db_path=db, _data_dir=root, _lock_path=root / "lock")
            before = hashlib.sha256(db.read_bytes()).hexdigest()
            self.assertTrue(require_schema_current(db_path=db).ready)
            self.assertEqual((), migrate(31, _db_path=db, _data_dir=root,
                                         _lock_path=root / "lock").applied)
            self.assertEqual(before, hashlib.sha256(db.read_bytes()).hexdigest())

    def test_current_web_schema_check_is_read_only(self):
        from backup_manager import db as database
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); db = root / "db.sqlite3"
            migrate(31, _db_path=db, _data_dir=root, _lock_path=root / "lock")
            before = hashlib.sha256(db.read_bytes()).hexdigest()
            with mock.patch.object(database, "DB_PATH", db), mock.patch.object(database, "DATA_DIR", root):
                status, _ = request("/health/ready")
            self.assertEqual(200, status)
            self.assertEqual(before, hashlib.sha256(db.read_bytes()).hexdigest())
            self.assertFalse(Path(str(db) + "-wal").exists())
            self.assertFalse(Path(str(db) + "-shm").exists())

    def test_missing_database_check_does_not_create_database_or_sidecars(self):
        with tempfile.TemporaryDirectory() as raw:
            db = Path(raw) / "missing.sqlite3"
            self.assertEqual("SCHEMA_INVALID", check_schema_version(db_path=db).code)
            self.assertFalse(db.exists())
            self.assertFalse(Path(str(db) + "-wal").exists())
            self.assertFalse(Path(str(db) + "-shm").exists())

    def test_old_ahead_and_invalid_schema_have_controlled_codes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); old = root / "old.sqlite3"
            migrate(27, _db_path=old, _data_dir=root, _lock_path=root / "lock")
            self.assertEqual("SCHEMA_OUTDATED", check_schema_version(db_path=old).code)
            ahead = root / "ahead.sqlite3"
            with open_db(ahead) as conn:
                conn.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)")
                conn.execute("INSERT INTO schema_migrations VALUES('032_future')")
            self.assertEqual("SCHEMA_AHEAD", check_schema_version(db_path=ahead).code)
            unknown = root / "unknown.sqlite3"
            with open_db(unknown) as conn:
                conn.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)")
                conn.execute("INSERT INTO schema_migrations VALUES('legacy_unknown')")
            self.assertEqual("SCHEMA_INVALID", check_schema_version(db_path=unknown).code)
            invalid = root / "invalid.sqlite3"; sqlite3.connect(invalid).close()
            self.assertEqual("SCHEMA_INVALID", check_schema_version(db_path=invalid).code)

    def test_presence_of_extra_migration_never_changes_schema_during_check(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); db = root / "db.sqlite3"
            migrate(31, _db_path=db, _data_dir=root, _lock_path=root / "lock")
            extra = ROOT / "migrations" / "999_untracked_test.sql"
            try:
                extra.write_text("CREATE TABLE must_never_exist(id INTEGER);", encoding="utf-8")
                require_schema_current(db_path=db)
                with open_db(db) as conn:
                    self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='must_never_exist'").fetchone())
            finally:
                extra.unlink(missing_ok=True)


class WebAndEntrypointTests(unittest.TestCase):
    def test_web_requests_do_not_migrate_and_maintenance_is_sanitized(self):
        self.assertFalse(hasattr(app, "migrate"))
        with mock.patch.object(app, "require_schema_current") as required, \
             mock.patch.object(app, "connect", side_effect=AssertionError("database should not open")):
            required.side_effect = SchemaCompatibilityError("SCHEMA_OUTDATED")
            for _ in range(2):
                status, body = request("/")
                self.assertEqual(503, status)
                self.assertIn(b"SCHEMA_OUTDATED", body)
                self.assertNotIn(str(ROOT).encode(), body)

    def test_liveness_remains_up_and_readiness_reflects_schema(self):
        status, body = request("/health/live")
        self.assertEqual(200, status); self.assertIn(b'"live":true', body)
        with mock.patch.object(app, "check_schema_version",
                               return_value=SchemaVersionStatus(28, 29, "SCHEMA_OUTDATED")):
            status, body = request("/health/ready")
        self.assertEqual(503, status); self.assertIn(b"SCHEMA_OUTDATED", body)
        with mock.patch.object(app, "require_schema_current",
                               side_effect=SchemaCompatibilityError("SCHEMA_OUTDATED")):
            status, _ = request("/static/app.css", accept="text/css")
        self.assertEqual(200, status)

    def test_workers_and_importers_guard_schema_without_processing(self):
        modules = (
            "backup_manager.worker", "backup_manager.ftp_importer", "backup_manager.lifecycle_runner",
            "backup_manager.cloud_worker", "backup_manager.telegram_worker",
        )
        for name in modules:
            with self.subTest(module=name):
                module = __import__(name, fromlist=["main"])
                self.assertFalse(hasattr(module, "migrate"))
                with mock.patch.object(module, "require_schema_current",
                                       side_effect=SchemaCompatibilityError("SCHEMA_OUTDATED")):
                    with self.assertRaises(SystemExit) as stopped:
                        module.main()
                    self.assertEqual(3, stopped.exception.code)


class ExplicitMigratorTests(unittest.TestCase):
    def mini(self, raw: str, bodies=None):
        root = Path(raw); migrations = root / "migrations"; migrations.mkdir()
        bodies = bodies or {1: "CREATE TABLE one(id INTEGER);", 2: "CREATE TABLE two(id INTEGER);"}
        return root, migrations, manifest(migrations, bodies)

    def migrate_mini(self, root, migrations, manifest_path, target, **kwargs):
        return migrate(target, _db_path=root / "db.sqlite3", _data_dir=root,
                       _lock_path=root / "migration.lock", _migrations_dir=migrations,
                       _manifest_path=manifest_path, _seed=False, **kwargs)

    def test_explicit_target_stops_before_later_migration_and_cli_requires_target(self):
        with tempfile.TemporaryDirectory() as raw:
            root, migrations, manifest_path = self.mini(raw)
            result = self.migrate_mini(root, migrations, manifest_path, 1)
            self.assertEqual((1,), result.applied)
            with open_db(root / "db.sqlite3") as conn:
                self.assertTrue(conn.execute("SELECT 1 FROM sqlite_master WHERE name='one'").fetchone())
                self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='two'").fetchone())
        result = subprocess.run([sys.executable, "-m", "backup_manager.cli", "migrate"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode); self.assertIn("--to-version", result.stderr)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); env = os.environ.copy()
            env.update({"BACKUP_MANAGER_DATA": raw, "BACKUP_MANAGER_DB": str(root / "cli.sqlite3")})
            result = subprocess.run([sys.executable, "-m", "backup_manager.cli", "migrate",
                                     "--to-version", "31"], cwd=ROOT, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("versao=31", result.stdout)
            self.assertTrue(require_schema_current(db_path=root / "cli.sqlite3").ready)

    def test_manifest_hash_missing_duplicate_and_unauthorized_file_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root, migrations, manifest_path = self.mini(raw)
            (migrations / "001_test_1.sql").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(MigrationError, "MIGRATION_HASH_MISMATCH"):
                self.migrate_mini(root, migrations, manifest_path, 1)
            (migrations / "001_test_1.sql").unlink()
            with self.assertRaisesRegex(MigrationError, "MIGRATION_FILE_MISSING"):
                self.migrate_mini(root, migrations, manifest_path, 1)
            duplicate = json.loads(manifest_path.read_text()); duplicate["migrations"][1]["version"] = 1
            manifest_path.write_text(json.dumps(duplicate))
            with self.assertRaisesRegex(MigrationError, "MIGRATION_MANIFEST_INVALID"):
                self.migrate_mini(root, migrations, manifest_path, 1)
        with tempfile.TemporaryDirectory() as raw:
            root, migrations, manifest_path = self.mini(raw)
            (migrations / "001_unauthorized.sql").write_text("SELECT 1;")
            with self.assertRaisesRegex(MigrationError, "MIGRATION_FILE_UNAUTHORIZED"):
                self.migrate_mini(root, migrations, manifest_path, 1)
        with tempfile.TemporaryDirectory() as raw:
            root, migrations, manifest_path = self.mini(raw)
            self.migrate_mini(root, migrations, manifest_path, 1)
            (migrations / "001_test_1.sql").write_text("CREATE TABLE changed(id INTEGER);")
            with self.assertRaisesRegex(MigrationError, "MIGRATION_HASH_MISMATCH"):
                self.migrate_mini(root, migrations, manifest_path, 1)
        with tempfile.TemporaryDirectory() as raw:
            root, migrations, manifest_path = self.mini(raw)
            original = migrations / "001_test_1.sql"; target = root / "outside.sql"
            target.write_text(original.read_text()); original.unlink(); original.symlink_to(target)
            with self.assertRaisesRegex(MigrationError, "MIGRATION_FILE_MISSING"):
                self.migrate_mini(root, migrations, manifest_path, 1)

    def test_lock_timeout_release_after_error_and_two_migrators(self):
        with tempfile.TemporaryDirectory() as raw:
            root, migrations, manifest_path = self.mini(raw)
            lock = root / "migration.lock"
            with _migration_lock(0, lock_path=lock, data_dir=root):
                with self.assertRaisesRegex(MigrationError, "MIGRATION_LOCKED"):
                    migrate(2, lock_timeout=0.05, _db_path=root / "db.sqlite3", _data_dir=root,
                            _lock_path=lock, _migrations_dir=migrations, _manifest_path=manifest_path)
            bad = migrations / "001_test_1.sql"; bad.write_text("changed")
            with self.assertRaises(MigrationError):
                self.migrate_mini(root, migrations, manifest_path, 1)
            bad.write_text("CREATE TABLE one(id INTEGER);")
            results = []
            def execute():
                results.append(self.migrate_mini(root, migrations, manifest_path, 2, lock_timeout=2).applied)
            threads = [threading.Thread(target=execute) for _ in range(2)]
            [thread.start() for thread in threads]; [thread.join() for thread in threads]
            self.assertEqual(1, sum(bool(item) for item in results))

    def test_pending_list_is_read_after_lock(self):
        with tempfile.TemporaryDirectory() as raw:
            root, migrations, manifest_path = self.mini(raw); lock = root / "migration.lock"
            result = []
            with _migration_lock(0, lock_path=lock, data_dir=root):
                thread = threading.Thread(target=lambda: result.append(
                    migrate(2, lock_timeout=2, _db_path=root / "db.sqlite3", _data_dir=root,
                            _lock_path=lock, _migrations_dir=migrations, _manifest_path=manifest_path,
                            _seed=False)))
                thread.start(); time.sleep(0.1)
                with open_db(root / "db.sqlite3") as conn:
                    conn.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,applied_at TEXT DEFAULT CURRENT_TIMESTAMP)")
                    conn.execute("CREATE TABLE one(id INTEGER)")
                    conn.execute("INSERT INTO schema_migrations(version) VALUES('001_test_1')")
            thread.join()
            self.assertEqual((2,), result[0].applied)

    def test_lock_excludes_a_real_second_process(self):
        with tempfile.TemporaryDirectory() as raw:
            root, migrations, manifest_path = self.mini(raw); lock = root / "migration.lock"
            code = ("from pathlib import Path; import sys,time; "
                    "from backup_manager.db import _migration_lock; "
                    "ctx=_migration_lock(0,lock_path=Path(sys.argv[1]),data_dir=Path(sys.argv[2])); "
                    "ctx.__enter__(); print('LOCKED',flush=True); time.sleep(2); ctx.__exit__(None,None,None)")
            process = subprocess.Popen([sys.executable, "-c", code, str(lock), str(root)], cwd=ROOT,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual("LOCKED", process.stdout.readline().strip())
                with self.assertRaisesRegex(MigrationError, "MIGRATION_LOCKED"):
                    self.migrate_mini(root, migrations, manifest_path, 1, lock_timeout=0.05)
            finally:
                process.terminate(); process.wait(timeout=5)
                process.stdout.close(); process.stderr.close()

    def test_backup_failure_prevents_ddl_and_backup_is_consistent_during_writes(self):
        with tempfile.TemporaryDirectory() as raw:
            root, migrations, manifest_path = self.mini(raw)
            self.migrate_mini(root, migrations, manifest_path, 1)
            with mock.patch("backup_manager.db._sqlite_backup", side_effect=MigrationError("MIGRATION_BACKUP_FAILED")):
                with self.assertRaisesRegex(MigrationError, "MIGRATION_BACKUP_FAILED"):
                    self.migrate_mini(root, migrations, manifest_path, 2)
            with open_db(root / "db.sqlite3") as conn:
                self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='two'").fetchone())
                conn.execute("CREATE TABLE writes(value INTEGER)")
            stop = threading.Event()
            def writer():
                with open_db(root / "db.sqlite3", timeout=2) as conn:
                    while not stop.is_set():
                        conn.execute("INSERT INTO writes VALUES(1)"); conn.commit()
            thread = threading.Thread(target=writer); thread.start(); time.sleep(0.05)
            backup = _sqlite_backup(root / "db.sqlite3", root)
            stop.set(); thread.join()
            with open_db(backup) as conn:
                self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])

    def test_second_migration_rolls_back_first_remains_and_retry_resumes(self):
        with tempfile.TemporaryDirectory() as raw:
            bad = {1: "CREATE TABLE one(id INTEGER);",
                   2: "CREATE TABLE two(id INTEGER); INSERT INTO missing VALUES(1);"}
            root, migrations, manifest_path = self.mini(raw, bad)
            with self.assertRaises(sqlite3.OperationalError):
                self.migrate_mini(root, migrations, manifest_path, 2)
            with open_db(root / "db.sqlite3") as conn:
                versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations")]
                self.assertEqual(["001_test_1"], versions)
                self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='two'").fetchone())
            fixed = "CREATE TABLE two(id INTEGER);"
            (migrations / "002_test_2.sql").write_text(fixed)
            payload = json.loads(manifest_path.read_text()); payload["migrations"][1]["sha256"] = hashlib.sha256(fixed.encode()).hexdigest()
            manifest_path.write_text(json.dumps(payload))
            self.assertEqual((2,), self.migrate_mini(root, migrations, manifest_path, 2).applied)
            with open_db(root / "db.sqlite3") as conn:
                self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])
                self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())

    def test_clean_install_and_upgrade_030_to_031(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); db = root / "db.sqlite3"; lock = root / "lock"
            clean = migrate(31, _db_path=db, _data_dir=root, _lock_path=lock)
            self.assertEqual(tuple(range(1, 32)), clean.applied)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); db = root / "db.sqlite3"; lock = root / "lock"
            migrate(30, _db_path=db, _data_dir=root, _lock_path=lock)
            upgraded = migrate(31, _db_path=db, _data_dir=root, _lock_path=lock)
            self.assertEqual((31,), upgraded.applied)
            self.assertTrue(require_schema_current(db_path=db).ready)
        install = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        deploy = (ROOT / "scripts/deploy-update.sh").read_text(encoding="utf-8")
        self.assertIn('migrate --to-version "${SCHEMA_VERSION}"', install)
        self.assertIn('migrate --to-version "${SCHEMA_VERSION}"', deploy)
        self.assertIn("schema-release-version", install)
        self.assertIn("schema-release-version", deploy)
        self.assertNotIn('SCHEMA_VERSION="29"', install + deploy)
        deploy_main = deploy.split("main() {", 1)[1]
        self.assertLess(deploy_main.index("run_tests"), deploy_main.index("quiesce_runtime"))
        self.assertLess(deploy_main.index("quiesce_runtime"), deploy_main.index("apply_migrations"))

    def test_seed_failure_after_schema_can_resume_without_reapplying_ddl(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); db = root / "db.sqlite3"; lock = root / "lock"
            with mock.patch("backup_manager.db.seed_defaults", side_effect=RuntimeError("seed failed")):
                with self.assertRaisesRegex(RuntimeError, "seed failed"):
                    migrate(31, _db_path=db, _data_dir=root, _lock_path=lock)
            with open_db(db) as conn:
                self.assertEqual(31, conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0])
                self.assertFalse(conn.execute("SELECT 1 FROM users WHERE username='admin'").fetchone())
            resumed = migrate(31, _db_path=db, _data_dir=root, _lock_path=lock)
            self.assertEqual((), resumed.applied)
            self.assertFalse(resumed.backup_path)
            self.assertTrue(require_schema_current(db_path=db).ready)


if __name__ == "__main__":
    unittest.main()
