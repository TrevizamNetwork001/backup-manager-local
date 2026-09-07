ALTER TABLE google_drive_connections ADD COLUMN oauth_session_hash TEXT;
ALTER TABLE google_drive_connections ADD COLUMN oauth_user_id INTEGER REFERENCES users(id);
ALTER TABLE google_drive_connections ADD COLUMN oauth_redirect_uri TEXT;
ALTER TABLE google_drive_connections ADD COLUMN oauth_consumed_at TEXT;
ALTER TABLE cloud_sync_items ADD COLUMN resumable_session_created_at TEXT;

INSERT INTO settings(key,value) VALUES('public_base_url','') ON CONFLICT(key) DO NOTHING;
