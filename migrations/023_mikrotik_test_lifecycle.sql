CREATE TABLE mikrotik_ftp_tests_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    integration_id INTEGER NOT NULL REFERENCES mikrotik_ftp_integrations(id) ON DELETE RESTRICT,
    token_hash TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'waiting_upload',
    expires_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expected_filename TEXT NOT NULL DEFAULT '',
    last_check_at TEXT,
    received_at TEXT,
    validated_at TEXT,
    completed_at TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status IN ('pending','running','waiting_upload','validating','validated','failed','expired','cancelled'))
);
INSERT INTO mikrotik_ftp_tests_new
    (id,uuid,integration_id,token_hash,status,expires_at,started_at,expected_filename,last_check_at,
     received_at,validated_at,completed_at,error_code,error_message,created_at,updated_at)
SELECT id,uuid,integration_id,token_hash,
       CASE status WHEN 'success' THEN 'validated' WHEN 'timeout' THEN 'expired'
         WHEN 'receiving' THEN 'validating' WHEN 'incomplete' THEN 'failed'
         WHEN 'invalid_file' THEN 'failed' ELSE status END,
       expires_at,started_at,'',NULL,received_at,validated_at,completed_at,error_code,error_message,created_at,updated_at
FROM mikrotik_ftp_tests;
DROP TABLE mikrotik_ftp_tests;
ALTER TABLE mikrotik_ftp_tests_new RENAME TO mikrotik_ftp_tests;
CREATE INDEX idx_mikrotik_ftp_tests_integration ON mikrotik_ftp_tests(integration_id, created_at);
CREATE INDEX idx_mikrotik_ftp_tests_status ON mikrotik_ftp_tests(status, expires_at);
DROP INDEX idx_mikrotik_ftp_upload_sha;
CREATE UNIQUE INDEX idx_mikrotik_ftp_upload_sha ON mikrotik_ftp_uploads(integration_id, sha256)
WHERE sha256 IS NOT NULL AND is_test=0;
