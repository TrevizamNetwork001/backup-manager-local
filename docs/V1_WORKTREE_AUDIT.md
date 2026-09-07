# Auditoria do working tree para v1.0.0

Data da auditoria: 2026-07-13 (America/Sao_Paulo)

Base auditada: `5e2dea7` (`docs: prepara versao 1.0`)

Tag local `v1.0.0`: ausente no início da auditoria.

## Origem reconstruída

O Git não registra a autoria ou o comando que criou arquivos não rastreados. A
origem abaixo foi reconstruída por conteúdo, imports, rotas, migrations, units,
testes já existentes, datas dos arquivos e documentação. Os testes de cloud,
Google Drive e apresentação foram adicionados no commit `0ea85ac`, mas os
módulos, migrations, templates e assets que eles importam permaneceram fora do
Git. Isso explica por que funcionalidades testadas não entraram na tag anterior.

As datas locais separam três conjuntos: FASE 10.1/10.2 em 2026-07-11,
apresentação em 2026-07-12 (com ajuste de sidebar em 2026-07-13), e FASE 11.2 em
2026-07-12. Não há commits anteriores contendo os arquivos novos; portanto eles
não duplicam funcionalidades já versionadas.

## Inventário e decisão

Legenda de testes: `cloud` = `tests/test_cloud_sync.py`; `drive` =
`tests/test_google_drive.py`; `presentation` = `tests/test_presentation.py`;
`repository` = `tests/test_update_repository.py`; `suite` = testes existentes
que importam a aplicação ou o módulo integrado.

| Arquivo | Função, fase e dependências | Uso e testes | Decisão v1.0.0 |
|---|---|---|---|
| `backup_manager/cloud_sync.py` | Fila, elegibilidade, simulação, retry, lock e transporte Drive; depende de storage, Google Drive e migrations 012–014. FASE 10.1/10.2. | Importado por app, CLI, worker, SSH e FTP; `cloud`, `drive`, `suite`. | Incluir. |
| `backup_manager/cloud_worker.py` | Entrypoint do worker; depende de cloud sync e DB. FASE 10.1. | `backup-manager-cloud-sync.service`; `cloud` e validação da unit. | Incluir. |
| `migrations/012_cloud_sync.sql` | Tabelas, índices e settings da fila simulada. FASE 10.1. | Descoberta automática e ordenada por `db.migrate`; `cloud`. | Incluir. |
| `deploy/systemd/backup-manager-cloud-sync.service` | Worker oneshot endurecido. FASE 10.1. | Aponta para módulo versionado e paths atuais; systemd verify. | Incluir. |
| `deploy/systemd/backup-manager-cloud-sync.timer` | Disparo por minuto do worker. FASE 10.1. | Instalado/habilitado por deploy-update; systemd verify. | Incluir. |
| `docs/CLOUD_SYNC.md` | Operação e limites do modo simulado. FASE 10.1. | Referência operacional. | Incluir. |
| `backup_manager/google_drive.py` | OAuth web/PKCE, tokens criptografados e upload resumível. FASE 10.2. | Importado por app e cloud sync; `drive`, `cloud`. | Incluir. |
| `migrations/013_google_drive.sql` | Evolui as tabelas cloud e cria conexão Drive. FASE 10.2. | Executada após 012; `drive`. | Incluir. |
| `migrations/014_google_web_oauth.sql` | Vincula OAuth a sessão/usuário e persiste retomada. FASE 10.2. | Executada após 013; `drive`. | Incluir. |
| `docs/GOOGLE_DRIVE.md` | Configuração OAuth web e garantias operacionais. FASE 10.2. | Referência de implantação. | Incluir. |
| `backup_manager/update_repository.py` | Índice assinado, SemVer, SSRF, check, fila e download seguro. FASE 11.2; depende de updater, version e migration 016. | Importado por app e CLI; `repository`, updater e suite. | Incluir. |
| `migrations/016_update_repository.sql` | Downloads e settings do repositório. FASE 11.2. | Executada após a 015 já rastreada; `repository`/suite. | Incluir. |
| `deploy/systemd/backup-manager-update-check.service` | Consulta de índice com rede e hardening. FASE 11.2. | CLI `update-check`; ajustada para o usuário operacional root da v1.0. | Incluir. |
| `deploy/systemd/backup-manager-update-check.timer` | Consulta periódica, sem aplicação automática. FASE 11.2. | Instalada e habilitada por deploy-update; systemd verify. | Incluir. |
| `deploy/systemd/backup-manager-update-download.service` | Worker explícito de download/validação. FASE 11.2. | CLI `update-download-run`; instalada sem timer; systemd verify. | Incluir. |
| `scripts/publish-update-index.py` | Gera índice e assinaturas em estação de release; depende de updater/cryptography. FASE 11.2. | Compilação e documentação de publicação. | Incluir. |
| `docs/UPDATE_REPOSITORY.md` | Modelo de ameaça, configuração e operação. FASE 11.2. | Referência administrativa. | Incluir. |
| `docs/UPDATE_PUBLICATION.md` | Procedimento offline de publicação. FASE 11.2. | Referência de release. | Incluir. |
| `tests/test_update_repository.py` | Assinatura, schema, SemVer, SSRF e integração de deploy. FASE 11.2. | Descoberto pela suíte. | Incluir. |
| `backup_manager/dev_server.py` | Autoreload opt-in de desenvolvimento. Apresentação/dev. | Chamado por `run-dev.sh`; `presentation`. | Incluir. |
| `backup_manager/presentation.py` | Templates e URLs versionadas de assets. Apresentação. | Importado por app; `presentation` e suite. | Incluir. |
| `backup_manager/presentation_check.py` | Valida templates, CSS e JS. Apresentação/deploy. | Chamado por CLI/deploy-ui; `presentation`. | Incluir. |
| `scripts/run-dev.sh` | Runner local, sem alterar produção. Desenvolvimento. | Chama dev_server; shell e `presentation`. | Incluir. |
| `scripts/deploy-ui.sh` | Validação e restart controlado da interface. Deploy. | Fluxo opcional documentado; shell e `presentation`. | Incluir. |
| `templates/base.html` | Shell HTML e referências versionadas a CSS/JS. Apresentação. | Renderizado por app; `presentation`. | Incluir. |
| `templates/login.html` | Login. Apresentação. | Renderizado pela rota `/login`; `presentation`/suite. | Incluir. |
| `templates/dashboard.html` | Centro de operações. Apresentação/observabilidade. | Renderizado pela rota `/`; `presentation`, observability, suite. | Incluir. |
| `templates/components/sidebar.html` | Navegação e usuário. Apresentação. | Incluído pelo renderer do app; `presentation`. | Incluir. |
| `templates/components/topbar.html` | Barra superior. Apresentação. | Incluído pelo renderer do app; `presentation`. | Incluir. |
| `static/app.js` | Menu, notices, estados de botões e polling SSH. Apresentação. | Referenciado por `base.html` via `static_url`; `presentation`. | Incluir. |
| `static/app.css` | Layout, dashboard, responsividade e login. Apresentação. | Referenciado por todos os layouts; CSS check e suite. | Incluir. |
| `docs/DESENVOLVIMENTO.md` | Ambiente dev e deploy visual. Apresentação/dev. | Referência para mantenedores. | Incluir. |
| `backup_manager/app.py` | Integra templates, dashboard, cloud/Drive e repositório remoto em rotas administrativas. FASE 10.1/10.2/11.2/apresentação. | Aplicação atual; testes app/cloud/drive/presentation/repository. | Incluir nos commits funcionais por trechos coerentes. |
| `backup_manager/cli.py` | Comandos cloud e update remoto; reforça confirmação de apply. FASE 10.1/11.2. | Units e operação manual; suite. | Incluir por funcionalidade. |
| `backup_manager/ftp_importer.py` | Auto-enfileira backups FTP elegíveis. FASE 10.1. | Importador atual; cloud/app. | Incluir com cloud. |
| `backup_manager/ssh.py` | Auto-enfileira backups SSH elegíveis. FASE 10.1. | Jobs SSH atuais; cloud/app. | Incluir com cloud. |
| `backup_manager/notifications.py` | Eventos seguros de cloud e Drive. FASE 10.1/10.2. | Dispatcher atual; notifications/cloud/drive. | Incluir com cloud/Drive. |
| `backup_manager/security.py` | Recusa chave local insegura/symlink. FASE 10.2/hardening. | Criptografia Drive e demais segredos; drive/suite. | Incluir com Google Drive. |
| `backup_manager/observability.py` | Métricas cloud e melhorias do dashboard/cobertura. FASE 10.1/apresentação. | Dashboard atual; observability/app/presentation. | Incluir com apresentação/observabilidade. |
| `scripts/deploy-update.sh` | Migra, sincroniza units e valida produção sem tocar certificados. FASE 10.1/11.2/deploy. | Único fluxo autorizado nesta auditoria; suite e shell. | Incluir por funcionalidade; instalar todas as novas units. |
| `scripts/install.sh` | Acrescenta cloud a instalações novas; não será executado. FASE 10.1. | Somente instalação inicial; testes de instalador e shell. | Incluir, sem executar. |
| `docs/ARQUITETURA.md` | Documenta templates/assets. Apresentação. | Referência arquitetural. | Incluir em documentação final. |
| `docs/DEPLOYMENT.md` | Central de configuração e units de update. Deploy/FASE 11.2. | Referência operacional. | Incluir em documentação final. |
| `docs/RC1_VALIDATION.md` | Acrescenta checklist histórico da FASE 10.1. | Referência histórica. | Incluir em documentação final. |
| `docs/UPDATES.md` | Liga FASE 11.1 à 11.2. | Referência operacional. | Incluir em documentação final. |
| `docs/UPDATE_PACKAGE_FORMAT.md` | Documenta assinatura destacada do transporte. FASE 11.2. | Publicador e operador. | Incluir em documentação final. |
| `.gitignore` | Regras de artefatos locais. Auditoria v1.0. | Git. | Incluir com documentação/auditoria. |
| `ideias.png` | Mockup/moodboard visual local de 1264×843; não é referenciado por Python, templates, CSS, JS, docs ou deploy. | Nenhum uso/teste no produto. | Preservar fisicamente, não versionar, ignorar explicitamente. |

## Dependências e completude

- `db.migrate()` descobre `migrations/*.sql` por nome ordenado; a sequência
  completa será 001–016, com 015 já rastreada e 012/013/014/016 incluídas.
- `app.py` renderiza somente os cinco templates inventariados. `base.html`
  referencia `app.js` e `app.css` por `static_url`, e ambos existem.
- Todos os imports Python novos resolvem para arquivos inventariados e destinados
  ao Git. Não há import ou include que dependa de `ideias.png`, banco, storage,
  credenciais, certificados, PureDB, logs, pacotes ou temporários.
- As units cloud apontam para `backup_manager.cloud_worker`; as units de update
  apontam para comandos existentes da CLI. Os paths graváveis correspondem ao
  banco e ao update root usados pelo código.
- A auditoria corrigiu a lacuna em `deploy-update.sh`: agora ele instala as três
  units da FASE 11.2, habilita o timer de consulta e valida sua atividade. O
  worker de download permanece oneshot explícito e nunca aplica a atualização.
- `install.sh` será apenas versionado/validado; não será executado. A aplicação
  da versão será feita exclusivamente por `scripts/deploy-update.sh`.

## Validação e fechamento

Os resultados finais de testes, migrations, serviços, commits, hash e tag serão
registrados no relatório de entrega após o deploy controlado.
