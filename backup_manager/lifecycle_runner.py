from __future__ import annotations

from .db import SchemaCompatibilityError, connect, require_schema_current
from .lifecycle import execute


def main() -> None:
    try:
        require_schema_current()
    except SchemaCompatibilityError as exc:
        print(exc.code)
        raise SystemExit(3)
    with connect() as conn:
        enabled = conn.execute("SELECT value FROM settings WHERE key='lifecycle_enabled'").fetchone()
        if not enabled or enabled[0] != "1":
            print("lifecycle disabled")
            return
        report = execute(conn, confirmation="", automatic=True)
    print(f"moved={report['moved_count']} purged={report['purged_count']} rejected={report['rejected_count']}")


if __name__ == "__main__":
    main()
