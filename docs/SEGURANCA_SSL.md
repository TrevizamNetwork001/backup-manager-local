# Seguranca e SSL

## Autenticacao

- Usuario inicial `admin`.
- Senha provisoria obrigatoriamente trocada no primeiro acesso.
- Hash de senha com PBKDF2-SHA256.
- Sessao por token aleatorio em cookie `HttpOnly`.
- Expiracao de sessao em 8 horas.

## Perfis

- `Admin`: acesso administrativo.
- `Leitura/Download`: perfil previsto para leitura e download.

Telas administrativas exigem permissao de admin. Usuarios de `Leitura/Download`
podem ver que uma credencial SSH existe, mas nao veem senha, nao criam, nao editam
e nao testam credenciais.

## Credenciais SSH

- Senhas SSH nao sao armazenadas em texto puro.
- O SQLite guarda somente valores criptografados.
- A chave fica fora do banco em `/etc/backup-manager-local/secret.key`.
- O instalador cria a chave com permissao restrita e nao sobrescreve chave existente.
- Campos de senha nao sao preenchidos ao editar credencial.
- Se o campo de senha ficar vazio na edicao, a senha atual e preservada.
- Logs, auditoria e HTML nao devem conter senha nem conteudo de backup.

Inclua `/etc/backup-manager-local/secret.key` no backup seguro do servidor. Sem
essa chave, credenciais SSH salvas anteriormente nao podem ser descriptografadas.

## Recomendacoes para equipamentos

- Use usuario SSH dedicado para backup.
- Aplique permissao minima para leitura da configuracao.
- Prefira comandos sem dados sensiveis.
- No MikroTik, o padrao e `/export`. O comando `/export show-sensitive` pode expor
segredos e deve ser usado somente com aceitacao explicita de risco.

## HTTPS

O acesso inicial usa HTTP na porta 80. HTTPS só deve ser habilitado por
`scripts/configure-https.sh` com Let's Encrypt ou certificado próprio válido.

## HTTP

A porta 80 atende diretamente até que HTTPS seja configurado.

## Headers

O Nginx aplica:

- `X-Frame-Options: SAMEORIGIN`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: same-origin`

## Observacao sobre certificado real

Para produção, configure um domínio e execute `scripts/configure-https.sh`.
