import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "deploy/systemd"
MANIFEST = SYSTEMD / "units.manifest"


class SystemdManifestTests(unittest.TestCase):
    def entries(self):
        result = []
        for raw in MANIFEST.read_text().splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                result.append(tuple(line.split()))
        return result

    def test_manifest_is_unique_allowlisted_and_references_existing_units(self):
        entries = self.entries()
        names = [unit for _, unit in entries]
        self.assertEqual(len(names), len(set(names)))
        for profile, unit in entries:
            self.assertIn(profile, {"both", "install", "update"})
            self.assertRegex(unit, r"^backup-manager-[a-z0-9-]+\.(?:service|timer)$")
            self.assertTrue((SYSTEMD / unit).is_file(), unit)

    def test_install_and_update_consume_the_same_manifest(self):
        install = (ROOT / "scripts/install.sh").read_text()
        update = (ROOT / "scripts/deploy-update.sh").read_text()
        for script in (install, update):
            self.assertIn('deploy/systemd/units.manifest', script)
            self.assertIn('Unidade inválida no manifesto', script)
            self.assertEqual(1, script.count('done <"${APP_DIR}/deploy/systemd/units.manifest"'))

    def test_expected_runtime_roles_are_declared(self):
        names = {unit for _, unit in self.entries()}
        expected = {
            "backup-manager-local.service", "backup-manager-worker.timer",
            "backup-manager-ftp-importer.timer", "backup-manager-lifecycle.timer",
            "backup-manager-observability.timer", "backup-manager-database-guard.timer",
            "backup-manager-cloud-sync.timer",
            "backup-manager-telegram-backup.timer", "backup-manager-update-check.timer",
        }
        self.assertTrue(expected.issubset(names))

    def test_database_guard_is_enabled_and_quiesced_during_deploy(self):
        install = (ROOT / "scripts/install.sh").read_text()
        update = (ROOT / "scripts/deploy-update.sh").read_text()
        self.assertIn("systemctl enable --now backup-manager-database-guard.timer", install)
        self.assertIn('DATABASE_GUARD_NAME="backup-manager-database-guard"', update)
        self.assertIn('systemctl enable --now "${DATABASE_GUARD_NAME}.timer"', update)
        self.assertIn('"${DATABASE_GUARD_NAME}.service"', update)


if __name__ == "__main__":
    unittest.main()
