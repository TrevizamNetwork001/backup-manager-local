ALTER TABLE telegram_destinations ADD COLUMN disabled_at TEXT;
ALTER TABLE telegram_destinations ADD COLUMN disabled_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE telegram_destinations ADD COLUMN last_verified_at TEXT;
ALTER TABLE telegram_destinations ADD COLUMN last_success_at TEXT;
ALTER TABLE telegram_destinations ADD COLUMN last_failure_at TEXT;
ALTER TABLE telegram_destinations ADD COLUMN sanitized_last_error TEXT NOT NULL DEFAULT '';

CREATE TABLE telegram_destination_actions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT NOT NULL UNIQUE,
 destination_id INTEGER NOT NULL REFERENCES telegram_destinations(id) ON DELETE RESTRICT,
 user_id INTEGER REFERENCES users(id),
 action TEXT NOT NULL CHECK(action IN ('disable','cancel_pending','transfer_pending')),
 item_type TEXT NOT NULL CHECK(item_type IN ('test','summary','alert','backup_file','all')),
 item_count INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_telegram_destination_actions_destination ON telegram_destination_actions(destination_id,created_at);
