import unittest

from backup_manager.operation_states import (CANONICAL_OPERATION_STATES, DOMAIN_STATE_MAPS,
                                             canonical_operation_state)


class CanonicalOperationStateTests(unittest.TestCase):
    def test_every_mapping_targets_the_small_canonical_vocabulary(self):
        self.assertEqual({"pending", "running", "success", "failed", "cancelled", "expired"},
                         CANONICAL_OPERATION_STATES)
        for domain, mapping in DOMAIN_STATE_MAPS.items():
            self.assertTrue(mapping, domain)
            self.assertTrue(set(mapping.values()).issubset(CANONICAL_OPERATION_STATES), domain)

    def test_current_database_vocabularies_are_fully_covered(self):
        expected = {
            "mikrotik_ftp_tests": {"pending", "running", "waiting_upload", "validating", "validated", "failed", "expired", "cancelled"},
            "backup_operations": {"waiting_upload", "validating", "success", "failed", "expired"},
            "mikrotik_ftp_uploads": {"receiving", "validating", "success", "failed", "timeout", "incomplete", "invalid_file", "duplicate"},
            "ftp_received_files": {"detected", "waiting_stable", "processing", "imported", "rejected", "failed", "duplicate"},
            "backups": {"receiving", "validating", "available", "quarantined", "failed", "trashed", "deleted"},
        }
        self.assertEqual(expected, {domain: set(mapping) for domain, mapping in DOMAIN_STATE_MAPS.items()})

    def test_unknown_domain_or_state_fails_closed(self):
        self.assertEqual("success", canonical_operation_state("mikrotik_ftp_tests", "validated"))
        with self.assertRaises(ValueError):
            canonical_operation_state("mikrotik_ftp_tests", "mystery")
        with self.assertRaises(ValueError):
            canonical_operation_state("unknown", "success")


if __name__ == "__main__":
    unittest.main()
