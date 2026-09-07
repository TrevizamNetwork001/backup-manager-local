from __future__ import annotations

from .db import SchemaCompatibilityError, connect, require_schema_current, schema_problems
from .jobs import run_once
from .notifications import run_cycle


def main() -> None:
    try:
        require_schema_current()
    except SchemaCompatibilityError as exc:
        print(exc.code)
        raise SystemExit(3)
    with connect() as conn:
        missing = schema_problems(conn)
        if missing:
            raise SystemExit(f"SCHEMA_OUTDATED missing={','.join(missing)}; execute backup_manager.cli migrate")
        result = run_once(conn)
        notifications = run_cycle(conn)
    print(
        "jobs-run-once "
        f"queued={result.queued_runs} processed={result.processed_runs} "
        f"success={result.success_runs} failed={result.failed_runs} "
        f"timeout={result.timeout_runs} skipped={result.skipped_runs}"
        f" notifications_queued={notifications.queued} notifications_sent={notifications.sent}"
        f" notifications_retry={notifications.retried} notifications_failed={notifications.failed}"
    )


if __name__ == "__main__":
    main()
