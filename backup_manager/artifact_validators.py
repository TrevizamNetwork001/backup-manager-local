from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

MAX_TEXT_SCAN_BYTES = 2 * 1024 * 1024
DEFAULT_BINARY_MIN_BYTES = 128


@dataclass(frozen=True)
class ValidationResult:
    state: str
    code: str
    message: str
    size_bytes: int
    sha256: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state not in {"valid", "invalid", "suspicious"}:
            raise ValueError("Resultado de validação inválido.")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


def _read_and_hash(path: str | Path) -> tuple[bytes, int, str]:
    target = Path(path)
    digest = hashlib.sha256()
    preview = bytearray()
    size = 0
    with target.open("rb") as source:
        while block := source.read(1024 * 1024):
            size += len(block)
            digest.update(block)
            if len(preview) < MAX_TEXT_SCAN_BYTES:
                preview.extend(block[:MAX_TEXT_SCAN_BYTES - len(preview)])
    return bytes(preview), size, digest.hexdigest()


def _result(state: str, code: str, message: str, size: int, digest: str, **metadata) -> ValidationResult:
    return ValidationResult(state, code, message[:240], size, digest, metadata)


def _hash_mismatch(size: int, digest: str) -> ValidationResult:
    return _result("invalid", "sha256_mismatch", "SHA-256 informado não corresponde ao arquivo.", size, digest)


HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body")
LOGIN_MARKERS = ("login", "sign in", "username", "authentication required")
ERROR_MARKERS = (
    "authentication failed", "permission denied", "invalid credentials", "login failed",
    "error:", "failure:", "syntax error", "bad command name", "unknown command",
)
SENSITIVE_PATTERNS = (
    re.compile(r"(?im)^\s*/user\s+(?:add|set)\b"),
    re.compile(r"(?i)\bgroup\s*=\s*full\b"),
    re.compile(r"(?i)\bpassword\s*="),
    re.compile(r"(?im)^\s*/system\s+(?:script|scheduler)\s+add\b"),
    re.compile(r"(?im)^\s*/tool\s+fetch\b"),
    re.compile(r"(?i)\b(?:tokens?|secrets?|api[_-]?keys?)\s*="),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class RouterOSExportValidator:
    def validate(self, path: str | Path, *, expected_sha256: str | None = None) -> ValidationResult:
        preview, size, digest = _read_and_hash(path)
        if expected_sha256 is not None and expected_sha256.lower() != digest:
            return _hash_mismatch(size, digest)
        if size == 0:
            return _result("invalid", "empty_output", "Export RouterOS vazio.", size, digest)
        lowered_bytes = preview.lstrip().lower()
        if any(marker in lowered_bytes[:4096] for marker in HTML_MARKERS):
            return _result("invalid", "html_content", "Conteúdo HTML não é um export RouterOS.", size, digest)
        text = preview.decode("utf-8", errors="replace")
        lowered = text.casefold()
        if any(marker in lowered[:8192] for marker in LOGIN_MARKERS) and not re.search(r"(?m)^\s*/", text):
            return _result("invalid", "login_page", "Página ou prompt de login recebido no lugar do export.", size, digest)
        if any(marker in lowered for marker in ERROR_MARKERS):
            return _result("invalid", "command_error", "Saída contém mensagem de autenticação ou erro.", size, digest)
        routeros_structure = bool(
            re.search(r"(?m)^\s*#.*(?:RouterOS|software id|model)", text, re.IGNORECASE)
            or re.search(r"(?m)^\s*/(?:interface|ip|ipv6|system|routing|queue|user|tool)\b", text)
        )
        if not routeros_structure:
            return _result("invalid", "incompatible_export", "Conteúdo incompatível com export RouterOS.", size, digest)
        sensitive_count = sum(len(pattern.findall(text)) for pattern in SENSITIVE_PATTERNS)
        if sensitive_count:
            return _result("suspicious", "sensitive_commands", "Export contém padrões sensíveis e requer revisão protegida.",
                           size, digest, sensitive_pattern_count=sensitive_count, scanned_bytes=len(preview))
        return _result("valid", "routeros_export_valid", "Export RouterOS validado.", size, digest,
                       scanned_bytes=len(preview), truncated_scan=size > len(preview))


class RouterOSBinaryBackupValidator:
    def __init__(self, minimum_bytes: int = DEFAULT_BINARY_MIN_BYTES) -> None:
        if minimum_bytes < 1:
            raise ValueError("Tamanho mínimo inválido.")
        self.minimum_bytes = minimum_bytes

    def validate(self, path: str | Path, *, expected_sha256: str | None = None) -> ValidationResult:
        preview, size, digest = _read_and_hash(path)
        if expected_sha256 is not None and expected_sha256.lower() != digest:
            return _hash_mismatch(size, digest)
        if size == 0:
            return _result("invalid", "empty_backup", "Backup RouterOS vazio.", size, digest)
        if size < self.minimum_bytes:
            return _result("invalid", "backup_too_small", "Backup RouterOS abaixo do tamanho mínimo.", size, digest,
                           minimum_bytes=self.minimum_bytes)
        lowered = preview.lstrip().lower()
        if any(marker in lowered[:4096] for marker in HTML_MARKERS):
            return _result("invalid", "html_content", "Conteúdo HTML não é um backup binário RouterOS.", size, digest)
        sample = preview[:65536]
        printable = sum(byte in b"\t\n\r" or 32 <= byte <= 126 for byte in sample)
        printable_ratio = printable / len(sample) if sample else 1.0
        if printable_ratio >= 0.85:
            return _result("invalid", "text_content", "Conteúdo textual incompatível com backup binário RouterOS.",
                           size, digest, printable_ratio=round(printable_ratio, 3))
        return _result("valid", "routeros_binary_valid", "Backup binário RouterOS passou pelas validações disponíveis.",
                       size, digest, minimum_bytes=self.minimum_bytes)
