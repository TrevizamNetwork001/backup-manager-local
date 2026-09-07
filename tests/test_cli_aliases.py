import unittest

from backup_manager.cli import COMMAND_ALIASES, domain_commands, normalize_command_argv


class HierarchicalCLITests(unittest.TestCase):
    def test_required_domains_have_canonical_commands(self):
        expected = {
            ("admin", "reset-password"), ("database", "migrate"), ("database", "check"),
            ("system", "diagnose"), ("system", "version"), ("ftp", "diagnose"),
            ("ftp", "reconcile"), ("backups", "reconcile"), ("retention", "simulate"),
            ("retention", "run"), ("jobs", "run"), ("telegram", "diagnose"),
            ("updates", "check"), ("updates", "apply"),
        }
        self.assertTrue(expected.issubset(COMMAND_ALIASES))

    def test_normalization_preserves_options_and_legacy_commands(self):
        self.assertEqual(
            ["backup-manager", "ftp-account-sync", "--account-id", "9"],
            normalize_command_argv(["backup-manager", "ftp", "sync", "--account-id", "9"]),
        )
        legacy = ["backup-manager", "ftp-account-sync", "--account-id", "9"]
        self.assertEqual(legacy, normalize_command_argv(legacy))
        unknown = ["backup-manager", "ftp", "unknown"]
        self.assertEqual(unknown, normalize_command_argv(unknown))

    def test_domain_help_is_generated_from_the_same_alias_catalog(self):
        self.assertEqual(("diagnose", "permissions", "reconcile", "scan", "stats", "sync", "sync-all"),
                         domain_commands("ftp"))
        self.assertEqual((), domain_commands("unknown"))


if __name__ == "__main__":
    unittest.main()
