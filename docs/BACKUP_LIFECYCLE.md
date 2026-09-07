# Retenção, limpeza e ciclo de vida

## Modelo de segurança

A limpeza nunca exclui um backup disponível diretamente. O ciclo é:

```text
available -> trashed -> deleted
                 |
                 +-> available (restauração enquanto não expirou)
```

A primeira transição usa movimentação atômica dentro do storage e mantém
UUID, tamanho e SHA-256 no banco. A restauração recalcula o SHA-256 antes de
devolver o arquivo. O expurgo físico só ocorre em execução posterior, quando
`trash_expires_at` tiver vencido. O registro do backup, os relatórios de ciclo
de vida e a auditoria permanecem no SQLite após o expurgo.

## Políticas

A política global define:

- quantidade máxima por equipamento (`0` significa ilimitada);
- idade máxima em dias (`0` significa ilimitada);
- dias de retenção na lixeira;
- dias até arquivos FTP rejeitados serem enviados à lixeira.

Um equipamento pode sobrescrever quantidade e idade; campos vazios herdam a
política global. Um backup vira candidato quando excede a quantidade **ou** a
idade. A ordenação por `received_at DESC, id DESC` preserva deterministicamente
os backups mais recentes.

## Simulação e execução

Na interface administrativa, acesse **Retenção**. A simulação grava um relatório
com arquivo, equipamento, tamanho, hash e motivo, mas não move nem remove nada.
O espaço estimado é a soma dos candidatos.

A execução manual exige digitar exatamente:

```text
MOVER_PARA_LIXEIRA
```

Também é possível operar por CLI:

```bash
python3 -m backup_manager.cli lifecycle-simulate
python3 -m backup_manager.cli lifecycle-cleanup --confirm MOVER_PARA_LIXEIRA
python3 -m backup_manager.cli lifecycle-health --hash
python3 -m backup_manager.cli storage-stats
```

Cada simulação, execução, restauração e health-check gera auditoria. A interface
lista o relatório mais recente e os backups ainda restauráveis na lixeira.

## Limpeza automática

`backup-manager-lifecycle.timer` executa diariamente às 03:30, com atraso
aleatório de até 15 minutos e persistência após indisponibilidade. A unit atua
somente no SQLite e nos diretórios `backups`, `trash` e rejeitados do storage;
não configura nem reinicia FTP, SSH, Nginx, firewall ou certificados.

```bash
systemctl status backup-manager-lifecycle.timer
journalctl -u backup-manager-lifecycle.service --since today
```

Desabilite temporariamente pelo setting `lifecycle_enabled=0`; o runner encerra
sem alterar arquivos.

## Arquivos rejeitados

Rejeitados permanecem no diretório da conta pelo período configurado. Depois
são movidos para `storage/trash/rejected/YYYY/MM/` e recebem uma expiração de
lixeira. Somente após essa segunda retenção ocorre expurgo físico. Os registros
em `ftp_received_files`, `lifecycle_runs`, `lifecycle_items` e `audit_log` são
preservados.

## Estatísticas e integridade

A página mostra contagem e bytes de backups disponíveis, lixeira e espaço livre
do filesystem. O health-check verifica caminho confinado ao storage, presença,
tamanho e, quando solicitado, SHA-256 de backups disponíveis e na lixeira. Ele
é somente leitura sobre os arquivos.

Problemas de integridade nunca causam exclusão automática. Corrija a origem do
problema ou restaure o arquivo a partir de uma cópia confiável antes de executar
novas limpezas.
