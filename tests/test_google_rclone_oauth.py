from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.parse
import uuid

os.environ.setdefault("BACKUP_MANAGER_DATA", tempfile.mkdtemp(prefix="bm-google-rclone-data-"))
os.environ.setdefault("BACKUP_MANAGER_DB", os.path.join(os.environ["BACKUP_MANAGER_DATA"], "google-rclone.sqlite3"))
os.environ.setdefault("BACKUP_MANAGER_SECRET_KEY", os.path.join(os.environ["BACKUP_MANAGER_DATA"], "secret.key"))

from backup_manager.db import connect, migrate
from backup_manager.google_rclone_oauth import GoogleOAuthError, SCOPE, begin, complete, target_for_state


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class GoogleRcloneOAuthTest(unittest.TestCase):
    def setUp(self):
        migrate(31)
        with connect() as conn:
            conn.execute("DELETE FROM cloud_sync_items")
            conn.execute("DELETE FROM google_drive_connections")
            conn.execute("DELETE FROM rclone_connections")
            conn.execute("DELETE FROM cloud_sync_policies")
            conn.execute("DELETE FROM cloud_targets")
            cursor = conn.execute("INSERT INTO cloud_targets(uuid,name,provider,mode) VALUES(?,?,'simulate','simulate')", (str(uuid.uuid4()), "Google"))
            self.target_id = cursor.lastrowid

    def tearDown(self):
        with connect() as conn:
            conn.execute("DELETE FROM cloud_sync_items")
            conn.execute("DELETE FROM google_drive_connections")
            conn.execute("DELETE FROM rclone_connections")
            conn.execute("DELETE FROM cloud_sync_policies")
            conn.execute("DELETE FROM cloud_targets")

    def test_browser_oauth_uses_pkce_state_and_returns_rclone_token(self):
        redirect_uri = "https://backup.example.com/cloud/rclone/google/callback"
        with connect() as conn:
            url = begin(conn, self.target_id, "client.apps.googleusercontent.com", "secret", redirect_uri,
                        user_id=1, session_token="admin-session")
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            state = query["state"][0]
            self.assertEqual(query["code_challenge_method"], ["S256"])
            self.assertEqual(query["scope"], [SCOPE])
            self.assertEqual(target_for_state(conn, state), self.target_id)

            def opener(request, timeout):
                self.assertEqual(timeout, 20)
                submitted = urllib.parse.parse_qs(request.data.decode())
                self.assertEqual(submitted["code"], ["authorization-code"])
                self.assertIn("code_verifier", submitted)
                return FakeResponse({"access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
                                     "scope": SCOPE, "token_type": "Bearer"})

            result = complete(conn, self.target_id, "authorization-code", state, redirect_uri,
                              user_id=1, session_token="admin-session", opener=opener)
            token = json.loads(result.token)
            row = conn.execute("SELECT * FROM google_drive_connections WHERE target_id=?", (self.target_id,)).fetchone()
        self.assertEqual(token["refresh_token"], "refresh")
        self.assertEqual(row["token_status"], "connected")
        self.assertNotIn("refresh", row["refresh_token_encrypted"])
        self.assertNotIn("secret", row["client_secret_encrypted"])

    def test_oauth_rejects_wrong_session_and_reused_state(self):
        redirect_uri = "https://backup.example.com/cloud/rclone/google/callback"
        with connect() as conn:
            url = begin(conn, self.target_id, "client.apps.googleusercontent.com", "secret", redirect_uri,
                        user_id=1, session_token="admin-session")
            state = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]
            with self.assertRaises(GoogleOAuthError):
                complete(conn, self.target_id, "code", state, redirect_uri, user_id=1,
                         session_token="other-session", opener=lambda *_args, **_kwargs: None)
            with self.assertRaises(GoogleOAuthError):
                complete(conn, self.target_id, "code", state, "https://other.example.com/callback",
                         user_id=1, session_token="admin-session", opener=lambda *_args, **_kwargs: None)


if __name__ == "__main__":
    unittest.main()
