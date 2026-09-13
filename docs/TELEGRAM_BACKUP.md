# Cópia secundária de backups via Telegram

## Fluxo e garantias

O fluxo é sempre:

```text
equipamento → backup local disponível → SHA-256 validado
            → rclone opcional → Telegram opcional
```

O worker Telegram usa o backup existente. Ele não executa SSH/FTP, não cria agendamento, não altera o formato original e não apaga ou marca o backup local como falho. A frequência acompanha exclusivamente os backups que o fluxo normal realmente cria.

## Políticas

As políticas podem ser globais, por grupo ou por equipamento. A regra mais específica vence para cada destino. É possível selecionar SSH, FTP e manual, envio de novos backups, destino/tópico, compactação, tentativas e base do retry.

Status: `queued`, `validating`, `preparing`, `uploading`, `sent`, `retry_wait`, `failed`, `skipped` e `cancelled`.

Antes do envio, o worker exige status local `available`, arquivo regular dentro do diretório de backups, ausência de symlink, tamanho igual ao banco, SHA-256 correto e ausência de trash/quarantine. A unicidade `backup + destination` impede duplicidade.

## Compactação

- `none`: envia o original.
- `zip`: ZIP sem senha e sem credenciais adicionais.
- `gzip`: aplicável a um arquivo único.

O original não é modificado. O pacote é criado no diretório temporário confinado, recebe nome sanitizado e é removido após sucesso ou falha. Extensões já comprimidas não são recomprimidas. ZIP com senha e fragmentação automática não fazem parte da v1.1.

## Limites da Bot API

`telegram_api_mode=public` usa `https://api.telegram.org` e o limite conhecido configurado em `telegram_public_max_file_bytes`. `local` é opt-in, requer `telegram_local_api_base_url` em HTTPS e usa `telegram_local_max_file_bytes`. A aplicação não inicia nem instala uma Bot API local.

Os padrões da migration seguem a [documentação oficial da Bot API](https://core.telegram.org/bots/api): 50 MB para documento multipart na API pública e 2000 MB para upload em servidor Bot API local. Como o Telegram informa que limites podem mudar, ambos permanecem configuráveis.

Não há limite comercial artificial. Se o arquivo preparado exceder a capacidade configurada, o item vira `skipped` com `TELEGRAM_FILE_TOO_LARGE`, um alerta textual pode ser enfileirado e o backup permanece local. O arquivo não é dividido.

## Documento e dados registrados

O envio usa `sendDocument`. A legenda inclui equipamento, grupo, método, data, nome sanitizado, tamanho e confirmação de integridade. Não inclui caminho absoluto, senha, token, conteúdo, IP ou IDs internos.

O histórico registra destino sanitizado, tópico, `message_id`, horário, bytes, duração, tentativa e erro seguro.

## Worker e retry

`backup-manager-telegram-backup.timer` aciona um oneshot a cada minuto. A unit usa usuário não root, lock não bloqueante, storage de backup somente leitura, escrita limitada ao temporário e estado SQLite, timeout e hardening do systemd.

O processamento é sequencial. 429 respeita `retry_after`; timeouts, DNS, TLS, conexão interrompida, 409 e 5xx são transitórios. Credenciais inválidas, falta de permissão, chat/tópico inválido e arquivo grande são definitivos conforme a resposta. Alertas e resumos permanecem na fila textual prioritária e não esperam uma fila grande de documentos.

## CLI

```bash
python3 -m backup_manager.cli telegram-test-file --destination-id 1
python3 -m backup_manager.cli telegram-backup-queue
python3 -m backup_manager.cli telegram-backup-stats
python3 -m backup_manager.cli telegram-backup-enqueue --backup-id 10
python3 -m backup_manager.cli telegram-backup-run-once
python3 -m backup_manager.cli telegram-backup-retry --item-id 3
python3 -m backup_manager.cli telegram-backup-cancel --item-id 3
```

Esses comandos não imprimem token nem caminho absoluto. O teste de arquivo cria conteúdo fictício pequeno no temporário e o remove ao terminar.

## Segurança e RBAC

Somente Admin cria destinos, políticas e mapeamentos. Operador pode consultar e atuar na fila; Leitura apenas consulta. Formulários de alteração usam CSRF. O transporte valida TLS e usa timeout. Logs e auditoria omitem token, URL com token, Authorization, conteúdo do arquivo e caminho absoluto.


## Correção de permissões — 13/09/2026

A fila acumulou 80 itens porque o usuário `backup-manager-telegram` não
conseguia ler arquivos importados do FTP. Diretórios estavam no grupo do FTP
e arquivos preservavam modos restritivos, incluindo 0300. O erro escapava da
validação e encerrava o worker antes de processar o lote.

A importação FTP agora normaliza diretórios para 0750 e arquivos para 0640,
usando o grupo da raiz dos backups (root nesta instalação). O diretório
temporário mantém 0770, como previsto no instalador, para permitir preparação
pelo worker. Os backups disponíveis tiveram permissões normalizadas, sem
alteração de conteúdo. O inventário reversível das permissões anteriores está
em `data/maintenance-backups/telegram-permissions-20260913100251.json`, ignorado
pelo Git. A leitura e SHA-256 dos 75 itens ainda enfileirados foram validados
sob o usuário do serviço.

Erros de leitura na validação passam a registrar `BACKUP_FILE_UNREADABLE` no
item, com descrição visível na interface, sem encerrar o processamento dos
próximos arquivos. Cinco itens receberam esse estado durante a intervenção e
precisam de nova tentativa após a confirmação dos envios.

O primeiro lote retomou entregas. Após uma pausa para confirmação explícita
do destino existente “Backup Telegram”, o usuário autorizou os envios restantes.
Os cinco itens recusados foram reenfileirados e o timer reativado. A recuperação
usa o próprio serviço sequencial, sem executar workers concorrentes. O alerta
não foi ocultado nem seus critérios foram alterados.


As legendas dos documentos agora convertem UTC para o fuso configurado em
`settings.timezone` (America/Sao_Paulo como padrão e fallback). O rótulo
“Enviado em” identifica o horário da cópia ao Telegram, não a data original do
backup: arquivos antigos recuperados da fila mantêm seu nome original.
Exemplo: 13:08 UTC aparece como 10:08 em São Paulo. Mensagens anteriores não
são reescritas. Há teste de conversão com mudança de dia e fuso UTC explícito.
