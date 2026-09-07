from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import uuid
from typing import Iterable

ARTIFACT_STATES = frozenset({
    "expected", "waiting", "received", "validating", "valid", "invalid",
    "suspicious", "duplicate", "expired", "late",
})
OPERATION_STATES = frozenset({"waiting_upload", "validating", "success", "failed", "expired"})
MIKROTIK_ARTIFACT_TYPES = frozenset({"backup", "rsc"})
CORRELATION_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,199}$")


@dataclass(frozen=True)
class ArtifactSnapshot:
    artifact_type: str
    is_required: bool
    state: str

    def __post_init__(self) -> None:
        if not self.artifact_type.strip():
            raise ValueError("Tipo de artefato ausente.")
        if self.state not in ARTIFACT_STATES:
            raise ValueError("Estado de artefato inválido.")


@dataclass(frozen=True)
class OperationReduction:
    status: str
    code: str = ""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Data deve possuir timezone.")
    return value.astimezone(timezone.utc)


def reduce_operation_state(
    artifacts: Iterable[ArtifactSnapshot], *, deadline: datetime, now: datetime
) -> OperationReduction:
    """Reduce immutable artifact snapshots without database, network or filesystem access."""
    items = tuple(artifacts)
    due = _utc(now) >= _utc(deadline)
    required = tuple(item for item in items if item.is_required)

    if any(item.state in {"invalid", "suspicious", "duplicate"} for item in required):
        return OperationReduction("failed", "required_artifact_rejected")
    if any(item.state in {"expired", "late"} for item in required):
        return OperationReduction("expired", "required_artifact_expired")
    if not required or all(item.state == "valid" for item in required):
        return OperationReduction("success")
    if any(item.state in {"received", "validating"} for item in required):
        return OperationReduction("expired" if due else "validating",
                                  "required_artifact_expired" if due else "")
    return OperationReduction("expired" if due else "waiting_upload",
                              "required_artifact_expired" if due else "")


def artifacts_v2_enabled(conn) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key='backup_artifacts_v2_enabled'").fetchone()
    return bool(row and row[0] == "1")


def ensure_mikrotik_operation(conn, *, provider_key: str, equipment_id: int,
                              correlation_key: str, artifact_types: Iterable[str],
                              deadline: datetime, now: datetime):
    """Create or return one aggregate operation and its fixed expectations."""
    types = tuple(dict.fromkeys(artifact_types))
    if not types or any(item not in MIKROTIK_ARTIFACT_TYPES for item in types):
        raise ValueError("Tipos de artefato inválidos.")
    if not CORRELATION_RE.fullmatch(correlation_key):
        raise ValueError("Chave de correlação inválida.")
    stamp = _utc(now).strftime("%Y-%m-%d %H:%M:%S")
    due = _utc(deadline).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO backup_operations
           (uuid,provider_key,equipment_id,correlation_key,source_method,status,deadline_at,started_at)
           VALUES(?,?,?,?, 'ftp','waiting_upload',?,?)
           ON CONFLICT(provider_key,equipment_id,correlation_key) DO NOTHING""",
        (str(uuid.uuid4()), provider_key, equipment_id, correlation_key, due, stamp),
    )
    operation = conn.execute(
        "SELECT * FROM backup_operations WHERE provider_key=? AND equipment_id=? AND correlation_key=?",
        (provider_key, equipment_id, correlation_key),
    ).fetchone()
    for artifact_type in types:
        conn.execute(
            """INSERT INTO backup_operation_artifacts
               (uuid,operation_id,artifact_type,is_required,state,deadline_at)
               VALUES(?,?,?,1,'expected',?)
               ON CONFLICT(operation_id,artifact_type) DO NOTHING""",
            (str(uuid.uuid4()), operation["id"], artifact_type, operation["deadline_at"]),
        )
    existing = conn.execute(
        "SELECT artifact_type FROM backup_operation_artifacts WHERE operation_id=? ORDER BY artifact_type",
        (operation["id"],),
    ).fetchall()
    if {row[0] for row in existing} != set(types):
        raise ValueError("Expectativas incompatíveis com a operação existente.")
    return operation


def reduce_persisted_operation(conn, operation_id: int, *, now: datetime) -> tuple[object, bool]:
    operation = conn.execute("SELECT * FROM backup_operations WHERE id=?", (operation_id,)).fetchone()
    rows = conn.execute(
        "SELECT artifact_type,is_required,state FROM backup_operation_artifacts WHERE operation_id=?",
        (operation_id,),
    ).fetchall()
    reduction = reduce_operation_state(
        (ArtifactSnapshot(row["artifact_type"], bool(row["is_required"]), row["state"]) for row in rows),
        deadline=datetime.fromisoformat(operation["deadline_at"].replace(" ", "T") + "+00:00"), now=now,
    )
    transitioned = operation["status"] != "success" and reduction.status == "success"
    stamp = _utc(now).strftime("%Y-%m-%d %H:%M:%S")
    completed = stamp if reduction.status in {"success", "failed", "expired"} else None
    message = {
        "required_artifact_rejected": "Artefato obrigatório rejeitado.",
        "required_artifact_expired": "Prazo de artefato obrigatório expirado.",
    }.get(reduction.code, "")
    conn.execute(
        """UPDATE backup_operations SET status=?,error_code=?,error_message=?,completed_at=?,updated_at=?
           WHERE id=?""",
        (reduction.status, reduction.code, message, completed, stamp, operation_id),
    )
    return reduction, transitioned


def expire_due_operations(conn, *, now: datetime) -> int:
    stamp = _utc(now).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """SELECT id FROM backup_operations
           WHERE provider_key='mikrotik_ftp' AND status IN ('waiting_upload','validating') AND deadline_at<=?""",
        (stamp,),
    ).fetchall()
    for row in rows:
        conn.execute(
            """UPDATE backup_operation_artifacts SET state='expired',validation_code='artifact_expired',
               validation_message='Artefato não recebido dentro do prazo.',completed_at=?,updated_at=?
               WHERE operation_id=? AND state IN ('expected','waiting')""",
            (stamp, stamp, row["id"]),
        )
        reduce_persisted_operation(conn, row["id"], now=now)
    return len(rows)


def safe_validation_metadata(result) -> str:
    allowed = {key: value for key, value in result.metadata.items()
               if key in {"scanned_bytes", "truncated_scan", "minimum_bytes", "printable_ratio"}}
    return json.dumps(allowed, separators=(",", ":"), sort_keys=True)
