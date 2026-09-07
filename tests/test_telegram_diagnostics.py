import io
import json
import unittest
import urllib.error
from unittest import mock

from backup_manager.notifications import TelegramAPIError, TelegramTransport


TOKEN = "1234567:abcdefghijklmnopqrstuvwxyzABCDE"


class Response:
    def __init__(self, payload, status=200): self.payload, self.status = payload, status
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit): return json.dumps(self.payload).encode()[:limit]


def http_error(status, description, parameters=None):
    payload = {"ok": False, "error_code": status, "description": description}
    if parameters: payload["parameters"] = parameters
    return urllib.error.HTTPError("https://redacted", status, "error", {}, io.BytesIO(json.dumps(payload).encode()))


class TelegramDiagnosticsTests(unittest.TestCase):
    def test_basic_send_for_chat_types_never_uses_thread(self):
        for chat_id in ("123456", "-123456", "-1001234567890"):
            with self.subTest(chat_id=chat_id), mock.patch("urllib.request.urlopen", return_value=Response({"ok": True, "result": {"message_id": 9}})) as opened:
                TelegramTransport().send(TOKEN, chat_id, "Teste do Backup Manager Local realizado com sucesso.")
                data = opened.call_args.args[0].data
                self.assertIn(f"chat_id={chat_id}".encode(), data)
                self.assertNotIn(b"message_thread_id", data)

    def test_supergroup_send_serializes_integer_topic_and_parse_mode(self):
        with mock.patch("urllib.request.urlopen", return_value=Response({"ok": True, "result": {"message_id": 9}})) as opened:
            TelegramTransport().send(TOKEN, "-1001234567890", "Resumo", 1411, "HTML")
        data = opened.call_args.args[0].data
        self.assertIn(b"chat_id=-1001234567890", data)
        self.assertIn(b"message_thread_id=1411", data)
        self.assertIn(b"parse_mode=HTML", data)

    def test_diagnose_success_and_optional_send(self):
        replies = [Response({"ok": True, "result": {"id": 7, "username": "backup_bot"}}),
                   Response({"ok": True, "result": {"id": -1001, "title": "NOC", "type": "supergroup", "is_forum": True}}),
                   Response({"ok": True, "result": {"status": "administrator", "can_post_messages": True}}),
                   Response({"ok": True, "result": {"message_id": 42}})]
        with mock.patch("urllib.request.urlopen", side_effect=replies) as opened:
            result = TelegramTransport().diagnose(TOKEN, "-1001", True)
        self.assertEqual("NOC", result["chat_title"]); self.assertTrue(result["can_send"])
        self.assertTrue(result["is_forum"])
        self.assertEqual("42", result["sent_message_id"]); self.assertEqual(4, opened.call_count)
        request = opened.call_args_list[-1].args[0]
        self.assertTrue(request.full_url.endswith("/sendMessage")); self.assertEqual("POST", request.method)
        self.assertIn(b"chat_id=-1001", request.data)

    def assert_api_error(self, status, description, friendly, parameters=None):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(status, description, parameters)):
            with self.assertRaises(TelegramAPIError) as caught: TelegramTransport().call(TOKEN, "getMe")
        error = caught.exception
        self.assertIn(friendly, error.friendly_message); self.assertNotIn(TOKEN, str(error))
        return error

    def test_invalid_token(self): self.assert_api_error(401, "Unauthorized", "inválido")
    def test_chat_not_found(self): self.assert_api_error(400, "Bad Request: chat not found", "não foi encontrado")
    def test_bot_kicked(self): self.assert_api_error(403, "Forbidden: bot was kicked", "removido")
    def test_insufficient_rights(self): self.assert_api_error(403, "Forbidden: not enough rights", "não possui permissão")
    def test_thread_not_found(self): self.assert_api_error(400, "Bad Request: message thread not found", "tópico")
    def test_closed_general_topic(self): self.assert_api_error(400, "Bad Request: TOPIC_CLOSED", "Geral")

    def test_migrated_group(self):
        error = self.assert_api_error(400, "Bad Request: group migrated", "group migrated", {"migrate_to_chat_id": -1009})
        self.assertEqual("-1009", error.migrate_to_chat_id)

    def test_rate_limit(self):
        error = self.assert_api_error(429, "Too Many Requests", "Limite temporário", {"retry_after": 17})
        self.assertEqual(17, error.retry_after)

    def test_server_error(self): self.assert_api_error(500, "Internal Server Error", "temporariamente indisponível")

    def test_timeout_and_sanitization(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError()):
            with self.assertRaises(TelegramAPIError) as caught: TelegramTransport().call(TOKEN, "getMe")
        self.assertIn("conectar", caught.exception.friendly_message)
        error = self.assert_api_error(400, f"chat not found\nhttps://x/{TOKEN}", "não foi encontrado")
        self.assertNotIn(TOKEN, error.description); self.assertNotIn("https://", error.description)

    def test_member_outside_group_cannot_send(self):
        replies = [Response({"ok": True, "result": {"id": 7, "username": "bot"}}),
                   Response({"ok": True, "result": {"title": "NOC", "type": "group"}}),
                   Response({"ok": True, "result": {"status": "left"}})]
        with mock.patch("urllib.request.urlopen", side_effect=replies):
            self.assertFalse(TelegramTransport().diagnose(TOKEN, "-1")["can_send"])


if __name__ == "__main__": unittest.main()
