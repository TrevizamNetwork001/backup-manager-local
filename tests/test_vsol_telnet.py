from __future__ import annotations

import socket
import unittest

from backup_manager.vsol_olt import VSOLOLTError, _safe_cli_detail, collect_running_config


CONFIG = "\n".join((
    "show running-config",
    "!",
    "hostname OLT-VSOL-LAB",
    "interface gpon 0/1",
    " description uplink-principal",
    " switchport trunk allowed vlan 10,20,30",
    " no shutdown",
    "!",
    "snmp-server location laboratorio-de-homologacao",
    "!",
))


class ScriptedSocket:
    def __init__(self, replies):
        self.replies = [list(reply) for reply in replies]
        self.current = self.replies.pop(0)
        self.sent = []
        self.timeout = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def recv(self, _size):
        if self.current:
            value = self.current.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value
        raise socket.timeout()

    def sendall(self, value):
        self.sent.append(value)
        if value != b" " and self.replies:
            self.current = self.replies.pop(0)

    def close(self):
        self.closed = True


class VSOLTelnetTest(unittest.TestCase):
    def test_cli_error_detail_removes_ftp_credentials(self):
        detail = _safe_cli_detail(
            "copy startup-config ftp://user:secret@192.0.2.1/olt.config\r\nTransfer failed\r\nOLT#"
        )
        self.assertNotIn("user", detail)
        self.assertNotIn("secret", detail)
        self.assertIn("Transfer failed", detail)

    def factory(self, replies):
        connection = ScriptedSocket(replies)
        calls = []

        def create(address, timeout):
            calls.append((address, timeout))
            return connection

        return connection, calls, create

    def test_collects_config_with_username_prompt_enable_and_pagination(self):
        connection, calls, factory = self.factory([
            [b"\xff\xfb\x01Welcome\r\nUser name: "],
            [b"Password: "],
            [b"OLT-VSOL>"],
            [b"Password: "],
            [b"OLT-VSOL#"],
            [(CONFIG[:90] + "--More--").encode("latin1"),
             (CONFIG[90:] + "\r\nOLT-VSOL#").encode("latin1")],
        ])

        result = collect_running_config(
            host="192.0.2.40", port=23, username="admin", password="secret",
            timeout=2, socket_factory=factory,
        )

        self.assertEqual(calls, [(('192.0.2.40', 23), 2)])
        self.assertEqual(connection.timeout, 0.5)
        self.assertEqual(connection.sent.count(b" "), 1)
        self.assertEqual(connection.sent[:5], [
            b"admin\r\n", b"secret\r\n", b"enable\r\n", b"secret\r\n",
            b"show running-config\r\n",
        ])
        self.assertNotIn("show running-config", result)
        self.assertNotIn("--More--", result)
        self.assertNotIn("OLT-VSOL#", result)
        self.assertIn("hostname OLT-VSOL-LAB", result)
        self.assertTrue(connection.closed)

    def test_accepts_login_already_in_privileged_mode(self):
        connection, _, factory = self.factory([
            [b"login: "], [b"password: "], [b"OLT#"],
            [(CONFIG + "\nOLT#").encode("latin1")],
        ])
        result = collect_running_config(
            host="olt.example", port=2323, username="operador", password="secret",
            timeout=2, socket_factory=factory,
        )
        self.assertNotIn(b"enable\r\n", connection.sent)
        self.assertIn("interface gpon 0/1", result)

    def test_rejects_command_error_and_always_closes_connection(self):
        connection, _, factory = self.factory([
            [b"login: "], [b"password: "], [b"OLT#"],
            [b"show running-config\r\n% Invalid input\r\nOLT#"],
        ])
        with self.assertRaisesRegex(VSOLOLTError, "recusou o comando"):
            collect_running_config(
                host="olt.example", port=23, username="admin", password="secret",
                timeout=2, socket_factory=factory,
            )
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
