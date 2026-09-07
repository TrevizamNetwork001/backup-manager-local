-- Campos reconciliados da variante histórica 025_telegram_backup_delivery.
-- backup_manager.db aplica esta migration de forma tolerante quando as colunas
-- já existem em bancos que receberam aquela variante.
ALTER TABLE telegram_backup_policies ADD COLUMN all_artifacts INTEGER NOT NULL DEFAULT 1 CHECK(all_artifacts IN (0,1));
ALTER TABLE telegram_backup_policies ADD COLUMN file_types TEXT NOT NULL DEFAULT '';
ALTER TABLE telegram_backup_items ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT '';
ALTER TABLE telegram_backup_items ADD COLUMN last_attempt_at TEXT;

DROP INDEX IF EXISTS idx_telegram_backup_unique;
UPDATE telegram_backup_items
SET idempotency_key = printf('%d:%d:%d:%s', backup_id, destination_id, COALESCE(thread_id,0), local_sha256)
WHERE idempotency_key = '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_backup_idempotency ON telegram_backup_items(idempotency_key);

INSERT INTO settings(key,value) VALUES
 ('telegram_backup_max_file_bytes','52428800'),
 ('telegram_backup_all_artifacts','1'),
 ('telegram_backup_default_destination',''),
 ('telegram_backup_max_attempts','5'),
 ('telegram_backup_retry_base_seconds','60')
ON CONFLICT(key) DO NOTHING;
