CREATE TABLE ftp_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
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

CREATE INDEX idx_ftp_accounts_equipment ON ftp_accounts(equipment_id);
CREATE UNIQUE INDEX idx_ftp_accounts_one_live_equipment ON ftp_accounts(equipment_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_ftp_accounts_active ON ftp_accounts(is_active, deleted_at);

CREATE TABLE ftp_received_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    ftp_account_id INTEGER NOT NULL REFERENCES ftp_accounts(id) ON DELETE RESTRICT,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    original_filename TEXT NOT NULL,
    incoming_relative_path TEXT NOT NULL,
    detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    stable_at TEXT,
    processed_at TEXT,
    status TEXT NOT NULL DEFAULT 'detected',
    file_size INTEGER NOT NULL DEFAULT 0,
    observed_size INTEGER,
    observed_mtime_ns INTEGER,
    sha256 TEXT,
    backup_id INTEGER REFERENCES backups(id),
    error_code TEXT,
    error_message TEXT,
    source_ip TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status IN ('detected', 'waiting_stable', 'processing', 'imported', 'rejected', 'failed', 'duplicate'))
);

CREATE INDEX idx_ftp_received_account ON ftp_received_files(ftp_account_id);
CREATE INDEX idx_ftp_received_equipment ON ftp_received_files(equipment_id);
CREATE INDEX idx_ftp_received_status ON ftp_received_files(status);
CREATE INDEX idx_ftp_received_detected ON ftp_received_files(detected_at);
CREATE INDEX idx_ftp_received_sha256 ON ftp_received_files(sha256);
CREATE INDEX idx_ftp_received_backup ON ftp_received_files(backup_id);

INSERT INTO settings(key, value) VALUES
    ('ftp_enabled', '0'),
    ('ftp_control_port', '21'),
    ('ftp_passive_start', '30000'),
    ('ftp_passive_end', '30100'),
    ('ftp_public_ip', ''),
    ('ftp_public_ipv6', ''),
    ('ftp_stable_seconds', '20'),
    ('ftp_scan_interval_seconds', '30'),
    ('ftp_max_upload_size', '52428800'),
    ('ftp_tls_enabled', '0')
ON CONFLICT(key) DO NOTHING;
