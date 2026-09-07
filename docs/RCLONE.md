# Armazenamento externo via rclone

O rclone é o transporte único para cópias externas. Ele permite usar Google Drive,
Amazon S3, Backblaze B2, Wasabi, MinIO, SFTP e outros provedores sem implementar
credenciais específicas dentro do Backup Manager.

O backup local continua sendo a origem. Um envio externo nunca move, altera ou
apaga o arquivo local.

## Configuração inicial

Na tela **Sincronização externa**, use **Iniciar configuração visual**, escolha o
provedor e responda às etapas apresentadas pelo próprio rclone. Campos secretos
são protegidos e não são incluídos nos logs.

Como alternativa, execute pelo terminal:

```bash
rclone config --config /etc/backup-manager-local/rclone.conf
chmod 600 /etc/backup-manager-local/rclone.conf
```

Crie um remote e conclua a autenticação solicitada pelo provedor. Para Google
Drive, o próprio rclone abre ou apresenta o endereço de autorização. Em 2026,
use um Client ID e Client Secret próprios; o Client ID compartilhado do rclone
está sendo descontinuado pelo Google.

Valide antes de cadastrar no Backup Manager:

```bash
rclone listremotes --config /etc/backup-manager-local/rclone.conf
rclone lsd REMOTE: --config /etc/backup-manager-local/rclone.conf
```

Depois informe o remote e a pasta no segundo formulário da tela. O sistema testa
o acesso antes de salvar.

## Assistente visual

Para Google Drive, prefira **Conectar com Google**. Crie no Google Cloud um
cliente OAuth do tipo **Aplicativo da Web**, cadastre a URL de retorno exibida
no painel e informe uma vez o Client ID e a chave secreta. O login, o retorno ao
Backup Manager, a criação do remote e o teste da pasta ocorrem pelo navegador;
não é necessário instalar rclone no computador nem transportar comandos ou
JSON. A autorização usa PKCE, estado vinculado à sessão administrativa e o
escopo reduzido `drive.file`.

O assistente rclone tradicional permanece para os demais provedores.

Ao concluir a conexão com o Google, o destino é validado e uma política global
é ativada automaticamente para novos backups SSH e FTP. O armazenamento local
continua sendo a origem e não é removido depois do envio. Backups antigos não
são enviados em massa automaticamente; podem ser adicionados à fila pela tela
de detalhes quando necessário.

O assistente da tela **Sincronização externa → Destinos** tem duas etapas:

1. **Conectar o provedor:** informe o nome do remote e escolha S3, B2, SFTP,
   OneDrive ou Dropbox. O rclone fornece as perguntas de autenticação.
2. **Criar o destino:** escolha o remote conectado, defina o nome exibido e a
   pasta-base, normalmente `BackupManager`, e teste a conexão.

Para servidor sem navegador, escolha `No` em `Config Is Local`. Execute o
comando `rclone authorize` no computador com navegador e cole o JSON de retorno
somente no campo do assistente. Nunca envie o comando, Client Secret ou token
para chats, tickets ou logs.

## Organização dos arquivos

Os envios são organizados automaticamente pelo nome da instalação, hostname e
data do backup:

```text
PEDRAS TELECOM/
    └── CE-VPN/
        └── 03-08-2026/
            └── backup.rsc
```

O agendamento define quando o backup é executado; a pasta de data identifica o
dia em que o arquivo foi recebido. A organização vale para novos envios;
arquivos antigos só são movidos mediante uma manutenção explícita.

## Política e Telegram

Depois de conectar o remote, crie uma política global ou por equipamento e
ative **Envio automático**. Backups SSH e FTP elegíveis entram na fila a cada
novo recebimento. O Telegram informa equipamento, destino, pasta remota,
tamanho e horário quando o upload rclone termina.

Se o Google retornar `unauthorized_client`, o Client ID ou Secret foi revogado,
alterado ou não corresponde ao token. Recrie/revalide a credencial no Google
Cloud, reconecte o remote e teste o destino antes de reativá-lo.

## Segurança

- `rclone.conf` deve permanecer `0600 root:root`;
- tokens e mensagens técnicas do rclone não são exibidos nem gravados na auditoria;
- a aplicação aceita somente nome de remote e caminho validados;
- nenhum argumento ou comando livre é executado;
- o worker revalida caminho local, tamanho e SHA-256 antes do envio;
- `copyto --immutable` evita sobrescrever silenciosamente um objeto existente.

Para criptografia adicional do conteúdo e dos nomes, crie um remote `crypt` no
próprio rclone e cadastre esse remote no Backup Manager.

## Migração do Google Drive antigo

A migration 031 desativa destinos OAuth diretos do Google Drive. Tokens e
histórico não são apagados automaticamente. Depois que o novo remote estiver
validado e os backups confirmados, o legado poderá ser removido em uma manutenção
separada.
