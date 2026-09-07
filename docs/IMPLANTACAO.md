# Implantacao Realizada

## Chave pública de updates

Copie apenas a chave pública Ed25519 para `/etc/backup-manager-local/update-public-key.pem`, com proprietário `root:root` e modo `0644` ou mais restritivo. A chave privada permanece exclusivamente no ambiente de release. Veja [UPDATES.md](UPDATES.md).

Após a migration 012, valide `python3 -m backup_manager.cli cloud-stats` e `python3 -m backup_manager.cli cloud-run-once`. A tela **Backup externo** cria somente destinos simulados; a FASE 10.1 não aceita tokens nem segredos.

A migration 031 adiciona destinos rclone e desativa integrações externas antigas, preservando o histórico. Siga [RCLONE.md](RCLONE.md); nenhuma mudança em Nginx, FTP, SSH ou Lifecycle é necessária.

FTP deve ser habilitado somente após planejar porta de controle, faixa passiva, UFW/NAT e compatibilidade FTPS conforme [FTP.md](FTP.md).

Data da implantacao: 2026-07-09

Diretorio do sistema:

```text
/opt/backup-manager-local
```

## Componentes implantados

- Aplicacao web local em Python.
- Dependencias SSH instaladas pelo sistema: `paramiko` e `cryptography`.
- Banco SQLite com migrations.
- Painel web com layout responsivo.
- Autenticacao por login e cookie de sessao.
- Usuario admin inicial.
- Troca obrigatoria de senha no primeiro acesso.
- Setup Wizard inicial.
- Cadastro de nome da instalacao/provedor.
- Cadastro de timezone.
- Cadastro de diretorio principal de backups.
- Cadastro de ambiente principal.
- Seeds de grupos padrao.
- Seeds de vendors padrao.
- Cadastro de POPs/localidades.
- CRUD basico de equipamentos.
- Perfis `Admin` e `Leitura/Download`.
- Auditoria basica.
- Dashboard inicial.
- Nginx como proxy reverso.
- HTTPS aplicado no Nginx.
- Redirecionamento HTTP para HTTPS.
- Servico `systemd` para manter a aplicacao ativa.
- Instalador idempotente em `scripts/install.sh`.
- Jobs agendados com worker `systemd timer`.
- Backup real por SSH Pull para drivers Generico SSH, MikroTik RouterOS, Huawei VRP e Cisco IOS.
- Credenciais SSH criptografadas com chave local fora do SQLite.

## Componentes fora do escopo atual

- TFTP.
- SFTP push.
- rclone.
- Licenciamento.

## Servicos

Aplicacao:

```text
backup-manager-local.service
```

Nginx:

```text
nginx.service
```

Worker:

```text
backup-manager-worker.timer
backup-manager-worker.service
```

Comandos uteis:

```bash
systemctl status backup-manager-local --no-pager
systemctl status backup-manager-worker.timer --no-pager
systemctl status nginx --no-pager
systemctl restart backup-manager-local
systemctl reload nginx
```

## Portas

- `127.0.0.1:8080`: aplicacao Python interna.
- `0.0.0.0:80`: Nginx, redireciona para HTTPS.
- `0.0.0.0:443`: Nginx HTTPS.

## URLs

URL local:

```text
https://127.0.0.1/
```

Use o IP privado ou domínio configurado para a instalação. Não mantenha IPs de
laboratório na documentação do cliente.

## Credencial inicial

```text
usuario: admin
senha: ChangeMe!2026
```

A senha provisoria deve ser trocada obrigatoriamente no primeiro acesso.

## Banco de dados

Arquivo SQLite:

```text
/opt/backup-manager-local/data/backup_manager.sqlite3
```

Migrations:

```text
/opt/backup-manager-local/migrations
```

Checagem de integridade:

```bash
cd /opt/backup-manager-local
python3 -m backup_manager.cli integrity-check
```

Resultado esperado:

```text
ok
```

## Aplicacao de atualizacoes locais

Toda alteracao de backend precisa passar por `scripts/deploy-update.sh` antes do
teste no navegador. Apenas commitar ou editar arquivos no repositorio nao muda o
codigo que ja esta carregado pelo processo Python em execucao; o deploy local
padronizado valida o codigo e reinicia os servicos corretos.

```bash
cd /opt/backup-manager-local
sudo bash scripts/deploy-update.sh
```

O script:

- exige root e executa a partir da raiz do projeto;
- mostra commit atual e `git status`;
- valida `git diff --check`, `bash -n scripts/install.sh`, `python3 -m py_compile`
  e `python3 -m unittest discover -s tests`;
- executa `integrity-check`, cria backup seguro do SQLite e aplica migrations;
- reinicia `backup-manager-local.service` e `backup-manager-worker.timer`;
- inicia uma rodada do worker apenas quando o servico do worker nao esta ativo e
  nao ha jobs vencidos;
- valida servicos, `nginx -t` e `GET /login`;
- nao faz `git pull`, nao altera UFW, SSL ou dados de storage;
- recarrega Nginx somente quando a configuracao do site implantada mudou.

## Arquivos de configuracao implantados

Servico systemd:

```text
/etc/systemd/system/backup-manager-local.service
```

Site Nginx:

```text
/etc/nginx/sites-available/backup-manager-local.conf
/etc/nginx/sites-enabled/backup-manager-local.conf
```

HTTPS deve ser configurado posteriormente por `scripts/configure-https.sh`, usando
Let's Encrypt ou certificado próprio fornecido pelo administrador.

Chave local de criptografia das credenciais SSH:

```text
/etc/backup-manager-local/secret.key
```

Essa chave deve ter permissao restrita e backup seguro. Sem ela, credenciais SSH
ja gravadas no SQLite nao podem ser recuperadas.

## Operacao SSH

CLI segura:

```bash
cd /opt/backup-manager-local
python3 -m backup_manager.cli ssh-test --equipment-id 1
python3 -m backup_manager.cli ssh-backup --equipment-id 1
```

Os comandos imprimem somente resumo, codigo de erro e metadados do backup. Eles
nao imprimem senha nem conteudo de configuracao.

## Commit inicial

```text
13274cf feat: inicia base do backup manager local
```
