from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .security import decrypt_secret, encrypt_secret


AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive.file"


class GoogleOAuthError(RuntimeError):
    def __init__(self, code: str, safe_message: str):
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(frozen=True)
class GoogleOAuthResult:
    target_id: int
    client_id: str
    client_secret: str
    token: str


def _session_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def begin(conn, target_id: int, client_id: str, client_secret: str, redirect_uri: str,
          *, user_id: int, session_token: str) -> str:
    client_id = client_id.strip()
    if not client_id.endswith(".apps.googleusercontent.com") or not client_secret or not session_token:
        raise ValueError("Informe as credenciais OAuth do tipo Aplicativo da Web.")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""INSERT INTO google_drive_connections(
        target_id,client_id,client_secret_encrypted,token_status,oauth_state_hash,
        pkce_verifier_encrypted,oauth_expires_at,oauth_session_hash,oauth_user_id,
        oauth_redirect_uri,oauth_consumed_at)
      VALUES(?,?,?,'pending',?,?,?,?,?,?,NULL)
      ON CONFLICT(target_id) DO UPDATE SET client_id=excluded.client_id,
        client_secret_encrypted=excluded.client_secret_encrypted,refresh_token_encrypted='',
        token_status='pending',oauth_state_hash=excluded.oauth_state_hash,
        pkce_verifier_encrypted=excluded.pkce_verifier_encrypted,
        oauth_expires_at=excluded.oauth_expires_at,oauth_session_hash=excluded.oauth_session_hash,
        oauth_user_id=excluded.oauth_user_id,oauth_redirect_uri=excluded.oauth_redirect_uri,
        oauth_consumed_at=NULL,updated_at=CURRENT_TIMESTAMP""",
      (target_id, client_id, encrypt_secret(client_secret), _session_hash(state),
       encrypt_secret(verifier), expires, _session_hash(session_token), user_id, redirect_uri))
    params = {
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent select_account",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def target_for_state(conn, state: str) -> int:
    digest = _session_hash(state or "")
    row = conn.execute("SELECT target_id FROM google_drive_connections WHERE oauth_state_hash=? AND token_status='pending'", (digest,)).fetchone()
    if not row:
        raise GoogleOAuthError("GOOGLE_OAUTH_STATE_INVALID", "A autorização expirou ou não pertence a esta conexão.")
    return int(row[0])


def complete(conn, target_id: int, code: str, state: str, redirect_uri: str,
             *, user_id: int, session_token: str, opener=None) -> GoogleOAuthResult:
    row = conn.execute("SELECT * FROM google_drive_connections WHERE target_id=?", (target_id,)).fetchone()
    if (not row or row["token_status"] != "pending" or row["oauth_consumed_at"] or
            not secrets.compare_digest(_session_hash(state or ""), row["oauth_state_hash"] or "")):
        raise GoogleOAuthError("GOOGLE_OAUTH_STATE_INVALID", "A autorização expirou ou já foi utilizada.")
    if row["oauth_user_id"] != user_id or not secrets.compare_digest(row["oauth_session_hash"] or "", _session_hash(session_token or "")):
        raise GoogleOAuthError("GOOGLE_OAUTH_SESSION_INVALID", "A autorização não pertence a esta sessão administrativa.")
    if row["oauth_redirect_uri"] != redirect_uri:
        raise GoogleOAuthError("GOOGLE_REDIRECT_URI_MISMATCH", "A URL de retorno diverge da autorização iniciada.")
    expires = datetime.strptime(row["oauth_expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise GoogleOAuthError("GOOGLE_OAUTH_STATE_INVALID", "A autorização expirou. Inicie novamente.")
    claimed = conn.execute("UPDATE google_drive_connections SET oauth_consumed_at=CURRENT_TIMESTAMP WHERE target_id=? AND oauth_consumed_at IS NULL", (target_id,))
    if claimed.rowcount != 1:
        raise GoogleOAuthError("GOOGLE_OAUTH_STATE_INVALID", "A autorização já foi utilizada.")
    conn.commit()
    client_secret = decrypt_secret(row["client_secret_encrypted"])
    data = urllib.parse.urlencode({
        "client_id": row["client_id"], "client_secret": client_secret, "code": code,
        "code_verifier": decrypt_secret(row["pkce_verifier_encrypted"]),
        "grant_type": "authorization_code", "redirect_uri": redirect_uri,
    }).encode()
    request = urllib.request.Request(TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with (opener or urllib.request.urlopen)(request, timeout=20) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        exc.read(65536)
        raise GoogleOAuthError("GOOGLE_TOKEN_REJECTED", "O Google recusou a conclusão da autorização.") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeError) as exc:
        raise GoogleOAuthError("GOOGLE_OAUTH_NETWORK", "Não foi possível concluir a comunicação com o Google.") from exc
    if not payload.get("access_token") or not payload.get("refresh_token"):
        raise GoogleOAuthError("GOOGLE_TOKEN_INCOMPLETE", "O Google não retornou a autorização offline completa.")
    granted = str(payload.get("scope", SCOPE)).split()
    if SCOPE not in granted:
        raise GoogleOAuthError("GOOGLE_SCOPE_DENIED", "A permissão necessária do Google Drive não foi concedida.")
    expiry = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in", 3600)))
    token = json.dumps({
        "access_token": payload["access_token"], "token_type": payload.get("token_type", "Bearer"),
        "refresh_token": payload["refresh_token"], "expiry": expiry.isoformat().replace("+00:00", "Z"),
    }, separators=(",", ":"))
    conn.execute("""UPDATE google_drive_connections SET refresh_token_encrypted=?,granted_scope=?,
        token_status='connected',oauth_state_hash=NULL,pkce_verifier_encrypted=NULL,
        oauth_expires_at=NULL,connected_at=CURRENT_TIMESTAMP,revoked_at=NULL,
        updated_at=CURRENT_TIMESTAMP WHERE target_id=?""",
        (encrypt_secret(payload["refresh_token"]), " ".join(granted), target_id))
    return GoogleOAuthResult(target_id, str(row["client_id"]), client_secret, token)
