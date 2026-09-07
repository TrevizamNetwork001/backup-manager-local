CREATE TABLE ftp_accounts_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    username TEXT NOT NULL,
    password_hash_or_secret_reference TEXT NOT NULL,
    home_relative_path TEXT NOT NULL UNIQUE,
    permission_mode TEXT NOT NULL DEFAULT 'upload_only',
    allowed_source_ip TEXT,
    allowed_source_cidr TEXT,
    control_port INTEGER NOT NULL DEFAULT 21,
    is_active INTEGER NOT NULL DEFAULT 1,
    quota_bytes INTEGER,
    max_files INTEGER,
    last_login_at TEXT,
    last_upload_at TEXT,
    last_upload_filename TEXT,
    last_upload_status TEXT,
    sync_status TEXT NOT NULL DEFAULT 'pending',
    sync_error TEXT NOT NULL DEFAULT '',
    expected_backup_enabled INTEGER NOT NULL DEFAULT 0,
    expected_frequency TEXT,
    expected_time TEXT,
    expected_grace_minutes INTEGER,
    created_by_user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    CHECK (permission_mode IN ('upload_only', 'upload_and_list', 'read_write')),
    CHECK (control_port BETWEEN 1 AND 65535),
    CHECK (quota_bytes IS NULL OR quota_bytes > 0),
    CHECK (max_files IS NULL OR max_files > 0)
);

INSERT INTO ftp_accounts_new SELECT * FROM ftp_accounts;
DROP TABLE ftp_accounts;
ALTER TABLE ftp_accounts_new RENAME TO ftp_accounts;
CREATE INDEX idx_ftp_accounts_equipment ON ftp_accounts(equipment_id);
CREATE UNIQUE INDEX idx_ftp_accounts_active_username ON ftp_accounts(username) WHERE is_active=1 AND deleted_at IS NULL;
CREATE UNIQUE INDEX idx_ftp_accounts_one_active_equipment ON ftp_accounts(equipment_id) WHERE is_active=1 AND deleted_at IS NULL;
CREATE INDEX idx_ftp_accounts_active ON ftp_accounts(is_active, deleted_at);

CREATE TABLE mikrotik_ftp_integrations_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    ftp_account_id INTEGER NOT NULL REFERENCES ftp_accounts(id) ON DELETE RESTRICT,
    routeros_major_version INTEGER NOT NULL,
    backup_format TEXT NOT NULL,
    schedule_mode TEXT NOT NULL DEFAULT 'manual',
    schedule_frequency TEXT NOT NULL DEFAULT 'daily',
    schedule_time TEXT NOT NULL DEFAULT '02:00',
    ftp_host TEXT NOT NULL,
    ftp_port INTEGER NOT NULL DEFAULT 21,
    ftp_directory TEXT NOT NULL DEFAULT '/',
    encrypted_ftp_secret TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    retention_days INTEGER,
    last_test_status TEXT,
    last_test_at TEXT,
    last_success_at TEXT,
    last_upload_at TEXT,
    credential_created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    credential_rotated_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (routeros_major_version IN (6, 7)),
    CHECK (backup_format IN ('backup', 'rsc', 'both')),
    CHECK (schedule_mode IN ('manual', 'scheduled')),
    CHECK (schedule_frequency IN ('daily')),
    CHECK (schedule_time GLOB '[0-2][0-9]:[0-5][0-9]'),
    CHECK (ftp_port BETWEEN 1 AND 65535),
    CHECK (retention_days IS NULL OR retention_days > 0)
);

INSERT INTO mikrotik_ftp_integrations_new SELECT * FROM mikrotik_ftp_integrations;
DROP TABLE mikrotik_ftp_integrations;
ALTER TABLE mikrotik_ftp_integrations_new RENAME TO mikrotik_ftp_integrations;
CREATE UNIQUE INDEX idx_mikrotik_ftp_active_equipment ON mikrotik_ftp_integrations(equipment_id) WHERE is_active=1;
CREATE UNIQUE INDEX idx_mikrotik_ftp_active_account ON mikrotik_ftp_integrations(ftp_account_id) WHERE is_active=1;
CREATE INDEX idx_mikrotik_ftp_active ON mikrotik_ftp_integrations(is_active);
