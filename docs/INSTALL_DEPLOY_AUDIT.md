# Auditoria de instalação e deploy

Data da auditoria: 2026-07-10

## Estado encontrado

Os arquivos responsáveis pela tela de login são `backup_manager/app.py` (HTML
gerado por `login_page`) e `static/app.css` (layout, card e responsividade).
Os hashes foram preservados no backup pré-refatoração em
`/var/backups/backup-manager-local/pre-refactor/20260710224329/login-ui.sha256`.

`scripts/install.sh` anteriormente reinstalava o template Nginx e gerava o
certificado autoassinado; por isso uma execução em instalação existente podia
reverter HTTPS. O `deploy-update.sh` também possuía sincronização de template
Nginx; foi alterado para preservar configurações Let’s Encrypt, mas a
separação completa ainda exige os scripts dedicados.

O instalador foi protegido contra reexecução normal: ao detectar o banco ou a
unit existente, aborta antes de alterar arquivos. O backup completo desta
auditoria está em `/var/backups/backup-manager-local/pre-refactor/20260710224329`.

## Riscos identificados

- `install.sh` concentra instalação inicial, FTP, Nginx, HTTPS e units.
- O login é HTML inline em `app.py`; não existe template separado para ele.
- `static/app.css` é um arquivo crítico e não deve ser copiado por instaladores.
- A interface atual precisa ser protegida por hash antes/depois de deploy.

## Direção aprovada

`install.sh` será somente instalação nova; `deploy-update.sh` somente código,
migrations e units; FTP e HTTPS terão fluxos independentes e dry-run.
