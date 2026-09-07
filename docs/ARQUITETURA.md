# Arquitetura

## Visao geral

O Backup Manager Local mantém arquitetura local e modular:

```text
Navegador
  -> Nginx HTTPS :443
  -> Aplicacao Python 127.0.0.1:8080
  -> SQLite /opt/backup-manager-local/data/backup_manager.sqlite3
```

## Aplicação

A aplicação usa a biblioteca padrão do Python e dependências Debian para SSH e
criptografia:

- `wsgiref` para servidor HTTP interno;
- `sqlite3` para banco local;
- `hashlib.pbkdf2_hmac` para hash de senha;
- cookies HTTP-only para sessao.
- `paramiko` para SSH Pull;
- `cryptography` para segredos SSH e Telegram.

Não há dependências instaladas diretamente por `pip` nem serviços SaaS
obrigatórios. Telegram é opcional.

## Estrutura

```text
backup_manager/
  app.py       dispatcher e apresentação web ainda em decomposição
  db.py        conexao SQLite, migrations e seeds
  security.py  hash de senha e tokens
  cli.py       parser e apresentação dos comandos administrativos
  ftp_provisioning.py ciclo de vida central de contas e integrações FTP
  ftp_diagnostics.py diagnósticos FTP usados pelo CLI e interfaces
  ftp_pipeline.py etapas puras de descoberta, estabilização e validação
  ftp_correlation.py correlação de arquivos recebidos
  ftp_storage.py persistência de backups recebidos
  ftp_events.py eventos após armazenamento
  equipment_lifecycle.py arquivamento seguro de equipamentos
  jobs.py      scheduler e worker
  lifecycle.py retenção, lixeira e integridade
  observability.py agregação NOC somente leitura
  notifications.py fila, cooldown e Telegram
migrations/
  001...030.sql
static/
  app.css      estilos com versionamento automático
  app.js       comportamento visual compartilhado
templates/
  base.html    estrutura comum
  login.html   apresentação do login
  dashboard.html apresentação do centro de operações
  components/  sidebar e topbar
deploy/
  nginx/
  systemd/
scripts/
  install.sh
docs/
```

## Modelo de dados principal

- `settings`
- `roles`
- `users`
- `sessions`
- `equipment_groups`
- `vendors`
- `pops`
- `environments`
- `equipment`
- `audit_log`
- `schema_migrations`
- `backups`, `backup_jobs`, `backup_job_runs`
- `ftp_accounts`, `ftp_received_files`
- `retention_policies`, `lifecycle_runs`, `lifecycle_items`
- `notification_channels`, `notification_queue`, `notification_history`

## Processos

- `backup-manager-local.service`: aplicação web;
- `backup-manager-worker.timer`: jobs e entrega de notificações;
- `backup-manager-ftp-importer.timer`: importação FTP;
- `backup-manager-lifecycle.timer`: retenção e expurgo;
- `backup-manager-observability.timer`: snapshot seguro de serviços.
