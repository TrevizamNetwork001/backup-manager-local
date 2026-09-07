# Backup Manager Local

Versão atual `v1.1.0` (canal `stable`). A administração está consolidada na [Central de Configurações](docs/SETTINGS.md), com identidade visual, perfis simples, HTTPS opcional e migração sanitizada da configuração.

Atualizações usam `.bmu` assinado com Ed25519. A consulta do índice HTTPS e o download seguro nunca aplicam uma versão automaticamente. Consulte [docs/UPDATES.md](docs/UPDATES.md) e [docs/UPDATE_REPOSITORY.md](docs/UPDATE_REPOSITORY.md).

A cópia opcional via [rclone](docs/RCLONE.md) mantém o armazenamento local como origem e permite Google Drive, S3, B2, SFTP e outros provedores.

Backup Manager Local: painel web Python + SQLite para inventario de equipamentos,
storage local de backups, jobs agendados e backup real por SSH Pull.

Recebimento FTP Push com Pure-FTPd: [docs/FTP.md](docs/FTP.md).

Backups de OLT por SSH/Telnet com envio FTP: [docs/OLT_BACKUPS.md](docs/OLT_BACKUPS.md).

Retenção, lixeira e integridade: [docs/BACKUP_LIFECYCLE.md](docs/BACKUP_LIFECYCLE.md).

Centro de Operações e observabilidade: [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).

Preservação dos layouts modernos, referências visuais e checklist de deploy:
[docs/UI_RESTORATION_2026-08-01.md](docs/UI_RESTORATION_2026-08-01.md).
Registro completo do trabalho realizado em 01/08/2026:
[docs/CHANGELOG_2026-08-01.md](docs/CHANGELOG_2026-08-01.md).

Configuração e validação das integrações Google Drive e Telegram em 05/09/2026:
[docs/CONFIGURACAO_2026-09-05.md](docs/CONFIGURACAO_2026-09-05.md).

Alertas e relatórios operacionais: [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md).

Notas e validação da versão estável: [docs/RELEASE_1.1.0.md](docs/RELEASE_1.1.0.md).

## Executar

```bash
cd /opt/backup-manager-local
python3 run.py
```

Acesse `http://127.0.0.1:8080`.

Para desenvolvimento com autoreload de Python e templates, use
`BACKUP_MANAGER_PORT=8081 ./scripts/run-dev.sh`. Alterações em templates, CSS e
JavaScript aparecem ao atualizar o navegador. Consulte
[Desenvolvimento da interface](docs/DESENVOLVIMENTO.md).

Para validar e publicar uma alteração visual completa com restart somente após
os testes passarem, use `sudo ./scripts/deploy-ui.sh`.

## Instalar como sistema com Nginx

```bash
cd /opt/backup-manager-local
sudo bash scripts/install.sh
```

O instalador:

- instala Nginx quando necessario;
- instala dependencias Python de SSH (`paramiko` e `cryptography`);
- instala inicialmente somente HTTP na porta 80;
- cria `/etc/backup-manager-local/secret.key` sem sobrescrever chave existente;
- aplica migrations e valida `PRAGMA integrity_check`;
- cria o servico `backup-manager-local`;
- configura Nginx HTTP como proxy para `127.0.0.1:8080`;
- habilita e inicia os servicos.
- reinicia os servicos da aplicacao ao final da instalacao ou atualizacao dos
  arquivos de servico.

Documentação completa:

- [Instalacao](docs/INSTALL.md)
- [Operacao](docs/OPERACAO.md)
- [Arquitetura](docs/ARQUITETURA.md)
- [Seguranca e SSL](docs/SEGURANCA_SSL.md)
- [Central de Configurações](docs/SETTINGS.md)
- [Índice da documentação](docs/README.md)

Usuario inicial:

- usuario: `admin`
- senha provisoria: `ChangeMe!2026`

## Validar

```bash
python3 -m unittest discover -s tests
python3 -m backup_manager.cli integrity-check
python3 -m py_compile run.py backup_manager/*.py
python3 -m backup_manager.cli ssh-test --equipment-id 1
```

## Aplicar atualizacoes locais

Toda alteracao de backend precisa passar por `scripts/deploy-update.sh` antes do
teste no navegador. Isso garante que o processo Python em execucao foi
reiniciado e esta usando o codigo commitado/validado.

```bash
cd /opt/backup-manager-local
sudo bash scripts/deploy-update.sh
```

O fluxo nao faz `git pull` automaticamente, nao altera UFW, SSL ou dados de
storage, e nao recarrega Nginx quando a configuracao implantada nao mudou.

## Backup SSH

No detalhe do equipamento, o Admin configura:

- driver SSH: Generico SSH, MikroTik RouterOS, Huawei VRP ou Cisco IOS;
- credencial SSH com usuario/senha;
- teste de conexao;
- job manual ou agendado com metodo `ssh`.

Comandos padrao:

- MikroTik RouterOS: `/export`
- Huawei VRP: `screen-length 0 temporary` e `display current-configuration`
- Cisco IOS: `terminal length 0` e `show running-config`
- Generico SSH: exige comando customizado definido pelo Admin.

Credenciais sao criptografadas no SQLite com a chave local
`/etc/backup-manager-local/secret.key`. Guarde backup seguro dessa chave: sem ela,
as senhas ja salvas nao podem ser recuperadas.

## Escopo atual

Estão implementados inventário, backups manuais e SSH Pull, recebimento FTP,
agendamentos, retenção, Centro de Operações, notificações Telegram, relatórios e
cópia opcional via rclone. A v1.1 acrescenta resumos operacionais Telegram
e cópia secundária de backups para chats, canais e tópicos, preservando o storage
local e as integrações SSH, FTP e rclone. Veja
[Telegram avançado](docs/TELEGRAM_ADVANCED.md) e
[Cópia de backups via Telegram](docs/TELEGRAM_BACKUP.md).
