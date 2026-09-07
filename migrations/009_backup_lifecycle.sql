CREATE TABLE retention_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id INTEGER REFERENCES equipment(id) ON DELETE CASCADE,
    max_count INTEGER,
    max_age_days INTEGER,
    trash_retention_days INTEGER,
    rejected_retention_days INTEGER,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (max_count IS NULL OR max_count >= 0),
    CHECK (max_age_days IS NULL OR max_age_days >= 0),
    CHECK (trash_retention_days IS NULL OR trash_retention_days >= 1),
    CHECK (rejected_retention_days IS NULL OR rejected_retention_days >= 1)
);

CREATE UNIQUE INDEX idx_retention_policy_global ON retention_policies((equipment_id IS NULL)) WHERE equipment_id IS NULL;
CREATE UNIQUE INDEX idx_retention_policy_equipment ON retention_policies(equipment_id) WHERE equipment_id IS NOT NULL;

INSERT INTO retention_policies(equipment_id, max_count, max_age_days, trash_retention_days, rejected_retention_days)
VALUES(NULL, 0, 365, 30, 30);

CREATE TABLE lifecycle_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by_user_id INTEGER REFERENCES users(id),
    candidate_count INTEGER NOT NULL DEFAULT 0,
    candidate_bytes INTEGER NOT NULL DEFAULT 0,
    moved_count INTEGER NOT NULL DEFAULT 0,
    moved_bytes INTEGER NOT NULL DEFAULT 0,
    purged_count INTEGER NOT NULL DEFAULT 0,
    purged_bytes INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    report_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    CHECK (mode IN ('simulation', 'execution', 'automatic', 'health_check')),
    CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE INDEX idx_lifecycle_runs_created ON lifecycle_runs(created_at);

CREATE TABLE lifecycle_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES lifecycle_runs(id) ON DELETE CASCADE,
    backup_id INTEGER REFERENCES backups(id) ON DELETE RESTRICT,
    ftp_received_file_id INTEGER REFERENCES ftp_received_files(id) ON DELETE RESTRICT,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT,
    source_relative_path TEXT,
    trash_relative_path TEXT,
    result TEXT NOT NULL DEFAULT 'planned',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (action IN ('trash_backup', 'trash_rejected', 'purge_backup', 'purge_rejected', 'health_check')),
    CHECK (result IN ('planned', 'completed', 'failed', 'missing')),
    CHECK ((backup_id IS NOT NULL) != (ftp_received_file_id IS NOT NULL) OR action = 'health_check')
);

ALTER TABLE ftp_received_files ADD COLUMN lifecycle_trash_relative_path TEXT;
ALTER TABLE ftp_received_files ADD COLUMN lifecycle_trashed_at TEXT;
ALTER TABLE ftp_received_files ADD COLUMN lifecycle_expires_at TEXT;

INSERT INTO settings(key, value) VALUES
    ('lifecycle_enabled', '1'),
    ('lifecycle_last_run_at', '')
ON CONFLICT(key) DO NOTHING;
