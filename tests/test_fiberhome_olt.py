from __future__ import annotations

import unittest

from backup_manager.fiberhome_olt import FiberHomeFTPConfig, backup_commands, run_telnet_backup


class FakeSocket:
    def __init__(self) -> None:
        self.responses = iter((
            b"Login: ", b"Password: ", b"User> ", b"OLT# ", b"Success\nOLT# ",
            b"Success\nOLT# ", b"Success\nOLT# ",
        ))
        self.sent: list[str] = []

    def settimeout(self, timeout): pass
    def recv(self, size): return next(self.responses, b"")
    def sendall(self, value): self.sent.append(value.decode("iso-8859-1").strip())
    def close(self): pass


class FiberHomeOLTTest(unittest.TestCase):
    def config(self) -> FiberHomeFTPConfig:
        return FiberHomeFTPConfig("192.0.2.10", 21, "backup_user", "Senha123!",
                                  "olt-config.txt", "olt-system.db")

    def test_commands_include_port_and_both_artifacts(self) -> None:
        self.assertEqual(backup_commands(self.config()), (
            "enable", "ping 192.0.2.10",
            "upload ftp config 192.0.2.10 21 backup_user Senha123! olt-config.txt",
            "upload ftp system 192.0.2.10 21 backup_user Senha123! olt-system.db",
        ))

    def test_telnet_flow_authenticates_and_requires_two_successes(self) -> None:
        sock = FakeSocket()
        result = run_telnet_backup(
            olt_host="198.51.100.10", olt_port=23, olt_username="admin", olt_password="OltSecret!",
            ftp=self.config(), socket_factory=lambda address, timeout: sock,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(2, result["artifacts"])
        self.assertEqual("admin", sock.sent[0])
        self.assertEqual("OltSecret!", sock.sent[1])
        self.assertEqual("enable", sock.sent[2])


if __name__ == "__main__":
    unittest.main()
