# Recuperação e reconciliação — 23/08/2026

## Storage

Antes da mudança foi criado e validado um backup SQLite em
`data/recovery-backups`. O inventário encontrou 8 registros sem arquivo e 50
arquivos sem registro. Nenhum conteúdo foi apagado.

- os 8 registros irrecuperáveis foram preservados como histórico `failed`;
- arquivos de equipamento identificáveis foram cadastrados como `quarantined`;
- itens técnicos de `rejected/` continuam sob responsabilidade do lifecycle;
- o verificador passou a exigir arquivo apenas de estados que representam
  conteúdo armazenado;
- resultado final: 0 arquivos ausentes e 0 órfãos, com hashes aprovados.

O utilitário `scripts/reconcile-storage.py` faz dry-run por padrão. `--apply`
preserva arquivos e registra a operação na auditoria.

## Recuperação automática

`backup-manager-database-guard.timer` roda a cada hora. O guardião:

1. executa `integrity_check` e `foreign_key_check`;
2. cria backup consistente pela API SQLite e mantém 14 versões automáticas;
3. ao detectar corrupção, escolhe a cópia íntegra mais recente;
4. para web e timers de escrita;
5. preserva o banco corrompido com sufixo `corrupt-<data>`;
6. restaura e valida a cópia antes de religar os serviços.

O teste automatizado corrompe um banco temporário e comprova restauração e
preservação da evidência. A chave `/etc/backup-manager-local/secret.key` nunca é
recriada por esse mecanismo.

## Integrações

- SSH: conexão aprovada nos equipamentos 1 e 2;
- FTP: configuração aprovada e duas contas sincronizadas;
- Telegram: destino ativo, teste enfileirado e envios recentes aprovados;
- 369 falhas são históricas, encerradas em 15/07/2026;
- rclone/Google Drive: remote `meudrive` reconectado e validado em 05/09/2026;
  backup real confirmado em `PEDRAS-TELECOM/CE-VPN/05-09-2026`.

## Gate da versão estável

A RC9 continua bloqueada apenas pelo gate de assinatura oficial e promoção da
versão estável. O destino rclone real já foi configurado e testado.
