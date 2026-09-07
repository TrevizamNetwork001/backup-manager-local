CREATE TABLE backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL,
    relative_path TEXT,
    file_size INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL,
    source_method TEXT NOT NULL DEFAULT 'manual',
    backup_reason TEXT NOT NULL DEFAULT 'manual',
    backup_status TEXT NOT NULL DEFAULT 'receiving',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id INTEGER REFERENCES users(id),
    download_count INTEGER NOT NULL DEFAULT 0,
    last_downloaded_at TEXT,
    deleted_at TEXT,
    deleted_by_user_id INTEGER REFERENCES users(id),
    trash_relative_path TEXT,
    trash_expires_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    CHECK (source_method IN ('manual', 'ssh', 'ftp', 'sftp', 'tftp', 'api', 'system')),
    CHECK (backup_reason IN ('manual', 'scheduled', 'daily', 'save', 'commit', 'event', 'unknown')),
    CHECK (backup_status IN ('receiving', 'validating', 'available', 'quarantined', 'failed', 'trashed', 'deleted'))
);

CREATE INDEX idx_backups_equipment_id ON backups(equipment_id);
CREATE INDEX idx_backups_created_at ON backups(created_at);
CREATE INDEX idx_backups_backup_status ON backups(backup_status);
CREATE INDEX idx_backups_deleted_at ON backups(deleted_at);
CREATE INDEX idx_backups_sha256 ON backups(sha256);
CREATE INDEX idx_backups_uuid ON backups(uuid);

INSERT INTO settings(key, value) VALUES
    ('storage_root', '/var/lib/backup-manager-local'),
    ('backup_directory', '/var/lib/backup-manager-local/backups'),
    ('trash_directory', '/var/lib/backup-manager-local/trash'),
    ('quarantine_directory', '/var/lib/backup-manager-local/quarantine'),
    ('temporary_directory', '/var/lib/backup-manager-local/temporary'),
    ('default_retention_days', '365'),
    ('trash_retention_days', '30'),
    ('maximum_upload_size', '52428800')
ON CONFLICT(key) DO NOTHING;
