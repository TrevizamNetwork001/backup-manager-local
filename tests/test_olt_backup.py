from __future__ import annotations

import unittest
from unittest import mock
from datetime import datetime, timezone

from backup_manager.olt_backup import OLTBackupError, OLTBackupPlan, OLTBackupRequest, build_plan, redact_plan, run_ssh_plan
from backup_manager.storage import safe_filename


class FakeChannel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.current = b"banner#"
        self.sent = []
    def settimeout(self, timeout): pass
    def recv_ready(self): return bool(self.current)
    def recv(self, size): value, self.current = self.current, b""; return value
    def send(self, value): self.sent.append(value.strip()); self.current = self.responses.pop(0)
    def close(self): pass


class FakeClient:
    def __init__(self, channel): self.channel = channel
    def invoke_shell(self): return self.channel


class OLTBackupPlanTest(unittest.TestCase):
    def test_vendor_binary_extensions_are_accepted_by_managed_storage(self) -> None:
        for filename in ("fiberhome.db", "huawei.dat", "parks.bin", "vsol.config", "cdata.gz", "zte.cfg"):
            with self.subTest(filename=filename):
                self.assertEqual(filename, safe_filename(filename))

    def request(self, driver: str, action: str = "manual") -> OLTBackupRequest:
        return OLTBackupRequest(driver, "olt-core", "192.0.2.10", 21, "/backups",
                                "backup_user", "Senha123!", action)

    def test_all_ftp_olt_drivers_have_an_operational_plan(self) -> None:
        drivers = (
            "fiberhome_olt_telnet_ftp", "huawei_olt_ssh_ftp", "zte_olt_ssh_ftp",
            "intelbras_gpon_ssh_ftp", "intelbras_epon_ssh_ftp", "vsol_olt_ssh_ftp",
            "vsol_olt_telnet_cli",
            "parks_olt_100_200_ssh_ftp", "parks_olt_300_400_ssh_ftp", "cdata_olt_ssh_ftp",
        )
        now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
        for driver in drivers:
            with self.subTest(driver=driver):
                plan = build_plan(self.request(driver), now=now)
                self.assertTrue(plan.commands)
                self.assertTrue(plan.expected_files)
                self.assertNotIn("Senha123!", "\n".join(redact_plan(plan, "Senha123!").commands))

    def test_zte_install_plan_configures_automatic_backup(self) -> None:
        plan = build_plan(self.request("zte_olt_ssh_ftp", "install"))
        self.assertIn("file-server auto-backup all server-index 1", "\n".join(plan.commands))
        self.assertEqual((), plan.expected_files)

    def test_fiberhome_expects_two_received_files_over_telnet(self) -> None:
        plan = build_plan(self.request("fiberhome_olt_telnet_ftp"))
        self.assertEqual("telnet", plan.transport)
        self.assertEqual(2, len(plan.expected_files))

    def test_vsol_v1600gt_uses_telnet_to_trigger_ftp_export(self) -> None:
        plan = build_plan(self.request("vsol_olt_telnet_cli"))
        self.assertEqual("telnet", plan.transport)
        self.assertEqual("write", plan.commands[0])
        self.assertIn("copy startup-config ftp://backup_user:Senha123%21@192.0.2.10/", plan.commands[1])
        self.assertTrue(plan.expected_files[0].endswith(".config"))

    def test_interactive_intelbras_plan_waits_for_user_and_password_prompts(self) -> None:
        channel = FakeChannel((b"User:", b"Password:", b"Backup successful\nOLT#"))
        plan = OLTBackupPlan("ssh", ("backup network ftp host filename olt.cfg", "user", "secret"),
                             ("olt.cfg",), True)
        result = run_ssh_plan(FakeClient(channel), plan, timeout=1, quiet_seconds=0)
        self.assertTrue(result["ok"])
        self.assertEqual(3, result["commands_executed"])
        self.assertEqual(["backup network ftp host filename olt.cfg", "user", "secret"], channel.sent)


class HuaweiFTPResultTest(unittest.TestCase):
    def test_failure_aborts_before_next_backup_command(self):
        channel = FakeChannel((b"Backing up files is fails", b"Backing up files is successful"))
        plan = OLTBackupPlan("ssh", ("backup configuration ftp 192.0.2.1 olt.cfg",
                                     "backup data ftp 192.0.2.1 olt.dat"), ("olt.cfg", "olt.dat"))
        with self.assertRaises(OLTBackupError):
            run_ssh_plan(FakeClient(channel), plan, timeout=1, quiet_seconds=0)
        self.assertEqual(channel.sent, [plan.commands[0]])

    def test_success_after_confirmation(self):
        channel = FakeChannel((b"Are you sure to continue? (y/n)", b"Backing up files is successful"))
        plan = OLTBackupPlan("ssh", ("backup configuration ftp 192.0.2.1 olt.cfg",), ("olt.cfg",))
        self.assertTrue(run_ssh_plan(FakeClient(channel), plan, timeout=1, quiet_seconds=0)["ok"])
        self.assertEqual(channel.sent, [plan.commands[0], "y"])

    def test_command_rejection_does_not_wait_for_completion(self):
        channel = FakeChannel((b"Unknown command",))
        plan = OLTBackupPlan("ssh", ("backup configuration ftp 192.0.2.1 olt.cfg",), ("olt.cfg",))
        with mock.patch("backup_manager.olt_backup.time.sleep", side_effect=AssertionError("unexpected wait")):
            with self.assertRaises(OLTBackupError):
                run_ssh_plan(FakeClient(channel), plan, timeout=1, quiet_seconds=0)


if __name__ == "__main__":
    unittest.main()
