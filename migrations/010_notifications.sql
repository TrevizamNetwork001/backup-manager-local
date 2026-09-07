CREATE TABLE notification_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_type TEXT NOT NULL UNIQUE,
    is_enabled INTEGER NOT NULL DEFAULT 0,
    token_encrypted TEXT NOT NULL DEFAULT '',
    chat_id TEXT NOT NULL DEFAULT '',
    cooldown_minutes INTEGER NOT NULL DEFAULT 60,
    maintenance_enabled INTEGER NOT NULL DEFAULT 0,
    maintenance_start TEXT NOT NULL DEFAULT '00:00',
    maintenance_end TEXT NOT NULL DEFAULT '00:00',
    timezone TEXT NOT NULL DEFAULT 'America/Sao_Paulo',
    daily_summary_enabled INTEGER NOT NULL DEFAULT 1,
    daily_summary_time TEXT NOT NULL DEFAULT '08:00',
    weekly_summary_enabled INTEGER NOT NULL DEFAULT 1,
    weekly_summary_day INTEGER NOT NULL DEFAULT 0,
    weekly_summary_time TEXT NOT NULL DEFAULT '08:00',
    last_test_at TEXT,
    last_status TEXT NOT NULL DEFAULT 'not_configured',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (channel_type IN ('telegram')),
    CHECK (cooldown_minutes BETWEEN 1 AND 10080),
    CHECK (weekly_summary_day BETWEEN 0 AND 6)
);

INSERT INTO notification_channels(channel_type) VALUES('telegram');

CREATE TABLE notification_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    channel_id INTEGER NOT NULL REFERENCES notification_channels(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    subject TEXT NOT NULL,
    message TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    next_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_entity TEXT NOT NULL DEFAULT '',
    source_entity_id TEXT NOT NULL DEFAULT '',
    source_audit_id INTEGER REFERENCES audit_log(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT,
    CHECK (severity IN ('info', 'warning', 'critical', 'recovery')),
    CHECK (status IN ('pending', 'processing', 'sent', 'failed', 'suppressed'))
);

CREATE INDEX idx_notification_queue_due ON notification_queue(status,next_attempt_at);
CREATE UNIQUE INDEX idx_notification_queue_audit_channel ON notification_queue(source_audit_id,channel_id) WHERE source_audit_id IS NOT NULL;

CREATE TABLE notification_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL REFERENCES notification_queue(id) ON DELETE CASCADE,
    channel_type TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    response_code INTEGER,
    error_code TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status IN ('sent', 'retry', 'failed', 'suppressed'))
);

CREATE INDEX idx_notification_history_created ON notification_history(created_at);

CREATE TABLE notification_states (
    dedup_key TEXT PRIMARY KEY,
    is_active INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT,
    last_sent_at TEXT,
    recovered_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE notification_cursors (
    source TEXT PRIMARY KEY,
    last_id INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO notification_cursors(source,last_id) VALUES('audit_log',0);
