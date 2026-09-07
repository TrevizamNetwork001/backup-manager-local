CREATE TABLE cloud_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'simulate' CHECK(provider IN ('google_drive','generic','simulate')),
    mode TEXT NOT NULL DEFAULT 'simulate' CHECK(mode = 'simulate'),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    destination_label TEXT NOT NULL DEFAULT '',
    remote_folder_id TEXT,
    bandwidth_limit_kbps INTEGER CHECK(bandwidth_limit_kbps IS NULL OR bandwidth_limit_kbps > 0),
    sync_window_start TEXT,
    sync_window_end TEXT,
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 20),
    retry_base_seconds INTEGER NOT NULL DEFAULT 60 CHECK(retry_base_seconds BETWEEN 1 AND 86400),
    created_by_user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    CHECK((sync_window_start IS NULL) = (sync_window_end IS NULL))
);

CREATE TABLE cloud_sync_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    target_id INTEGER NOT NULL REFERENCES cloud_targets(id) ON DELETE RESTRICT,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('global','equipment','environment','group','vendor','pop')),
    equipment_id INTEGER REFERENCES equipment(id) ON DELETE RESTRICT,
    environment_id INTEGER REFERENCES environments(id) ON DELETE RESTRICT,
    group_id INTEGER REFERENCES equipment_groups(id) ON DELETE RESTRICT,
    vendor_id INTEGER REFERENCES vendors(id) ON DELETE RESTRICT,
    pop_id INTEGER REFERENCES pops(id) ON DELETE RESTRICT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    sync_new_backups INTEGER NOT NULL DEFAULT 0 CHECK(sync_new_backups IN (0,1)),
    include_ssh INTEGER NOT NULL DEFAULT 1 CHECK(include_ssh IN (0,1)),
    include_ftp INTEGER NOT NULL DEFAULT 1 CHECK(include_ftp IN (0,1)),
    minimum_file_size INTEGER CHECK(minimum_file_size IS NULL OR minimum_file_size >= 0),
    maximum_file_size INTEGER CHECK(maximum_file_size IS NULL OR maximum_file_size >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    CHECK(scope_type != 'equipment' OR equipment_id IS NOT NULL),
    CHECK(scope_type != 'global' OR equipment_id IS NULL),
    CHECK(maximum_file_size IS NULL OR minimum_file_size IS NULL OR maximum_file_size >= minimum_file_size)
);

CREATE TABLE cloud_sync_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    target_id INTEGER NOT NULL REFERENCES cloud_targets(id) ON DELETE RESTRICT,
    policy_id INTEGER NOT NULL REFERENCES cloud_sync_policies(id) ON DELETE RESTRICT,
    backup_id INTEGER NOT NULL REFERENCES backups(id) ON DELETE RESTRICT,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    provider TEXT NOT NULL CHECK(provider IN ('google_drive','generic','simulate')),
    mode TEXT NOT NULL CHECK(mode = 'simulate'),
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','validating','uploading','synced','failed','retry_wait','skipped','cancelled')),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
    max_attempts INTEGER NOT NULL CHECK(max_attempts >= 1),
    queued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT,
    next_attempt_at TEXT,
    duration_ms INTEGER,
    local_sha256 TEXT,
    remote_object_id TEXT,
    remote_object_name TEXT,
    remote_folder_id TEXT,
    bytes_total INTEGER NOT NULL DEFAULT 0,
    bytes_uploaded INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    safe_log TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cloud_targets_active ON cloud_targets(is_active);
CREATE INDEX idx_cloud_items_target ON cloud_sync_items(target_id);
CREATE INDEX idx_cloud_items_backup ON cloud_sync_items(backup_id);
CREATE INDEX idx_cloud_items_status ON cloud_sync_items(status);
CREATE INDEX idx_cloud_items_next_attempt ON cloud_sync_items(next_attempt_at);
CREATE INDEX idx_cloud_items_queued ON cloud_sync_items(queued_at);
CREATE INDEX idx_cloud_items_provider ON cloud_sync_items(provider);
CREATE INDEX idx_cloud_items_equipment ON cloud_sync_items(equipment_id);
CREATE UNIQUE INDEX idx_cloud_items_active_unique ON cloud_sync_items(target_id, backup_id) WHERE status != 'cancelled';
CREATE UNIQUE INDEX idx_cloud_policy_global ON cloud_sync_policies(target_id) WHERE scope_type='global' AND deleted_at IS NULL;
CREATE UNIQUE INDEX idx_cloud_policy_equipment ON cloud_sync_policies(target_id,equipment_id) WHERE scope_type='equipment' AND deleted_at IS NULL;

INSERT INTO settings(key,value) VALUES
 ('cloud_sync_enabled','1'),
 ('cloud_sync_scan_interval_seconds','60'),
 ('cloud_sync_default_max_attempts','3'),
 ('cloud_sync_default_retry_base_seconds','60'),
 ('cloud_sync_stuck_minutes','15'),
 ('cloud_sync_auto_enqueue','0'),
 ('cloud_sync_last_worker_at','')
ON CONFLICT(key) DO NOTHING;
