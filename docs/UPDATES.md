# Atualizações locais e remotas seguras

A FASE 11.1 introduz atualização exclusivamente por pacote local `.bmu` assinado. Ela não consulta rede, não usa Git, não executa `install.sh` e não possui timer automático.

A FASE 11.2 preserva esse motor e acrescenta índice remoto assinado, check periódico sem mutações e download administrativo em fila. Baixar nunca aplica. Consulte [UPDATE_REPOSITORY.md](UPDATE_REPOSITORY.md) e [UPDATE_PUBLICATION.md](UPDATE_PUBLICATION.md).

## Operação

Instale a chave pública Ed25519 em `/etc/backup-manager-local/update-public-key.pem`, pertencente a root e sem permissão de escrita por grupo/outros. O pacote entra em `/var/lib/backup-manager-local/updates/incoming`; validação não altera o sistema. Um plano persistido registra origem/destino, arquivos, migrations, serviços, espaço, checks e notas.

`backup-manager-update.service` executa o helper root-only fora da requisição web. Ele revalida assinatura e hashes, extrai em staging confinado, cria backup em `/var/backups/backup-manager-local/updates/<uuid>`, aplica arquivos atomicamente, roda migrations e checks, e reinicia somente units allowlisted.

CLI: `version`, `update-history`, `update-validate --package`, `update-plan --operation`, `update-apply --operation`, `update-status --operation` e `update-rollback --operation --confirm RESTAURAR_VERSAO_ANTERIOR`.

## Backup, falha e recuperação

O backup usa a API SQLite e copia apenas arquivos gerenciados afetados. Storage, FTP, PureDB, certificados, Nginx e UFW não são copiados nem modificados. Falha restaura aplicação e banco pré-update; relatórios permanecem. Recuperação manual deve validar o manifesto do backup antes de restaurar e nunca apagar evidências.

## Limitações da fase

Não há busca, download ou atualização automática; não há downgrade de schema; pacotes não modificam Nginx/certificados/UFW/PureDB. A validação final de units e `systemd-analyze verify` deve ocorrer numa VM de homologação, nunca na produção durante desenvolvimento.
# v1.0.0 → v1.1.0-rc1

A migration incremental `020_fix_notification_queue_destinations` corrige bancos
RC nos quais a migration 017 foi registrada sem as colunas de roteamento da fila.
Ela é condicional, transacional, preserva linhas e índices existentes e aceita
estados parcialmente migrados. O deploy aborta se `schema-check` não retornar
`SCHEMA_OK`.
