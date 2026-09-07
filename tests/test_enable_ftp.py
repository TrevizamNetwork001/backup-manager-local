import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "enable-ftp.sh").read_text()


class EnableFTPTests(unittest.TestCase):
    def test_requires_root_and_supports_dry_run(self):
        self.assertIn('[[ "${EUID}" -eq 0 ]]', SCRIPT)
        self.assertIn("--dry-run", SCRIPT)
        self.assertIn("DRY-RUN: nenhuma alteracao sera realizada", SCRIPT)

    def test_transaction_backs_up_sensitive_state_and_hashes(self):
        for value in ("${PURE_DIR}", "${PASSWD}", "${PUREDB}", "${SUDOERS}",
                      "${SERVICE}", "${TIMER}"):
            self.assertIn(f'backup_one "{value}"', SCRIPT)
        for value in ("${PURE_DIR}", "${SUDOERS}", "${SERVICE}", "${TIMER}"):
            self.assertIn(f'restore_one "{value}"', SCRIPT)
        self.assertIn("MANIFEST.sha256", SCRIPT)
        self.assertIn("sha256sum", SCRIPT)
        self.assertIn("ftp-setting.json", SCRIPT)
        self.assertIn("trap rollback ERR INT TERM EXIT", SCRIPT)

    def test_validates_before_restarting(self):
        wrapper = SCRIPT.index("pure-ftpd-wrapper --show-options")
        sudoers = SCRIPT.index('visudo -cf "${SUDOERS}"')
        restart = SCRIPT.index("systemctl restart pure-ftpd.service", wrapper)
        self.assertLess(wrapper, restart)
        self.assertLess(sudoers, restart)

    def test_dynamic_minuid_and_upload_only_controls(self):
        self.assertIn('FTP_UID="$(id -u backupftp)"', SCRIPT)
        self.assertIn('write_conf MinUID "${FTP_UID}"', SCRIPT)
        self.assertIn("write_conf KeepAllFiles yes", SCRIPT)
        self.assertIn("write_conf NoRename yes", SCRIPT)
        self.assertIn("write_conf Umask '477 077'", SCRIPT)
        self.assertIn('rm -f "${PURE_DIR}/auth/65unix" "${PURE_DIR}/auth/70pam"', SCRIPT)

    def test_does_not_manage_out_of_scope_services(self):
        for forbidden in ("nginx", "letsencrypt", "certbot", "ufw", "sshd", "iptables"):
            self.assertNotIn(f"systemctl restart {forbidden}", SCRIPT.lower())


if __name__ == "__main__":
    unittest.main()
