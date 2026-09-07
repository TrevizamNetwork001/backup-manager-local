CREATE TABLE rclone_connections (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 target_id INTEGER NOT NULL UNIQUE REFERENCES cloud_targets(id) ON DELETE RESTRICT,
 remote_name TEXT NOT NULL,
 base_path TEXT NOT NULL DEFAULT 'BackupManager',
 status TEXT NOT NULL DEFAULT 'configured' CHECK(status IN('configured','verified','failed','disabled')),
 last_test_at TEXT,
 last_error_code TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_rclone_connections_status ON rclone_connections(status);
UPDATE cloud_targets SET is_active=0, notes=trim(notes || ' Google Drive legado desativado pela migração rclone.'), updated_at=CURRENT_TIMESTAMP
 WHERE provider='google_drive' AND deleted_at IS NULL;
INSERT INTO settings(key,value) VALUES('rclone_config_path','/etc/backup-manager-local/rclone.conf') ON CONFLICT(key) DO NOTHING;
