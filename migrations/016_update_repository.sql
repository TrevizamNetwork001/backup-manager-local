CREATE TABLE update_downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    version TEXT NOT NULL,
    channel TEXT NOT NULL CHECK(channel IN ('stable','rc')),
    build_id TEXT NOT NULL,
    package_url_sanitized TEXT NOT NULL,
    expected_sha256 TEXT NOT NULL,
    expected_size INTEGER NOT NULL CHECK(expected_size > 0),
    status TEXT NOT NULL CHECK(status IN ('queued','downloading','validating','ready','failed','cancelled')),
    bytes_total INTEGER NOT NULL DEFAULT 0,
    bytes_downloaded INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_by_user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_update_downloads_status ON update_downloads(status, created_at);

INSERT INTO settings(key,value) VALUES
 ('update_repository_enabled','0'),
 ('update_repository_url',''),
 ('update_channel','stable'),
 ('update_check_interval_hours','24'),
 ('update_last_check_at',''),
 ('update_last_check_status','never'),
 ('update_last_check_error',''),
 ('update_available_version',''),
 ('update_available_build_id',''),
 ('update_available_release_json','{}'),
 ('update_repository_allowed_hosts',''),
 ('update_repository_lab_mode','0')
ON CONFLICT(key) DO NOTHING;
