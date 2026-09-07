CREATE TABLE ftp_accounts_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    equipment_id INTEGER REFERENCES equipment(id) ON DELETE RESTRICT,
    account_type TEXT NOT NULL DEFAULT 'backup',
    name TEXT NOT NULL,
    username TEXT NOT NULL,
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
    CHECK (account_type IN ('backup', 'file_server')),
    CHECK ((account_type='backup' AND equipment_id IS NOT NULL) OR
           (account_type='file_server' AND equipment_id IS NULL)),
    CHECK (permission_mode IN ('upload_only', 'upload_and_list', 'read_write')),
    CHECK (control_port BETWEEN 1 AND 65535),
    CHECK (quota_bytes IS NULL OR quota_bytes > 0),
    CHECK (max_files IS NULL OR max_files > 0)
);

INSERT INTO ftp_accounts_new (
    id,uuid,equipment_id,account_type,name,username,password_hash_or_secret_reference,
    home_relative_path,permission_mode,allowed_source_ip,allowed_source_cidr,control_port,
    is_active,quota_bytes,max_files,last_login_at,last_upload_at,last_upload_filename,
    last_upload_status,sync_status,sync_error,expected_backup_enabled,expected_frequency,
    expected_time,expected_grace_minutes,created_by_user_id,created_at,updated_at,deleted_at,notes
)
SELECT id,uuid,equipment_id,'backup',name,username,password_hash_or_secret_reference,
       home_relative_path,permission_mode,allowed_source_ip,allowed_source_cidr,control_port,
       is_active,quota_bytes,max_files,last_login_at,last_upload_at,last_upload_filename,
       last_upload_status,sync_status,sync_error,expected_backup_enabled,expected_frequency,
       expected_time,expected_grace_minutes,created_by_user_id,created_at,updated_at,deleted_at,notes
FROM ftp_accounts;

DROP TABLE ftp_accounts;
ALTER TABLE ftp_accounts_new RENAME TO ftp_accounts;
CREATE INDEX idx_ftp_accounts_equipment ON ftp_accounts(equipment_id);
CREATE INDEX idx_ftp_accounts_type ON ftp_accounts(account_type,is_active,deleted_at);
CREATE UNIQUE INDEX idx_ftp_accounts_active_username ON ftp_accounts(username)
    WHERE is_active=1 AND deleted_at IS NULL;
CREATE UNIQUE INDEX idx_ftp_accounts_one_active_equipment ON ftp_accounts(equipment_id)
    WHERE equipment_id IS NOT NULL AND is_active=1 AND deleted_at IS NULL;
CREATE INDEX idx_ftp_accounts_active ON ftp_accounts(is_active,deleted_at);
