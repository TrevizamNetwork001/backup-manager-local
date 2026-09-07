import os
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backup_manager.ftp_pipeline import discover, stabilization, validate_file
from backup_manager.ftp_events import dispatch_backup_created
from backup_manager.ftp_correlation import correlate
from backup_manager.mikrotik_ftp_uploads import (RetryableUploadValidationError,
                                                 UnmanagedUpload, UploadValidationError)


class FTPPipelineStageTests(unittest.TestCase):
    def test_correlation_classifies_managed_unmanaged_waiting_and_rejected(self):
        current = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
        recent = {"id": 9, "detected_at": (current - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")}
        old = {"id": 9, "detected_at": (current - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")}
        arguments = dict(conn=object(), account_id=1, received=recent, filename="backup.rsc",
                         size=12, mtime_ns=3, current=current)
        marker = object()
        with mock.patch("backup_manager.ftp_correlation.begin_validation", return_value=marker):
            decision = correlate(**arguments)
        self.assertEqual(("managed", marker), (decision.action, decision.match))
        for effect, expected in (
            (UnmanagedUpload(), ("unmanaged", "ignored_unmanaged")),
            (RetryableUploadValidationError("pair_missing", "Par ausente"), ("waiting_pair", "pair_missing")),
            (UploadValidationError("invalid_name", "Nome inválido"), ("reject", "invalid_name")),
        ):
            with mock.patch("backup_manager.ftp_correlation.begin_validation", side_effect=effect):
                decision = correlate(**arguments)
            self.assertEqual(expected, (decision.action, decision.code))
        arguments["received"] = old
        with mock.patch("backup_manager.ftp_correlation.begin_validation",
                        side_effect=RetryableUploadValidationError("pair_missing", "Par ausente")):
            decision = correlate(**arguments)
        self.assertEqual(("reject", "pair_timeout"), (decision.action, decision.code))

    def test_event_dispatch_reuses_cloud_and_telegram_queues(self):
        audited = []
        backups = [(7, "backup-uuid", "router.rsc", 123, "abcdef1234567890")]
        with mock.patch("backup_manager.cloud_sync.enqueue_new_backup") as cloud, \
             mock.patch("backup_manager.telegram_backup.enqueue_new_backup") as telegram:
            count = dispatch_backup_created(object(), backups,
                audit_created=lambda filename, details: audited.append((filename, details)))
        self.assertEqual(1, count)
        cloud.assert_called_once_with(mock.ANY, 7)
        telegram.assert_called_once_with(mock.ANY, 7)
        self.assertEqual("router.rsc", audited[0][0])
        self.assertEqual("abcdef123456", audited[0][1]["sha256"])

    def test_discovery_ignores_hidden_and_partial_files(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            for name in ("backup.rsc", ".hidden", "upload.part", "upload.tmp"):
                (directory / name).write_bytes(b"content")
            self.assertEqual(["backup.rsc"], [item.name for item in discover(directory)])

    def test_stabilization_distinguishes_changed_waiting_and_stable(self):
        current = datetime(2026, 7, 17, 12, 0, 10, tzinfo=timezone.utc)
        existing = {"observed_size": 10, "observed_mtime_ns": 20, "updated_at": "2026-07-17 12:00:05"}
        changed = type("Info", (), {"st_size": 11, "st_mtime_ns": 21})()
        same = type("Info", (), {"st_size": 10, "st_mtime_ns": 20})()
        self.assertEqual("changed", stabilization(existing, changed, current=current, stable_seconds=3).state)
        self.assertEqual("stable", stabilization(existing, same, current=current, stable_seconds=3).state)
        self.assertEqual("waiting", stabilization(existing, same, current=current, stable_seconds=8).state)

    def test_validation_rejects_unsafe_empty_and_large_files(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            empty = directory / "empty.cfg"; empty.touch()
            regular = directory / "large.cfg"; regular.write_bytes(b"12345")
            link = directory / "link.cfg"; os.symlink(regular, link)
            self.assertEqual("empty_file", validate_file(empty.lstat(), max_size=10, account_quota=None).code)
            self.assertEqual("file_too_large", validate_file(regular.lstat(), max_size=4, account_quota=None).code)
            self.assertEqual("unsafe_file", validate_file(link.lstat(), max_size=10, account_quota=None).code)
            self.assertIsNone(validate_file(regular.lstat(), max_size=10, account_quota=10))


if __name__ == "__main__":
    unittest.main()
