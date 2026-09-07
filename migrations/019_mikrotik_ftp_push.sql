CREATE TABLE mikrotik_ftp_integrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    equipment_id INTEGER NOT NULL UNIQUE REFERENCES equipment(id) ON DELETE RESTRICT,
    ftp_account_id INTEGER NOT NULL UNIQUE REFERENCES ftp_accounts(id) ON DELETE RESTRICT,
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

CREATE INDEX idx_mikrotik_ftp_active ON mikrotik_ftp_integrations(is_active);

CREATE TABLE mikrotik_ftp_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    integration_id INTEGER NOT NULL REFERENCES mikrotik_ftp_integrations(id) ON DELETE RESTRICT,
    token_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    expires_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    received_at TEXT,
    validated_at TEXT,
    completed_at TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status IN ('pending','waiting_upload','receiving','validating','success','failed','timeout','incomplete','invalid_file'))
);

CREATE INDEX idx_mikrotik_ftp_tests_integration ON mikrotik_ftp_tests(integration_id, created_at);
CREATE INDEX idx_mikrotik_ftp_tests_status ON mikrotik_ftp_tests(status, expires_at);

CREATE TABLE mikrotik_ftp_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    integration_id INTEGER NOT NULL REFERENCES mikrotik_ftp_integrations(id) ON DELETE RESTRICT,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    test_id INTEGER REFERENCES mikrotik_ftp_tests(id) ON DELETE RESTRICT,
    received_file_id INTEGER UNIQUE REFERENCES ftp_received_files(id) ON DELETE RESTRICT,
    backup_id INTEGER REFERENCES backups(id) ON DELETE RESTRICT,
    original_filename TEXT NOT NULL,
    stored_filename TEXT,
    file_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT,
    status TEXT NOT NULL DEFAULT 'receiving',
    temporary_path TEXT,
    final_path TEXT,
    is_test INTEGER NOT NULL DEFAULT 0,
    routeros_major_version INTEGER NOT NULL,
    backup_format TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    validated_at TEXT,
    completed_at TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (file_type IN ('backup','rsc')),
    CHECK (status IN ('receiving','validating','success','failed','timeout','incomplete','invalid_file','duplicate')),
    CHECK (routeros_major_version IN (6, 7)),
    CHECK (backup_format IN ('backup', 'rsc', 'both'))
);

CREATE UNIQUE INDEX idx_mikrotik_ftp_upload_sha ON mikrotik_ftp_uploads(integration_id, sha256) WHERE sha256 IS NOT NULL;
CREATE INDEX idx_mikrotik_ftp_upload_status ON mikrotik_ftp_uploads(status, received_at);
