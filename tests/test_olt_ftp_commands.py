from __future__ import annotations

import unittest

from backup_manager.cdata_olt import CDataFTPConfig, backup_command as cdata_command, redact_command as redact_cdata
from backup_manager.huawei_olt import HuaweiFTPConfig, backup_commands as huawei_commands, redact_commands as redact_huawei
from backup_manager.intelbras_olt import (
    IntelbrasFTPConfig, epon_backup_command, gpon_backup_dialog,
    redacted_epon_command, redacted_gpon_dialog,
)
from backup_manager.parks_olt import (
    ParksFTPConfig, backup_100xx_200xx_command, backup_300xx_400xx_command, redact_command as redact_parks,
)
from backup_manager.zte_olt import ZTEFTPConfig, automatic_backup_commands, manual_test_commands, redact_commands
from backup_manager.vsol_olt import VSOLFTPConfig, daily_schedule_commands, manual_backup_commands, redact_commands as redact_vsol
from backup_manager.ssh import DatacomDmOSDriver, driver_group, driver_label, get_driver, is_ftp_push_olt_driver


class OLTFTPCommandsTest(unittest.TestCase):
    def test_connection_methods_are_classified_by_equipment_type(self) -> None:
        self.assertEqual("OLT", driver_group("cdata_olt_ssh_ftp"))
        self.assertEqual("Roteador", driver_group("mikrotik_routeros"))
        self.assertEqual("Switch", driver_group("huawei_vrp"))
        self.assertEqual("Genérico", driver_group("generic_ssh"))
        self.assertIn("Legado", driver_label("parks_ssh"))
        self.assertEqual("generic_ssh", get_driver({"ssh_backup_driver": "parks_ssh"}).vendor_key)
        self.assertTrue(is_ftp_push_olt_driver("huawei_olt_ssh_ftp"))
        self.assertFalse(is_ftp_push_olt_driver("datacom_dmos_ssh"))
        self.assertEqual("vsol_olt_telnet_cli", get_driver({"ssh_backup_driver": "vsol_olt_telnet_cli"}).vendor_key)
        self.assertFalse(is_ftp_push_olt_driver("vsol_olt_telnet_cli"))

    def test_datacom_dmos_captures_running_configuration_over_ssh(self) -> None:
        driver = DatacomDmOSDriver({})
        self.assertEqual(("show version",), driver.test_commands())
        self.assertEqual(("show running-config",), driver.backup_commands())

    def test_zte_manual_and_automatic_commands(self) -> None:
        config = ZTEFTPConfig("192.0.2.10", "backups", "backup_user", "Senha123!")
        self.assertIn("upload cfg startrun.dat", manual_test_commands(config)[2])
        automatic = automatic_backup_commands(config)
        self.assertIn("file-server auto-backup all server-index 1", automatic[3])
        self.assertIn("auto-backup condition cfg-changed", automatic[4])
        self.assertEqual("show file-server auto-backup", automatic[-1])
        self.assertNotIn("Senha123!", "\n".join(redact_commands(automatic, config.password)))

    def test_huawei_exports_configuration_and_database(self) -> None:
        config = HuaweiFTPConfig("192.0.2.20", "backup_user", "Senha123!", "olt.cfg", "olt.dat")
        commands = huawei_commands(config)
        self.assertEqual(("enable", "config", "undo interactive", "undo smart", "scroll"), commands[:5])
        self.assertIn("backup configuration ftp 192.0.2.20 olt.cfg", commands)
        self.assertIn("backup data ftp 192.0.2.20 olt.dat", commands)
        self.assertNotIn("Senha123!", "\n".join(redact_huawei(commands, config.password)))

    def test_intelbras_gpon_uses_interactive_credentials(self) -> None:
        config = IntelbrasFTPConfig("192.0.2.30", "backup_user", "Senha123!", "olt.cfg")
        dialog = gpon_backup_dialog(config)
        self.assertEqual("backup network ftp 192.0.2.30 filename olt.cfg", dialog[0][0])
        self.assertEqual(("backup_user", r"User\s*:?[ ]*$"), dialog[1])
        self.assertEqual(("Senha123!", r"Password\s*:?[ ]*$"), dialog[2])
        self.assertNotIn("Senha123!", repr(redacted_gpon_dialog(config)))

    def test_intelbras_epon_uses_single_command_and_redacts_password(self) -> None:
        config = IntelbrasFTPConfig("192.0.2.30", "backup_user", "Senha123!", "olt.cfg")
        self.assertEqual(
            "upload configuration ftp inet 192.0.2.30 backup_user Senha123! olt.cfg",
            epon_backup_command(config),
        )
        self.assertNotIn("Senha123!", redacted_epon_command(config))

    def test_vsol_manual_backup_and_optional_daily_schedule(self) -> None:
        config = VSOLFTPConfig("192.0.2.40", 21, "backup_user", "Senha123!", "olt.config")
        manual = manual_backup_commands(config)
        self.assertEqual(manual, (
            "write",
            "copy startup-config ftp://backup_user:Senha123%21@192.0.2.40/olt.config",
        ))
        self.assertNotIn("Senha123!", "\n".join(redact_vsol(manual, config.password)))
        self.assertEqual(daily_schedule_commands(config), (
            "cron task backup_diario", "schedule daily 03:00",
            'action "copy startup-config ftp://backup_user:Senha123%21@192.0.2.40/olt.config"',
            "exit", "write",
        ))
        encoded = manual_backup_commands(VSOLFTPConfig(
            "192.0.2.40", 21, "backup@user", "Senha:forte#2026", "olt.config",
        ))
        self.assertIn("backup%40user:Senha%3Aforte%232026@192.0.2.40", encoded[1])
        self.assertNotIn("Senha%3Aforte%232026", "\n".join(redact_vsol(encoded, "Senha:forte#2026")))

    def test_parks_commands_are_separated_by_product_family(self) -> None:
        config = ParksFTPConfig("192.0.2.50", "backup_user", "Senha123!", "olt.bin")
        old_family = backup_100xx_200xx_command(config)
        new_family = backup_300xx_400xx_command(config)
        self.assertEqual("copy startup-config ftp://192.0.2.50/olt.bin backup_user Senha123!", old_family)
        self.assertEqual("copy startup-config ftp://backup_user:Senha123!@192.0.2.50/olt.bin", new_family)
        self.assertNotIn("Senha123!", redact_parks(new_family, config.password))

    def test_cdata_supports_gz_and_txt_backup_formats(self) -> None:
        config = CDataFTPConfig("192.0.2.60", "backup_user", "Senha123!", "olt-config", "gz")
        command = cdata_command(config)
        self.assertEqual("backup save-config format gz ftp 192.0.2.60 backup_user Senha123! olt-config", command)
        self.assertNotIn("Senha123!", redact_cdata(command, config.password))


if __name__ == "__main__":
    unittest.main()
