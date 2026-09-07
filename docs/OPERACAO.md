# Operacao

## Acesso

Use:

```text
http://IP_PRIVADO/
```

ou, localmente na VM:

```text
http://127.0.0.1/
```

HTTPS é opcional e deve ser configurado posteriormente com domínio válido.

## Primeiro acesso

```text
usuario: admin
senha: ChangeMe!2026
```

Ao autenticar, o sistema redireciona para a troca obrigatoria de senha.

## Setup Wizard

O setup inicial coleta:

- nome da instalacao;
- nome do provedor;
- timezone;
- diretorio principal de backups;
- ambiente principal;
- POPs/localidades.

Depois do setup, o Centro de Operações passa a ser exibido.

## Inventario

O cadastro basico de equipamentos registra:

- hostname;
- IP/endereco;
- vendor;
- grupo;
- POP/localidade;
- ambiente;
- notas.

Backups SSH usam a credencial e o driver configurados no equipamento. Contas
FTP recebem backups por push e permanecem isoladas por chroot.

## Auditoria

Eventos de acesso, equipamentos, credenciais, backups, jobs, FTP, Lifecycle e
notificações são gravados em `audit_log`.

## Validação operacional

```bash
python3 -m backup_manager.cli integrity-check
python3 -m backup_manager.cli storage-check --hash
python3 -m backup_manager.cli jobs-check
python3 -m backup_manager.cli ftp-config-check
python3 -m backup_manager.cli notification-check
```
