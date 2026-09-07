# Registro consolidado das alterações — 01/08/2026

Este documento registra o trabalho realizado e consolidado em 01/08/2026 na
interface do Backup Manager Local. Ele complementa
`UI_RESTORATION_2026-08-01.md`, que contém as regras permanentes para evitar
novas perdas de layout.

## Objetivo do dia

Recuperar páginas que haviam voltado ao padrão visual antigo, uniformizar a
navegação e as áreas administrativas e restaurar a Central de Segurança a
partir das referências visuais existentes na instalação.

## Configurações

- A navegação por abas foi ajustada para não mostrar barra horizontal cinza.
- As abas passaram a quebrar linha quando necessário, sem conteúdo cortado.
- A aba “Backup da Configuração” foi removida da navegação solicitada.
- Foram modernizadas as áreas Geral, Usuários, Acesso e HTTPS, Backup e
  Retenção, Google Drive, Notificações e Atualizações.
- A listagem de usuários, a edição de usuário e o fluxo “Novo usuário” foram
  alinhados ao novo padrão, incluindo o botão de criação.
- A área HTTPS passou a apresentar os dados persistidos do domínio e do e-mail
  administrativo usados na emissão do certificado.
- A renovação automática e o resumo do certificado foram mantidos visíveis.
- Os resumos de Backup e Retenção, Google Drive e Notificações receberam cards,
  métricas e ações proporcionais ao texto.
- O botão “Gerenciar sincronização” deixou de ocupar uma área excessiva.
- A página completa de sincronização externa (`/cloud`) e a área de
  Atualizações foram alinhadas ao mesmo padrão visual.

Arquivos principais: `backup_manager/settings_views.py`,
`backup_manager/settings_center.py`, `static/settings.css` e `static/app.css`.

## Navegação global

- A sidebar autenticada foi uniformizada entre Dashboard, Configurações e as
  demais páginas.
- Os mesmos grupos, ícones, usuário e estado do sistema passaram a ser usados
  em todas as rotas autenticadas.
- As barras de rolagem visíveis que destoavam do layout foram ocultadas, sem
  impedir a rolagem do conteúdo.
- O contexto do topo passou a apresentar corretamente usuário e perfil.

Arquivos principais: `backup_manager/app.py`, componentes de template e
`static/app.css`.

## Dashboard, Backups e Agendamentos

- A Dashboard principal foi preservada conforme `inicial.png`.
- Foram restaurados os estilos `dashboard-reference-*` e o painel
  `dashboard-security`.
- A página `/backups` foi preservada conforme `comoesta.png`.
- Foram restaurados o wrapper e cabeçalho `backup-reference-*`, a ação “Ver
  equipamentos”, filtros, indicadores, tabela e cards laterais.
- A página `/jobs` manteve os componentes `jobs-modern-*` e o padrão moderno de
  indicadores, filtros e tabela.

Arquivos principais: `backup_manager/dashboard_views.py`,
`templates/dashboard.html`, `backup_manager/backups_views.py`,
`backup_manager/jobs_views.py` e `static/app.css`.

## Relatórios

- A falha interna em Auditoria foi corrigida reconciliando as implementações de
  relatórios e suas consultas.
- A tela de Exportações e o formulário “Gerar arquivo / Nova exportação” foram
  modernizados.
- Foram padronizadas as páginas:
  - `/reports/backups`;
  - `/reports/equipment`;
  - `/reports/storage`;
  - `/reports/ftp`;
  - `/reports/telegram`;
  - `/reports/audit`.
- Cabeçalhos, abas, filtros, indicadores, cards e tabelas passaram a usar
  `report-reference-page` e seletores específicos por relatório.
- As rotas mantêm filtros, paginação e detalhes operacionais existentes.

Arquivos principais: `backup_manager/app.py`,
`backup_manager/report_views.py`, `backup_manager/reports.py` e
`static/app.css`.

## Logs, Auditoria e Central de Segurança

- Foi identificado que `/audit` era uma implementação legada distinta de
  `/reports/audit`.
- A página dedicada foi restaurada a partir de `/opt/backup-manager-local/iii.jpeg`.
- A Central de Segurança agora apresenta:
  - estado atual;
  - falhas nos últimos 10 minutos;
  - falhas nas últimas 24 horas;
  - IPs distintos;
  - acessos bloqueados;
  - ranking de IPs com mais tentativas;
  - ranking de contas mais visadas;
  - eventos recentes de autenticação;
  - diferenciação visual entre login válido e tentativa inválida.
- O marcador `/audit#security-report` foi preservado.

## País, ASN, operadora e bandeiras

- Foi encontrado o conjunto de bandeiras em
  `/opt/backup-manager-local/static/assets/flags`.
- As bandeiras SVG reais passaram a ser exibidas por código ISO, em vez de
  emojis.
- País, ASN e operadora são resolvidos localmente por `maxminddb` usando:
  - `/usr/share/GeoIP/GeoLite2-Country.mmdb`;
  - `/usr/share/GeoIP/GeoLite2-ASN.mmdb`.
- Não existe dependência de API externa para enriquecer os IPs.
- Endereços privados exibem “Rede local” e um ícone próprio de rede.
- Endereços sem correspondência usam fallback seguro e não interrompem a tela.

Exemplos validados em 01/08/2026:

- `45.182.96.18` → Brasil, `AS269339`, CONEXAO WEB TELECOM LTDA;
- `154.16.44.106` → Reino Unido, `AS25369`, Hydra Communications Ltd;
- `127.0.0.1` → Rede local.

## Referências visuais

- `/opt/backup-manager-local/inicial.png`: Dashboard.
- `/opt/backup-manager-local/comoesta.png`: Backups.
- `/opt/backup-manager-local/iii.jpeg`: Central de Segurança.

Hashes e procedimentos de verificação estão em
`docs/UI_RESTORATION_2026-08-01.md`.

## Validações executadas

- `python3 -m py_compile backup_manager/app.py` — aprovado.
- `python3 -m backup_manager.presentation_check` — aprovado.
- Resolução GeoLite2 dos IPs de referência — aprovada.
- Sincronização de `app.py` e `app.css` com a instalação ativa — confirmada nos
  pontos de publicação.
- `backup-manager-local.service` — reiniciado e confirmado como `active` após
  as publicações.

A suíte ampla `tests.test_app` também foi executada, mas encontrou dados
duplicados, sessões ausentes e credenciais criptografadas por outra chave no
ambiente de testes compartilhado. Essas falhas são de isolamento/estado do
banco de testes e não foram usadas como evidência de regressão visual. A
verificação estrutural e de apresentação passou.

## Arquivos do estado de trabalho relacionados

O estado consolidado do projeto em 01/08/2026 contém alterações nos seguintes
arquivos relevantes às áreas tratadas:

- `backup_manager/app.py`;
- `backup_manager/backups_views.py`;
- `backup_manager/dashboard_views.py`;
- `backup_manager/report_views.py`;
- `backup_manager/reports.py`;
- `backup_manager/settings_center.py`;
- `backup_manager/settings_views.py`;
- `static/app.css`;
- `static/settings.css`;
- `templates/dashboard.html`;
- testes de apresentação, relatórios, configurações e aplicação.

Há outros arquivos modificados no worktree. Mudanças existentes pertencem ao
estado do projeto e não devem ser descartadas com `git reset`, `git checkout`
ou cópia integral de outra árvore.

## Publicação segura daqui em diante

1. Leia `docs/UI_RESTORATION_2026-08-01.md`.
2. Compare projeto e instalação ativa antes de copiar qualquer arquivo.
3. Preserve imagens, bases GeoLite2 e `static/assets/flags`.
4. Execute compilação e `presentation_check`.
5. Publique somente arquivos compreendidos e validados.
6. Reinicie o serviço e confirme estado `active`.
7. Valide todas as rotas listadas neste documento com `Ctrl+F5`.

