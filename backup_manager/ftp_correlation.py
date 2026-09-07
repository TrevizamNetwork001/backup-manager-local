from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .mikrotik_ftp_uploads import (
    RetryableUploadValidationError,
    UnmanagedUpload,
    UploadValidationError,
    begin_validation,
)


@dataclass(frozen=True)
class CorrelationDecision:
    action: str
    match: object | None = None
    code: str = ""
    message: str = ""


def correlate(conn, *, account_id: int, received, filename: str, size: int,
              mtime_ns: int, current: datetime, pair_grace_seconds: int = 300) -> CorrelationDecision:
    """Classify one stable upload without moving or storing its file."""
    try:
        match = begin_validation(
            conn, account_id=account_id, received_file_id=received["id"],
            filename=filename, size=size, now=current, mtime_ns=mtime_ns,
        )
        return CorrelationDecision("managed", match=match)
    except UnmanagedUpload:
        return CorrelationDecision("unmanaged", code="ignored_unmanaged",
                                   message="Arquivo não gerenciado; preservado no diretório FTP.")
    except RetryableUploadValidationError as exc:
        detected_at = datetime.fromisoformat(received["detected_at"].replace(" ", "T") + "+00:00")
        action = "waiting_pair" if (current - detected_at).total_seconds() < pair_grace_seconds else "reject"
        return CorrelationDecision(action, code=exc.code if action == "waiting_pair" else "pair_timeout",
                                   message=exc.safe_message)
    except UploadValidationError as exc:
        return CorrelationDecision("reject", code=exc.code, message=exc.safe_message)
