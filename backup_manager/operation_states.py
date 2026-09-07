from __future__ import annotations


CANONICAL_OPERATION_STATES = frozenset({"pending", "running", "success", "failed", "cancelled", "expired"})

DOMAIN_STATE_MAPS = {
    "mikrotik_ftp_tests": {
        "pending": "pending", "running": "running", "waiting_upload": "running",
        "validating": "running", "validated": "success", "failed": "failed",
        "expired": "expired", "cancelled": "cancelled",
    },
    "backup_operations": {
        "waiting_upload": "pending", "validating": "running", "success": "success",
        "failed": "failed", "expired": "expired",
    },
    "mikrotik_ftp_uploads": {
        "receiving": "running", "validating": "running", "success": "success",
        "failed": "failed", "timeout": "expired", "incomplete": "failed",
        "invalid_file": "failed", "duplicate": "success",
    },
    "ftp_received_files": {
        "detected": "pending", "waiting_stable": "pending", "processing": "running",
        "imported": "success", "rejected": "failed", "failed": "failed", "duplicate": "success",
    },
    "backups": {
        "receiving": "pending", "validating": "running", "available": "success",
        "quarantined": "failed", "failed": "failed", "trashed": "cancelled", "deleted": "cancelled",
    },
}


def canonical_operation_state(domain: str, state: str) -> str:
    try:
        return DOMAIN_STATE_MAPS[domain][state]
    except KeyError as exc:
        raise ValueError(f"Estado desconhecido para {domain}: {state}") from exc
