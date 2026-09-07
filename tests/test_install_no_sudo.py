from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")


class InstallWithoutSudoRegressionTest(unittest.TestCase):
    def test_pure_ftpd_path_prepares_sudoers_for_root_only_images(self) -> None:
        self.assertIn("apt-get install -y sudo", INSTALLER)
        self.assertIn("install -d -o root -g root -m 0755 /etc/sudoers.d", INSTALLER)
        self.assertIn("install -m 0440 -o root -g root", INSTALLER)
        self.assertIn("visudo -cf /etc/sudoers.d/backup-manager-ftp", INSTALLER)

    def test_daemon_is_stopped_before_and_during_configuration(self) -> None:
        self.assertGreaterEqual(INSTALLER.count("systemctl stop pure-ftpd.service"), 2)

    def test_min_uid_and_empty_puredb_are_dynamic_and_non_destructive(self) -> None:
        self.assertIn('ftp_uid="$(id -u backupftp)"', INSTALLER)
        self.assertIn('printf \'%s\\n\' "${ftp_uid}" >/etc/pure-ftpd/conf/MinUID', INSTALLER)
        self.assertIn('if [[ ! -e /etc/pure-ftpd/pureftpd.pdb ]]', INSTALLER)
        self.assertIn('pure-pw mkdb', INSTALLER)


if __name__ == "__main__":
    unittest.main()
