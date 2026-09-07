# Deployment

## Versão 1.0

A configuração administrativa fica em **Configurações**. Em migrações, exporte
o JSON sanitizado no servidor de origem e importe-o somente após instalar e
executar o Setup no destino. Credenciais, tokens e certificados devem ser
configurados novamente; nenhum arquivo de backup ou log integra o pacote.

HTTPS permanece opcional e independente do deploy da aplicação. O assistente
usa o helper administrativo, preserva o site Nginx existente e só recarrega uma
configuração aprovada por `nginx -t`. Deploys e atualizações continuam sem
sobrescrever certificados ou configurações de acesso.

Instale e habilite `backup-manager-update-check.timer` e instale `backup-manager-update-download.service`. Somente check/download recebem `AF_INET/AF_INET6`; `backup-manager-update.service` permanece sem rede. Instale apenas a chave pública Ed25519 em `/etc/backup-manager-local/update-public-key.pem`, nunca a privada.

## Atualização de produto

Deploy técnico não é o mecanismo de atualização do cliente. Use somente o pacote assinado e o helper descritos em [UPDATES.md](UPDATES.md), nunca Git ou `install.sh`. Valide a unit em homologação antes de disponibilizá-la.

O deploy instala `backup-manager-cloud-sync.timer`. O serviço oneshot roda a cada minuto; destinos simulados não usam rede e destinos externos são enviados pelo rclone. Consulte [CLOUD_SYNC.md](CLOUD_SYNC.md) e [RCLONE.md](RCLONE.md). Esta função não modifica Nginx, firewall, SSH, FTP/PureDB ou Lifecycle.

`deploy-update.sh` instala as units do importador e só reinicia o timer FTP quando já habilitado. Pure-FTPd não é reinstalado em atualizações; quando presente, é verificado.

O deploy também instala e habilita `backup-manager-lifecycle.timer`, responsável
pela retenção automática diária. Ele não altera Nginx, certificados, SSH ou a
configuração do daemon FTP.

`backup-manager-observability.timer` atualiza a cada 30 segundos o snapshot
local de serviços consumido pelo Dashboard. A requisição web não executa
`systemctl`; consulte [OBSERVABILITY.md](OBSERVABILITY.md).

O Notification Manager reutiliza `backup-manager-worker.timer`: produtores
registram auditoria, a interface apenas enfileira testes e o worker entrega
Telegram com timeout e retry. Não há nova dependência de sistema; consulte
[NOTIFICATIONS.md](NOTIFICATIONS.md).

Este projeto mantem a documentacao operacional principal em portugues:

- [IMPLANTACAO.md](IMPLANTACAO.md)
- [INSTALL.md](INSTALL.md)

Para a FASE 5, a implantacao inclui `paramiko`, `cryptography`, worker systemd,
jobs `ssh` e a chave local `/etc/backup-manager-local/secret.key` para proteger
credenciais SSH.
# Worker Telegram v1.1

Atualizações devem usar somente `scripts/deploy-update.sh`; não execute `install.sh` sobre uma instalação existente. O deploy instala e habilita `backup-manager-telegram-backup.service/.timer`, preserva Nginx e Let's Encrypt e valida as units. O worker necessita leitura em `backups`, escrita em `temporary` e no SQLite.
# Validação de schema no upgrade

Use `bash scripts/deploy-update.sh`, nunca `install.sh`, para atualizar uma
instalação existente. O script cria um backup SQLite antes da migration, aplica
as migrations e executa `schema-check` antes de reiniciar serviços. Qualquer
falha interrompe o deploy. Depois, confirme `integrity-check`, `SCHEMA_OK` e o
estado do timer/worker.
