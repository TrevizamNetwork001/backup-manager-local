# Política de arquivos de atualização

| Classe | Conteúdo | Regra |
|---|---|---|
| managed | `backup_manager/`, `migrations/`, `deploy/systemd/`, scripts controlados, `static/`, `templates/` | Pode ser declarado, assinado e atualizado atomicamente. |
| preserved | `/etc/backup-manager-local/`, banco real, storage, uploads FTP, PureDB, certificados, Nginx e configurações do cliente | Nunca é sobrescrito pelo payload. O SQLite apenas recebe migrations declaradas e possui backup prévio. |
| generated | caches, bytecode, temporários e relatórios locais | Não entra no pacote; pode ser recriado. |
| forbidden | Git, `install.sh`, UFW, Nginx, Let's Encrypt, PureDB, secrets, tokens, client secrets e arquivos de usuário | Rejeição do pacote. |

Remoções precisam aparecer individualmente no manifesto com ação `remove`. Paths são relativos à raiz do produto e validados novamente no staging e na aplicação.

