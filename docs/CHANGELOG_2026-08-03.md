# Alterações de 03/08/2026 — rclone

## Integração externa

- Google Drive direto removido do runtime; rclone passou a ser o transporte único.
- rclone atualizado no servidor para `1.75.0`.
- Assistente visual com seleção de provedor, autenticação por etapas e validação
  sem comandos livres.
- Client ID e Client Secret próprios suportados para evitar a descontinuação do
  Client ID compartilhado do rclone.
- Destinos rclone testam/criam a pasta-base antes de serem salvos.
- Uploads preservam o backup local e usam `copyto --immutable`.

## Organização e notificações

- Caminho remoto: `BackupManager/<grupo>/<hostname>/<DD-MM-AAAA>/<arquivo>`.
- Grupo ausente usa `SEM-GRUPO`.
- Telegram passou a informar equipamento, destino, pasta remota, tamanho e
  horário no sucesso do upload.
- O arquivo já enviado durante a validação foi reorganizado quando a credencial
  permitiu; nenhum backup local foi removido.

## Segurança e migração

- Migration 031 desativa destinos Google legados e preserva histórico.
- Tokens e segredos não são exibidos em logs.
- O destino é desativado automaticamente quando a credencial retorna
  `unauthorized_client`, evitando falhas automáticas contínuas.

## Validação

- 337 testes automatizados aprovados na publicação.
- Serviço web, timer, readiness, FTP e Nginx validados pelo deploy.

Versão publicada: `v1.1.0-rc9` (`d53f1fa`).
