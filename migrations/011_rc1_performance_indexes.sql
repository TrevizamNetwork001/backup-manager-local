CREATE INDEX IF NOT EXISTS idx_backups_equipment_status_received
ON backups(equipment_id, backup_status, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_job_runs_status_created
ON backup_job_runs(status, created_at);

CREATE INDEX IF NOT EXISTS idx_ftp_received_status_updated
ON ftp_received_files(status, updated_at);

CREATE INDEX IF NOT EXISTS idx_audit_action_id
ON audit_log(action, id DESC);

CREATE INDEX IF NOT EXISTS idx_notification_queue_dedup
ON notification_queue(dedup_key);

CREATE INDEX IF NOT EXISTS idx_lifecycle_mode_id
ON lifecycle_runs(mode, id DESC);
