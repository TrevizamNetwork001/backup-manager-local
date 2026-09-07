ALTER TABLE notification_history ADD COLUMN error_description TEXT NOT NULL DEFAULT '';
ALTER TABLE notification_history ADD COLUMN retry_after INTEGER;
ALTER TABLE notification_history ADD COLUMN migrate_to_chat_id TEXT;
