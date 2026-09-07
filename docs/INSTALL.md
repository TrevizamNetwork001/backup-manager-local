# Instalacao

Para habilitar Pure-FTPd durante a instalação, consulte [FTP.md](FTP.md). Sem `--enable-pure-ftpd`, o daemon não é instalado.

## Requisitos

- Debian/Ubuntu com `apt-get`.
- `python3`.
- Permissao de root.
- Portas 80 e 443 liberadas para acesso web.
- Acesso de saida TCP/22 da VM para os equipamentos que serao copiados por SSH.

## Instalar ou reaplicar

```bash
cd /opt/backup-manager-local
bash scripts/install.sh
```

O instalador pode ser reaplicado. Ele:

- instala Nginx se necessario;
- instala `python3-paramiko` e `python3-cryptography`;
- cria `/opt/backup-manager-local/data`;
- cria `/etc/backup-manager-local/secret.key` se ainda nao existir;
- configura inicialmente somente HTTP; HTTPS deve ser habilitado por `scripts/configure-https.sh`;
- aplica migrations;
- valida o banco SQLite;
- instala unidade `systemd`;
- instala site Nginx;
- remove o site default do Nginx;
- habilita e inicia os servicos.

O arquivo `/etc/backup-manager-local/secret.key` criptografa as senhas SSH salvas
no SQLite. Ele nao deve ser versionado e nao e sobrescrito pelo instalador.
Inclua essa chave no backup administrativo seguro do servidor, pois sem ela as
credenciais armazenadas nao podem ser abertas.

## Configurar backup SSH

1. Acesse o detalhe do equipamento.
2. Em `Backup SSH`, selecione o driver:
   - `MikroTik RouterOS`: usa `/export`.
   - `Huawei VRP`: usa `screen-length 0 temporary` e `display current-configuration`.
   - `Cisco IOS`: usa `terminal length 0` e `show running-config`.
   - `Generico SSH`: exige comando customizado.
3. Cadastre uma credencial SSH com usuario, senha e porta.
4. Clique em `Testar SSH`.
5. Execute `Executar backup SSH agora` ou crie job com metodo `ssh`.

Recomenda-se criar usuario dedicado no equipamento, com permissao minima suficiente
para ler a configuracao. Evite comandos que exportem segredos. Em MikroTik, o
padrao desta fase e `/export`; `/export show-sensitive` deve ser usado somente
quando o risco for aceito explicitamente.

## HTTPS

HTTPS é opcional. Após o primeiro acesso, abra **Configurações → Acesso e HTTPS**,
informe domínio e e-mail, valide o acesso e confirme a emissão Let's Encrypt.
Instalações restritas a IP privado podem continuar normalmente em HTTP.

O helper não cria certificado autoassinado e preserva a configuração Nginx
anterior. Para uso administrativo direto:

```bash
sudo scripts/configure-https.sh --action issue --domain backup.exemplo.com.br --email admin@exemplo.com.br --confirm
```

Depois execute:

```bash
nginx -t
systemctl reload nginx
```

## Verificacao

```bash
systemctl is-active backup-manager-local nginx
curl -fsS http://127.0.0.1/login
python3 -m backup_manager.cli ssh-test --equipment-id 1
```

Depois da instalação, consulte [SETTINGS.md](SETTINGS.md) para configurar a
identidade, os usuários e o backup de migração.
