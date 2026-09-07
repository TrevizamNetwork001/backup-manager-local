from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


DEFAULT_SECRET_KEY_PATH = "/etc/backup-manager-local/secret.key"


class SecretKeyError(RuntimeError):
    pass


def hash_password(password: str, salt: str | None = None) -> str:
    if salt is None:
        salt = base64.urlsafe_b64encode(os.urandom(16)).decode("ascii")
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("ascii"),
        240_000,
    )
    encoded = base64.urlsafe_b64encode(digest).decode("ascii")
    return f"pbkdf2_sha256$240000${salt}${encoded}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256" or iterations != "240000":
        return False
    candidate = hash_password(password, salt).split("$", 3)[3]
    return hmac.compare_digest(candidate, expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def secret_key_path() -> Path:
    return Path(os.environ.get("BACKUP_MANAGER_SECRET_KEY", DEFAULT_SECRET_KEY_PATH))


def ensure_secret_key() -> Path:
    path = secret_key_path()
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o640)
    with os.fdopen(fd, "wb") as handle:
        handle.write(Fernet.generate_key())
        handle.write(b"\n")
    try:
        path.chmod(0o640)
    except OSError:
        pass
    return path


def _load_fernet() -> Fernet:
    path = secret_key_path()
    if not path.exists():
        try:
            path = ensure_secret_key()
        except OSError as exc:
            raise SecretKeyError("Chave local de criptografia indisponivel.") from exc
    try:
        info=path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o037:
            raise SecretKeyError("Permissões da chave local de criptografia são inseguras.")
        key = path.read_bytes().strip()
        return Fernet(key)
    except SecretKeyError:
        raise
    except Exception as exc:
        raise SecretKeyError("Chave local de criptografia nao pode ser lida.") from exc


def encrypt_secret(value: str) -> str:
    if value == "":
        return ""
    return _load_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _load_fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError) as exc:
        raise SecretKeyError("Credencial criptografada nao pode ser aberta.") from exc
