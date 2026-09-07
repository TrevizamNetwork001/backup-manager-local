ALTER TABLE equipment ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;

CREATE TABLE backup_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    job_type TEXT NOT NULL DEFAULT 'manual',
    method TEXT NOT NULL DEFAULT 'dry_run',
    schedule_enabled INTEGER NOT NULL DEFAULT 0,
    schedule_type TEXT NOT NULL DEFAULT 'none',
    schedule_time TEXT NOT NULL DEFAULT '',
    schedule_days TEXT NOT NULL DEFAULT '',
    timezone TEXT NOT NULL DEFAULT 'UTC',
    status TEXT NOT NULL DEFAULT 'active',
    last_run_at TEXT,
    next_run_at TEXT,
    created_by_user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    CHECK (job_type IN ('manual', 'scheduled', 'test')),
    CHECK (method IN ('manual', 'ssh', 'ftp', 'sftp', 'tftp', 'api', 'dry_run')),
    CHECK (schedule_type IN ('none', 'daily', 'weekly', 'monthly', 'interval')),
    CHECK (status IN ('active', 'paused', 'disabled', 'deleted'))
);

CREATE TABLE backup_job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    job_id INTEGER NOT NULL REFERENCES backup_jobs(id) ON DELETE RESTRICT,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    trigger_type TEXT NOT NULL DEFAULT 'manual',
    method TEXT NOT NULL DEFAULT 'dry_run',
    status TEXT NOT NULL DEFAULT 'queued',
    started_at TEXT,
    finished_at TEXT,
    duration_ms INTEGER,
    attempt INTEGER NOT NULL DEFAULT 1,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    worker_id TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    safe_log TEXT NOT NULL DEFAULT '',
    created_backup_id INTEGER REFERENCES backups(id),
    created_by_user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (trigger_type IN ('manual', 'scheduled', 'system', 'retry')),
    CHECK (status IN ('queued', 'running', 'success', 'failed', 'cancelled', 'timeout', 'skipped'))
);

CREATE TABLE backup_worker_locks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lock_key TEXT NOT NULL UNIQUE,
    locked_by TEXT NOT NULL,
    locked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_backup_jobs_equipment_id ON backup_jobs(equipment_id);
CREATE INDEX idx_backup_jobs_status ON backup_jobs(status);
CREATE INDEX idx_backup_jobs_next_run_at ON backup_jobs(next_run_at);
CREATE INDEX idx_backup_jobs_schedule ON backup_jobs(schedule_enabled, schedule_type);
CREATE INDEX idx_backup_jobs_uuid ON backup_jobs(uuid);

CREATE INDEX idx_backup_job_runs_job_id ON backup_job_runs(job_id);
CREATE INDEX idx_backup_job_runs_equipment_id ON backup_job_runs(equipment_id);
CREATE INDEX idx_backup_job_runs_status ON backup_job_runs(status);
CREATE INDEX idx_backup_job_runs_created_at ON backup_job_runs(created_at);
CREATE INDEX idx_backup_job_runs_started_at ON backup_job_runs(started_at);
CREATE INDEX idx_backup_job_runs_uuid ON backup_job_runs(uuid);

CREATE INDEX idx_backup_worker_locks_expires_at ON backup_worker_locks(expires_at);

INSERT INTO settings(key, value) VALUES
    ('job_run_timeout_seconds', '300'),
    ('job_worker_lock_seconds', '120')
ON CONFLICT(key) DO NOTHING;
