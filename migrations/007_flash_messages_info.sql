CREATE TABLE flash_messages_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_token TEXT NOT NULL REFERENCES sessions(token) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (kind IN ('error', 'success', 'info'))
);

INSERT INTO flash_messages_new(id, session_token, kind, text, created_at)
SELECT id, session_token, kind, text, created_at FROM flash_messages;

DROP TABLE flash_messages;
ALTER TABLE flash_messages_new RENAME TO flash_messages;

CREATE INDEX idx_flash_messages_session_token ON flash_messages(session_token, id);
