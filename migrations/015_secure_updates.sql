CREATE TABLE product_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    channel TEXT NOT NULL,
    build_id TEXT NOT NULL DEFAULT '',
    installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    installed_by_user_id INTEGER REFERENCES users(id),
    package_sha256 TEXT NOT NULL DEFAULT '',
    previous_version TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('installed','rolled_back','failed','pending')),
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE update_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    package_name TEXT NOT NULL,
    package_sha256 TEXT NOT NULL DEFAULT '',
    from_version TEXT NOT NULL,
    to_version TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('uploaded','validating','validated','rejected','ready','applying','success','failed','rolling_back','rolled_back')),
    requested_by_user_id INTEGER REFERENCES users(id),
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    validated_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    backup_path TEXT NOT NULL DEFAULT '',
    staging_path TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    rollback_status TEXT NOT NULL DEFAULT '',
    manifest_json_sanitized TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_update_operations_status ON update_operations(status, created_at DESC);
CREATE INDEX idx_product_versions_installed ON product_versions(installed_at DESC);

INSERT INTO product_versions(version, channel, build_id, status, notes)
SELECT '1.0.0-rc1', 'rc', 'local', 'installed', 'Registro inicial da versão instalada'
WHERE NOT EXISTS (SELECT 1 FROM product_versions);

INSERT INTO settings(key,value) VALUES('product_version','1.0.0-rc1') ON CONFLICT(key) DO NOTHING;
INSERT INTO settings(key,value) VALUES('update_max_package_bytes','536870912') ON CONFLICT(key) DO NOTHING;

