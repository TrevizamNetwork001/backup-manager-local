CREATE TABLE flash_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_token TEXT NOT NULL REFERENCES sessions(token) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (kind IN ('error', 'success', 'info'))
);

CREATE INDEX idx_flash_messages_session_token ON flash_messages(session_token, id);
