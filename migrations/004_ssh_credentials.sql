ALTER TABLE equipment ADD COLUMN ssh_backup_driver TEXT NOT NULL DEFAULT '';
ALTER TABLE equipment ADD COLUMN ssh_custom_command TEXT NOT NULL DEFAULT '';

CREATE TABLE device_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    credential_type TEXT NOT NULL DEFAULT 'ssh',
    name TEXT NOT NULL,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL DEFAULT '',
    private_key_encrypted TEXT NOT NULL DEFAULT '',
    private_key_passphrase_encrypted TEXT NOT NULL DEFAULT '',
    port INTEGER NOT NULL DEFAULT 22,
    auth_method TEXT NOT NULL DEFAULT 'password',
    is_active INTEGER NOT NULL DEFAULT 1,
    last_test_status TEXT NOT NULL DEFAULT '',
    last_test_at TEXT,
    last_test_error TEXT NOT NULL DEFAULT '',
    created_by_user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    CHECK (credential_type IN ('ssh')),
    CHECK (auth_method IN ('password', 'private_key', 'password_or_key'))
);

CREATE INDEX idx_device_credentials_equipment_id ON device_credentials(equipment_id);
CREATE INDEX idx_device_credentials_active ON device_credentials(equipment_id, credential_type, is_active, deleted_at);

INSERT INTO settings(key, value) VALUES
    ('ssh_connect_timeout_seconds', '15'),
    ('ssh_command_timeout_seconds', '60')
ON CONFLICT(key) DO NOTHING;
