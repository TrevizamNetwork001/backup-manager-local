CREATE TABLE mikrotik_ftp_tests_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    integration_id INTEGER NOT NULL REFERENCES mikrotik_ftp_integrations(id) ON DELETE RESTRICT,
    token_hash TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'waiting_upload',
    expires_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    received_at TEXT,
    validated_at TEXT,
    completed_at TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status IN ('pending','waiting_upload','receiving','validating','success','failed','timeout','incomplete','invalid_file'))
);
INSERT INTO mikrotik_ftp_tests_new SELECT * FROM mikrotik_ftp_tests;
DROP TABLE mikrotik_ftp_tests;
ALTER TABLE mikrotik_ftp_tests_new RENAME TO mikrotik_ftp_tests;
CREATE INDEX idx_mikrotik_ftp_tests_integration ON mikrotik_ftp_tests(integration_id, created_at);
CREATE INDEX idx_mikrotik_ftp_tests_status ON mikrotik_ftp_tests(status, expires_at);
