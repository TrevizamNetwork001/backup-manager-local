-- Modelo aditivo e opt-in para operações compostas e seus artefatos.
-- Não altera nem preenche backups, uploads FTP ou integrações existentes.

CREATE TABLE backup_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    provider_key TEXT NOT NULL,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE RESTRICT,
    job_run_id INTEGER REFERENCES backup_job_runs(id) ON DELETE RESTRICT,
    correlation_key TEXT NOT NULL,
    source_method TEXT NOT NULL DEFAULT 'ftp',
    status TEXT NOT NULL DEFAULT 'waiting_upload'
        CHECK(status IN ('waiting_upload','validating','success','failed','expired')),
    deadline_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(length(trim(provider_key)) BETWEEN 1 AND 80),
    CHECK(length(trim(correlation_key)) BETWEEN 1 AND 200),
    CHECK(length(error_code) <= 80),
    CHECK(length(error_message) <= 240),
    UNIQUE(provider_key, equipment_id, correlation_key)
);

CREATE INDEX idx_backup_operations_status_deadline
ON backup_operations(status, deadline_at);
CREATE INDEX idx_backup_operations_equipment_created
ON backup_operations(equipment_id, created_at);

CREATE TABLE backup_operation_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    operation_id INTEGER NOT NULL REFERENCES backup_operations(id) ON DELETE CASCADE,
    backup_id INTEGER REFERENCES backups(id) ON DELETE RESTRICT,
    artifact_type TEXT NOT NULL,
    is_required INTEGER NOT NULL DEFAULT 1 CHECK(is_required IN (0,1)),
    state TEXT NOT NULL DEFAULT 'expected'
        CHECK(state IN ('expected','waiting','received','validating','valid','invalid',
                        'suspicious','duplicate','expired','late')),
    filename TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER CHECK(size_bytes IS NULL OR size_bytes >= 0),
    sha256 TEXT,
    validation_code TEXT NOT NULL DEFAULT '',
    validation_message TEXT NOT NULL DEFAULT '',
    validation_metadata_json TEXT NOT NULL DEFAULT '{}',
    deadline_at TEXT,
    received_at TEXT,
    validated_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(length(trim(artifact_type)) BETWEEN 1 AND 80),
    CHECK(length(filename) <= 255),
    CHECK(sha256 IS NULL OR (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*')),
    CHECK(length(validation_code) <= 80),
    CHECK(length(validation_message) <= 240),
    UNIQUE(operation_id, artifact_type)
);

CREATE INDEX idx_backup_operation_artifacts_state
ON backup_operation_artifacts(state, deadline_at);
CREATE INDEX idx_backup_operation_artifacts_backup
ON backup_operation_artifacts(backup_id);

INSERT INTO settings(key, value)
VALUES('backup_artifacts_v2_enabled', '0')
ON CONFLICT(key) DO NOTHING;
