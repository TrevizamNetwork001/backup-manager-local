import pathlib
import sqlite3
import unittest
from unittest import mock
from datetime import datetime, timezone


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = (ROOT / "backup_manager/app.py").read_text()
OBSERVABILITY = (ROOT / "backup_manager/observability.py").read_text()
COLLECTOR = (ROOT / "backup_manager/service_snapshot.py").read_text()
CSS = (ROOT / "static/app.css").read_text()
DEPLOY = (ROOT / "scripts/deploy-update.sh").read_text()
TIMER = (ROOT / "deploy/systemd/backup-manager-observability.timer").read_text()


class ObservabilitySafetyTests(unittest.TestCase):
    def test_oneshot_transitions_are_not_red(self):
        from backup_manager import observability

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("create table settings (key text, value text)")
        conn.execute("insert into settings values ('ftp_enabled','0')")
        for active, sub in (("activating", "start"), ("deactivating", "stop")):
            with mock.patch.object(observability, "load_service_snapshot", return_value=(
                {"worker": {"active": active, "sub": sub, "result": "success", "load": "loaded"}}, False
            )):
                services, alerts = observability.service_health(conn, datetime.now(timezone.utc))
            worker = next(item for item in services if item["key"] == "worker")
            self.assertEqual(worker["level"], "green")
            self.assertEqual(worker["summary"], "execução em andamento")
            self.assertFalse(any(item["source"] == "Worker" for item in alerts))

    def test_web_request_path_never_executes_shell_or_subprocess(self):
        self.assertNotIn("subprocess", OBSERVABILITY)
        dashboard_source = APP[APP.index("def dashboard("):APP.index("def setup_page(")]
        for forbidden in ("subprocess", "os.system", "systemctl", "shell=True"):
            self.assertNotIn(forbidden, dashboard_source)
        self.assertIn("subprocess.run", COLLECTOR)
        self.assertIn('["/usr/bin/systemctl", "show", unit', COLLECTOR)
        self.assertNotIn("shell=True", COLLECTOR)

    def test_snapshot_timer_is_safe_and_runs_every_30_seconds(self):
        self.assertIn("OnUnitActiveSec=30s", TIMER)
        self.assertIn('OBSERVABILITY_NAME="backup-manager-observability"', DEPLOY)
        self.assertIn("ProtectSystem=strict", (ROOT / "deploy/systemd/backup-manager-observability.service").read_text())

    def test_dashboard_has_responsive_noc_styles(self):
        for breakpoint in ("1280px", "1050px", "860px", "520px"):
            self.assertIn(f"max-width: {breakpoint}", CSS)
        self.assertIn(".global-state", CSS)
        self.assertIn(".timeline-panel", CSS)
        self.assertIn(".service-grid", CSS)


if __name__ == "__main__":
    unittest.main()
