-- Complemento idempotente para instalações que receberam a migration 017 durante o RC.
CREATE TABLE IF NOT EXISTS telegram_test_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    destination_id INTEGER NOT NULL REFERENCES telegram_destinations(id) ON DELETE RESTRICT,
    test_type TEXT NOT NULL CHECK(test_type IN ('file')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','sent','failed')),
    message_id TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    created_by_user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_telegram_test_status ON telegram_test_items(status,created_at);
