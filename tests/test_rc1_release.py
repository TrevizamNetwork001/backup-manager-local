from __future__ import annotations

import pathlib
import re
import sqlite3
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
APP = (ROOT / "backup_manager/app.py").read_text(encoding="utf-8")
VIEWS = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "backup_manager").glob("*_views.py"))
CSS = (ROOT / "static/app.css").read_text(encoding="utf-8")


def apply_migrations(conn: sqlite3.Connection, paths) -> None:
    for path in paths:
        conn.executescript(path.read_text(encoding="utf-8"))


class RC1ReleaseTests(unittest.TestCase):
    def test_clean_database_applies_every_migration(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        apply_migrations(conn, sorted(MIGRATIONS.glob("*.sql")))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for expected in ("equipment", "backups", "ftp_accounts", "lifecycle_runs", "notification_queue"):
            self.assertIn(expected, tables)
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_rc1_index_upgrade_preserves_existing_rows_and_secrets(self) -> None:
        paths = sorted(MIGRATIONS.glob("*.sql"))
        rc1 = next(path for path in paths if path.name.startswith("011_"))
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        apply_migrations(conn, [path for path in paths if path < rc1])
        conn.execute("INSERT INTO equipment(hostname,ip_address,name) VALUES('rc1-router','10.0.0.1','RC1 Router')")
        conn.execute("UPDATE notification_channels SET token_encrypted='encrypted-secret' WHERE channel_type='telegram'")
        before = conn.execute("SELECT hostname,ip_address FROM equipment").fetchall()
        apply_migrations(conn, [rc1])
        self.assertEqual(conn.execute("SELECT hostname,ip_address FROM equipment").fetchall(), before)
        self.assertEqual(conn.execute("SELECT token_encrypted FROM notification_channels").fetchone()[0], "encrypted-secret")
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_long_operations_have_consistent_feedback_and_toasts(self) -> None:
        for text in ("Testando conexão...", "Gerando backup...", "Importando...", "Limpando...",
                     "Restaurando...", "Enviando notificação..."):
            self.assertIn(text, APP)
        self.assertIn('"success": "5000"', APP)
        self.assertIn('"info": "6000"', APP)
        self.assertIn('"error": "8000"', APP)
        self.assertIn('aria-live="polite"', APP)

    def test_accessibility_responsiveness_and_empty_states(self) -> None:
        self.assertIn(":focus-visible", CSS)
        self.assertIn("prefers-reduced-motion", CSS)
        for breakpoint in ("1280px", "1050px", "860px", "520px"):
            self.assertIn(f"max-width: {breakpoint}", CSS)
        self.assertIn('aria-label="Navegação principal"', APP)
        self.assertIn('aria-label="Breadcrumb"', APP)
        for empty in ("Nenhuma conta FTP", "Nenhum backup encontrado", "Nenhuma tentativa registrada",
                      "Nenhum job encontrado", "Nenhum equipamento ativo"):
            self.assertIn(empty, APP + VIEWS)

    def test_generic_database_exceptions_are_not_rendered(self) -> None:
        leaked = re.findall(r'except Exception as exc:[\s\S]{0,180}(?:str\(exc\)|\{exc\})', APP)
        self.assertEqual(leaked, [])
        self.assertIn("Não foi possível concluir a operação", APP)
        self.assertNotIn("traceback", APP.lower())

    def test_obsolete_internal_routes_are_not_exposed(self) -> None:
        self.assertNotIn("/ssh-config", APP)
        self.assertNotIn("/jobs/test", APP)
        self.assertNotIn("local_dt(payload['started_at'])", APP)
        self.assertNotIn("local_dt(payload['finished_at'])", APP)

    def test_database_and_runtime_units_use_restricted_permissions(self) -> None:
        database = (ROOT / "backup_manager/db.py").read_text(encoding="utf-8")
        self.assertIn("DB_PATH.chmod(0o640)", database)
        for unit in ("backup-manager-local.service", "backup-manager-worker.service"):
            content = (ROOT / "deploy/systemd" / unit).read_text(encoding="utf-8")
            self.assertIn("UMask=0027", content)


if __name__ == "__main__":
    unittest.main()
