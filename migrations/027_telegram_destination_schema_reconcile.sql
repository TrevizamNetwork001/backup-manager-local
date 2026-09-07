-- Reconcilia a variante historica da migration 025 sem reescreve-la.
-- Os triggers antigos conflitam com a troca segura do destino legado, que cria
-- o substituto antes de desativar o registro anterior. A gestao de destinos
-- aplica a mesma regra por validacao de aplicacao. Antes de remover os triggers,
-- esta migration recusa um estado que ja contenha nomes ativos duplicados.
-- Destinos inativos ou excluidos com o mesmo nome permanecem permitidos.

CREATE TEMP TABLE telegram_destination_schema_reconcile_guard (
 active_name_duplicate_count INTEGER NOT NULL,
 CONSTRAINT telegram_active_destination_names_must_be_unique
 CHECK(active_name_duplicate_count = 0)
);

INSERT INTO telegram_destination_schema_reconcile_guard(active_name_duplicate_count)
SELECT COUNT(*)
FROM (
 SELECT lower(trim(name))
 FROM telegram_destinations
 WHERE is_active = 1 AND deleted_at IS NULL
 GROUP BY lower(trim(name))
 HAVING COUNT(*) > 1
);

DROP TABLE telegram_destination_schema_reconcile_guard;

DROP TRIGGER IF EXISTS telegram_destination_unique_active_name_insert;
DROP TRIGGER IF EXISTS telegram_destination_unique_active_name_update;
