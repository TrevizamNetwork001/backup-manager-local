# Correção do upgrade Telegram v1.1.0-rc1

## Causa

O código da fila passou a gravar `destination_id` e `thread_id`, mas algumas
instalações RC já tinham `017_telegram_advanced` registrada sem essas colunas.
Reexecutar ou editar a 017 não é seguro, portanto a correção está na migration
incremental 020.

## Procedimento

1. Execute `bash scripts/deploy-update.sh`; um backup consistente do SQLite é
   criado antes da migration.
2. Confirme `python3 -m backup_manager.cli schema-check` (`SCHEMA_OK`).
3. Confirme `python3 -m backup_manager.cli integrity-check` (`ok`).
4. Envie uma mensagem de teste pela tela de notificações e verifique o histórico.

O procedimento não altera token, Chat ID ou histórico. Em falha do worker,
consulte o journal: `SCHEMA_OUTDATED` indica migration pendente; erros de chat,
tópico ou permissão ocorrem somente depois que a fila local está compatível.
