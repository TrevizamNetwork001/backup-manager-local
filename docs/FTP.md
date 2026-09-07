# Recebimento de backups por FTP

## Arquitetura

FTP é um fluxo *push*: o equipamento decide quando envia. O Pure-FTPd autentica uma conta virtual PureDB, chrootada em `ftp-incoming/accounts/<uuid>/incoming`. O importador aguarda duas observações sem mudança e `ftp_stable_seconds`, valida, calcula SHA-256 e move atomicamente ao storage definitivo. O registro usa `source_method=ftp`, `backup_reason=event` e `backup_status=available`.

Ao contrário do SSH *pull*, não existe job que “execute FTP”; uma conta existente também não representa backup bem-sucedido. Os campos de expectativa ficam preparados para monitoramento futuro.

## Instalação, portas e rede

```bash
sudo bash scripts/install.sh --enable-pure-ftpd \
  --ftp-control-port 21 --ftp-passive-start 30000 \
  --ftp-passive-end 30100 --enable-ufw
```

O instalador cria `backupftp` sem shell, configura PureDB, chroot, anonymous/FXP desativados, limites, helper e timer. Antes, salva `/etc/pure-ftpd` em `/var/backups/backup-manager-local/pure-ftpd/<timestamp>/`. Ele não reseta UFW, não altera 22/80/443 e não abre a porta 20.

Para NAT, informe `--ftp-public-ip 203.0.113.10` e encaminhe na rede a porta de controle e toda a faixa passiva. Não há descoberta de IP público nem mudança automática do roteador. Em rede privada, omita IP público e use o endereço interno; domínio e certificado não são exigidos. `--ftp-public-ipv6` registra IPv6, mas exposição e firewall continuam sob responsabilidade da rede.

## FTPS opcional

`--enable-ftps --ftp-tls-domain ftp.exemplo.com` usa os arquivos Let's Encrypt existentes e monta um PEM modo `0600` em `/etc/ssl/private`; a chave não vai para diretório público. FTP simples continua disponível para equipamentos legados, mas transmite usuário, senha e conteúdo sem confidencialidade. Prefira FTPS e restrição de rede quando suportados.

## Contas, senha e permissões

O Admin pode criar uma conta de backup vinculada a um equipamento ou uma conta
independente do tipo `file_server`. A conta independente não participa do
importador, não cria backup artificial e mantém seus arquivos no diretório
isolado da própria conta. Contas antigas são migradas como tipo `backup`, sem
alteração do vínculo ou histórico.

`upload_only` é o padrão; `upload_and_list` serve à compatibilidade e
`read_write` fica bloqueado. Somente envio; listagem pode estar disponível por
compatibilidade. Username aceita 3–32 caracteres (`a-z`, números, hífen e
underscore`); nomes privilegiados são recusados.

Importante: o perfil Pure-FTPd atual cria uploads como *write-only*. Uma conta
`file_server` permite separar arquivos sem cadastrar equipamento, mas não muda
silenciosamente a política global para leitura e escrita irrestritas. Arquivos
destinados a download precisam ser publicados com permissões controladas por
uma futura ação administrativa específica.

No perfil instalado, arquivos novos usam modo write-only, `KeepAllFiles` e `NoRename`: o teste direto confirmou `STOR`, negou `RETR`, `DELE` e `RNFR/RNTO`, e manteve `LIST/NLST` por compatibilidade. O chroot impede acesso ao pai, embora `CWD ..` na raiz responda sucesso e permaneça em `/`. Consulte [FTP_SECURITY_TESTS.md](FTP_SECURITY_TESTS.md) para as respostas observadas. Valide também a operação com o modelo real do equipamento.

A senha aparece uma única vez, em resposta `no-store`, e somente seu hash fica no SQLite. Depois, só pode ser redefinida. O helper recebe senha via stdin; ela não aparece em argumentos, auditoria ou logs.

```text
Servidor: IP ou hostname configurado
Porta: 21
Usuário: usuário da conta
Pasta: /
Modo: passivo
```

A restrição IP/CIDR é registrada, mas PureDB não aplica bloqueio seguro por origem por usuário nesta arquitetura. Ela não substitui firewall; não são geradas centenas de regras UFW por conta.

## Importação e rejeições

O polling ocorre pelo timer. Arquivos vazios, grandes, links, tipos especiais, hardlinks, extensões proibidas, nomes suspeitos e equipamentos inativos são movidos para `rejected`, sem exclusão automática. Compactados não são extraídos e `ftp-incoming` não é servido pelo Nginx. Perfil limitado vê somente recebimentos importados/duplicados.

Duplicidade é deliberadamente mantida como nova versão: novo recebimento comprova um novo evento. O hash fica registrado para deduplicação futura.

## Operação e troubleshooting

```bash
python3 -m backup_manager.cli ftp-config-check
python3 -m backup_manager.cli ftp-stats
python3 -m backup_manager.cli ftp-scan-once
python3 -m backup_manager.cli ftp-account-sync --account-id 1
python3 -m backup_manager.cli ftp-accounts-sync-all
journalctl -u pure-ftpd -u backup-manager-ftp-importer.service --since today
```

Se login funcionar mas upload travar, confira faixa passiva no daemon, UFW e NAT. Como a senha não é recuperável, `sync-all` verifica contas; para recriar credencial ausente, redefina a senha.

No backup/restore preserve `/etc/pure-ftpd/pureftpd.passwd`, `pureftpd.pdb` e configuração juntos, com acesso restrito. Refaça o banco com `pure-pw mkdb` se necessário e valide antes de reiniciar. Nunca versione PureDB.

Teste primeiro com arquivo local não sensível: autenticação, chroot, modo passivo, bloqueio do diretório pai, estabilidade, histórico/auditoria e isolamento entre duas contas. Só então use equipamento real de laboratório.
