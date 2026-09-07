from __future__ import annotations

import json


def updates_page_context(conn) -> dict[str, object]:
    history = conn.execute(
        """
        SELECT uuid, from_version, to_version, status, requested_at, finished_at, error_code
        FROM update_operations
        ORDER BY id DESC
        LIMIT 50
        """
    ).fetchall()
    settings = {
        row["key"]: row["value"]
        for row in conn.execute("SELECT key,value FROM settings WHERE key LIKE 'update_%'")
    }
    downloads = conn.execute(
        """
        SELECT uuid, version, status, bytes_downloaded, bytes_total, error_code, created_at
        FROM update_downloads
        ORDER BY id DESC
        LIMIT 20
        """
    ).fetchall()
    release: dict[str, object] = {}
    try:
        release = json.loads(settings.get("update_available_release_json", "{}"))
    except ValueError:
        release = {}
    return {
        "history": history,
        "settings": settings,
        "downloads": downloads,
        "release": release,
    }
