import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / "scripts/deploy-update.sh").read_text()
INSTALL = (ROOT / "scripts/install.sh").read_text()
SERVICE = (ROOT / "deploy/systemd/backup-manager-lifecycle.service").read_text()
TIMER = (ROOT / "deploy/systemd/backup-manager-lifecycle.timer").read_text()
APP = (ROOT / "backup_manager/app.py").read_text()


class LifecycleDeploymentTests(unittest.TestCase):
    def test_timer_is_installed_by_install_and_update(self):
        self.assertIn("backup-manager-lifecycle.timer", INSTALL)
        self.assertIn('LIFECYCLE_NAME="backup-manager-lifecycle"', DEPLOY)
        self.assertIn('systemctl enable --now "${LIFECYCLE_NAME}.timer"', DEPLOY)
        self.assertIn("Persistent=true", TIMER)

    def test_service_is_confined_to_storage_and_database(self):
        self.assertIn("ProtectSystem=strict", SERVICE)
        self.assertIn("ReadWritePaths=/var/lib/backup-manager-local /opt/backup-manager-local/data", SERVICE)
        for forbidden in ("nginx", "pure-ftpd", "sshd", "letsencrypt", "ufw"):
            self.assertNotIn(forbidden, SERVICE.lower())

    def test_permanent_delete_is_confined_to_trash_and_requires_confirmation(self):
        self.assertNotIn('/backups/[0-9a-fA-F-]{36}/delete', APP)
        self.assertNotIn("permanently_delete_backup", APP)
        self.assertIn('/backups/[0-9a-fA-F-]{36}/purge', APP)
        self.assertIn("PURGE_CONFIRMATION", APP)
        self.assertIn("EMPTY_TRASH_CONFIRMATION", APP)


if __name__ == "__main__":
    unittest.main()
