# Sincronização externa via rclone

O backup local validado continua sendo a fonte principal. A sincronização externa não substitui, altera, move nem apaga o arquivo local e não interfere no Lifecycle.

## Arquitetura e modo simulate

Uma política global ou por equipamento associa backups a um destino. O enfileiramento grava um item SQLite e retorna imediatamente; o timer executa um worker separado a cada minuto. O worker usa lock exclusivo, revalida destino, política, status `available`, confinamento ao storage, ausência de symlink, tamanho e SHA-256. Só então percorre `queued → validating → uploading → synced`.

Destinos reais são executados pelo rclone; consulte [RCLONE.md](RCLONE.md). Destinos `simulate` permanecem disponíveis somente para testes internos, sem upload externo.

Os caminhos remotos reais seguem `nome-da-instalacao/equipamento/DD-MM-AAAA/arquivo`.
O agendamento define quando o arquivo é criado; a data representa o dia do
recebimento. O grupo continua disponível para filtros e políticas, sem criar
uma pasta intermediária.

## Fila, retry e janela

Os estados são `queued`, `validating`, `uploading`, `synced`, `retry_wait`, `failed`, `skipped` e `cancelled`. O sucesso recebe `simulate:<uuid>`. Um índice parcial impede duplicata não cancelada no mesmo backup/destino.

O retry usa `retry_base_seconds * 2^(attempt - 1)`. Fora da janela, inclusive 22:00–06:00, o item permanece no estado atual sem incrementar a tentativa. `cloud-run-once --simulate-failure` gera a falha controlada `CLOUD_SIMULATED_FAILURE`; a nota interna `simulate_failure` no destino faz o mesmo.

O campo `bandwidth_limit_kbps` é aplicado pelo rclone durante o envio real.

## Elegibilidade e segurança

São recusados backups indisponíveis, excluídos, na lixeira, em quarentena, ausentes, fora do storage, symlinks, tamanho ou hash divergente, método bloqueado, política/destino inativo, limites de tamanho violados e duplicatas. Caminhos absolutos e conteúdo nunca entram no log seguro ou auditoria.

## Operação e futuro

Use `python3 -m backup_manager.cli cloud-targets`, `cloud-policies`, `cloud-queue`, `cloud-stats`, `cloud-enqueue --backup-id ID`, `cloud-run-once`, `cloud-retry --item-id ID` e `cloud-cancel --item-id ID`.

O Google Drive OAuth direto foi substituído pelo rclone. Conexões antigas ficam desativadas como legado e o histórico é preservado. Um destino desativado por erro de credencial deve ser reconectado e testado antes de ser ativado novamente.
# Telegram como destino secundário

A fila Telegram não faz parte da fila rclone e possui estados, retry, worker e métricas próprios. As duas integrações consomem eventos de um backup local já validado. Consulte [TELEGRAM_BACKUP.md](TELEGRAM_BACKUP.md).
