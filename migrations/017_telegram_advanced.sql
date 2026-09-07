-- Telegram avançado: destinos de mensagens, resumos e cópia secundária de backups.
CREATE TABLE telegram_destinations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    destination_type TEXT NOT NULL DEFAULT 'private'
        CHECK(destination_type IN ('private','group','supergroup','channel')),
    default_thread_id INTEGER CHECK(default_thread_id IS NULL OR default_thread_id > 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    use_for_alerts INTEGER NOT NULL DEFAULT 0 CHECK(use_for_alerts IN (0,1)),
    use_for_daily_summary INTEGER NOT NULL DEFAULT 0 CHECK(use_for_daily_summary IN (0,1)),
    use_for_weekly_summary INTEGER NOT NULL DEFAULT 0 CHECK(use_for_weekly_summary IN (0,1)),
    use_for_executive_summary INTEGER NOT NULL DEFAULT 0 CHECK(use_for_executive_summary IN (0,1)),
    use_for_backup_files INTEGER NOT NULL DEFAULT 0 CHECK(use_for_backup_files IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX idx_telegram_destination_chat_thread
ON telegram_destinations(chat_id,COALESCE(default_thread_id,0)) WHERE deleted_at IS NULL;
CREATE INDEX idx_telegram_destinations_active ON telegram_destinations(is_active);

INSERT INTO telegram_destinations(uuid,name,chat_id,destination_type,is_active,use_for_alerts,use_for_daily_summary,use_for_weekly_summary)
SELECT lower(hex(randomblob(16))),'Destino principal',chat_id,'private',is_enabled,1,daily_summary_enabled,weekly_summary_enabled
FROM notification_channels WHERE channel_type='telegram' AND chat_id!='';

ALTER TABLE notification_queue ADD COLUMN destination_id INTEGER REFERENCES telegram_destinations(id);
ALTER TABLE notification_queue ADD COLUMN thread_id INTEGER;
CREATE INDEX idx_notification_queue_destination ON notification_queue(destination_id);

CREATE TABLE telegram_topic_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    destination_id INTEGER NOT NULL REFERENCES telegram_destinations(id) ON DELETE RESTRICT,
    mapping_type TEXT NOT NULL CHECK(mapping_type IN ('equipment','group','method','category','event')),
    equipment_id INTEGER REFERENCES equipment(id) ON DELETE RESTRICT,
    group_id INTEGER REFERENCES equipment_groups(id) ON DELETE RESTRICT,
    match_value TEXT NOT NULL DEFAULT '',
    thread_id INTEGER NOT NULL CHECK(thread_id > 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(destination_id,mapping_type,equipment_id,group_id,match_value)
);
CREATE INDEX idx_telegram_topic_equipment ON telegram_topic_mappings(equipment_id);
CREATE INDEX idx_telegram_topic_group ON telegram_topic_mappings(group_id);

CREATE TABLE telegram_summary_settings (
    id INTEGER PRIMARY KEY CHECK(id=1),
    daily_enabled INTEGER NOT NULL DEFAULT 0 CHECK(daily_enabled IN (0,1)),
    daily_time TEXT NOT NULL DEFAULT '08:00',
    weekly_enabled INTEGER NOT NULL DEFAULT 0 CHECK(weekly_enabled IN (0,1)),
    weekly_day INTEGER NOT NULL DEFAULT 0 CHECK(weekly_day BETWEEN 0 AND 6),
    weekly_time TEXT NOT NULL DEFAULT '08:00',
    executive_enabled INTEGER NOT NULL DEFAULT 0 CHECK(executive_enabled IN (0,1)),
    executive_time TEXT NOT NULL DEFAULT '18:00',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO telegram_summary_settings(id,daily_enabled,daily_time,weekly_enabled,weekly_day,weekly_time)
SELECT 1,daily_summary_enabled,daily_summary_time,weekly_summary_enabled,weekly_summary_day,weekly_summary_time
FROM notification_channels WHERE channel_type='telegram';

CREATE TABLE telegram_summary_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    summary_type TEXT NOT NULL CHECK(summary_type IN ('daily','weekly','executive')),
    destination_id INTEGER NOT NULL REFERENCES telegram_destinations(id) ON DELETE RESTRICT,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    logical_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','sent','retry_wait','failed','skipped')),
    parts INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    next_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_telegram_summary_period ON telegram_summary_runs(period_start,period_end);
CREATE INDEX idx_telegram_summary_status ON telegram_summary_runs(status,next_attempt_at);

CREATE TABLE telegram_backup_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('global','group','equipment')),
    group_id INTEGER REFERENCES equipment_groups(id) ON DELETE RESTRICT,
    equipment_id INTEGER REFERENCES equipment(id) ON DELETE RESTRICT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    destination_id INTEGER NOT NULL REFERENCES telegram_destinations(id) ON DELETE RESTRICT,
    thread_id INTEGER CHECK(thread_id IS NULL OR thread_id > 0),
    include_ssh INTEGER NOT NULL DEFAULT 1 CHECK(include_ssh IN (0,1)),
    include_ftp INTEGER NOT NULL DEFAULT 1 CHECK(include_ftp IN (0,1)),
    include_manual INTEGER NOT NULL DEFAULT 1 CHECK(include_manual IN (0,1)),
    send_new_backups INTEGER NOT NULL DEFAULT 1 CHECK(send_new_backups IN (0,1)),
    compression_mode TEXT NOT NULL DEFAULT 'none' CHECK(compression_mode IN ('none','zip','gzip')),
    compression_level INTEGER NOT NULL DEFAULT 6 CHECK(compression_level BETWEEN 0 AND 9),
    retry_count INTEGER NOT NULL DEFAULT 5 CHECK(retry_count BETWEEN 1 AND 20),
    retry_base_seconds INTEGER NOT NULL DEFAULT 60 CHECK(retry_base_seconds BETWEEN 1 AND 86400),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    CHECK(scope_type!='group' OR group_id IS NOT NULL),
    CHECK(scope_type!='equipment' OR equipment_id IS NOT NULL)
);
CREATE UNIQUE INDEX idx_telegram_policy_global ON telegram_backup_policies(destination_id)
WHERE scope_type='global' AND deleted_at IS NULL;
CREATE UNIQUE INDEX idx_telegram_policy_group ON telegram_backup_policies(destination_id,group_id)
WHERE scope_type='group' AND deleted_at IS NULL;
CREATE UNIQUE INDEX idx_telegram_policy_equipment ON telegram_backup_policies(destination_id,equipment_id)
WHERE scope_type='equipment' AND deleted_at IS NULL;

CREATE TABLE telegram_backup_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    backup_id INTEGER NOT NULL REFERENCES backups(id) ON DELETE RESTRICT,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    destination_id INTEGER NOT NULL REFERENCES telegram_destinations(id) ON DELETE RESTRICT,
    policy_id INTEGER NOT NULL REFERENCES telegram_backup_policies(id) ON DELETE RESTRICT,
    thread_id INTEGER,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK(status IN ('queued','validating','preparing','uploading','sent','retry_wait','failed','skipped','cancelled')),
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    next_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    queued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    sent_at TEXT,
    finished_at TEXT,
    bytes_total INTEGER NOT NULL DEFAULT 0,
    bytes_sent INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    local_sha256 TEXT NOT NULL DEFAULT '',
    telegram_message_id TEXT NOT NULL DEFAULT '',
    destination_snapshot TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    safe_log TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX idx_telegram_backup_unique ON telegram_backup_items(backup_id,destination_id)
WHERE status!='cancelled';
CREATE INDEX idx_telegram_backup_status ON telegram_backup_items(status);
CREATE INDEX idx_telegram_backup_next ON telegram_backup_items(next_attempt_at);
CREATE INDEX idx_telegram_backup_backup ON telegram_backup_items(backup_id);
CREATE INDEX idx_telegram_backup_destination ON telegram_backup_items(destination_id);
CREATE INDEX idx_telegram_backup_equipment ON telegram_backup_items(equipment_id);
CREATE INDEX idx_telegram_backup_sent ON telegram_backup_items(sent_at);

CREATE TABLE telegram_test_items (
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
CREATE INDEX idx_telegram_test_status ON telegram_test_items(status,created_at);

INSERT INTO settings(key,value) VALUES
 ('telegram_daily_summary_enabled','0'),
 ('telegram_daily_summary_time','08:00'),
 ('telegram_weekly_summary_enabled','0'),
 ('telegram_weekly_summary_day','0'),
 ('telegram_weekly_summary_time','08:00'),
 ('telegram_executive_summary_enabled','0'),
 ('telegram_executive_summary_time','18:00'),
 ('telegram_backup_enabled','0'),
 ('telegram_backup_default_destination',''),
 ('telegram_backup_compression','none'),
 ('telegram_backup_max_attempts','5'),
 ('telegram_backup_retry_base_seconds','60'),
 ('telegram_backup_stuck_minutes','15'),
 ('telegram_api_mode','public'),
 ('telegram_local_api_base_url',''),
 ('telegram_public_max_file_bytes','52428800'),
 ('telegram_local_max_file_bytes','2147483648'),
 ('telegram_backup_last_worker_at','')
ON CONFLICT(key) DO NOTHING;
