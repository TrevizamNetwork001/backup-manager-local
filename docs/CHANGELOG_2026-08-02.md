# Modernização de Retenção e Auditoria — 02/08/2026

Este registro complementa a restauração visual de 01/08/2026 e documenta os
ajustes publicados nas rotas `/lifecycle` e `/audit`.

## Fila e histórico do backup via Telegram

- Modernização das telas de fila e histórico com métricas, busca e filtro por situação.
- Separação clara entre envios em andamento e registros já encerrados.
- Tradução dos estados e dos erros operacionais conhecidos.
- Ações de repetir e cancelar condicionadas ao estado do item.
- Layout responsivo em formato de cartões para tablet e celular.
- Correção da proteção do login após oito falhas em quinze minutos, com resposta HTTP 429 e auditoria do bloqueio.

## Retenção e limpeza

- A página `/lifecycle` foi alinhada ao padrão administrativo atual.
- O cabeçalho passou a informar claramente o estado da política global.
- Indicadores de backups, armazenamento, lixeira e espaço livre foram
  reorganizados em cartões proporcionais ao restante do painel.
- O formulário da política global ganhou unidades, ícones e textos auxiliares
  sem alterar nomes de campos ou regras de validação.
- A última execução deixou de exibir estados técnicos em inglês e passou a
  apresentar modo, estado, data, candidatos, espaço estimado, itens movidos e
  itens expurgados.
- Simulação, integridade e limpeza foram organizadas por nível de risco.
- Políticas por equipamento, relatório recente e lixeira restaurável receberam
  cabeçalhos, contadores, estados e rolagem responsiva.
- Confirmações exigidas para limpeza e exclusão definitiva foram preservadas.

Arquivos principais: `backup_manager/lifecycle_views.py` e `static/app.css`.

## Central de Segurança e Auditoria

- A escala tipográfica de `/audit` foi aumentada para acompanhar as demais
  telas do sistema.
- Títulos, indicadores, rankings e tabelas passaram a usar tamanhos legíveis e
  consistentes.
- O painel de autenticação mostra as 20 ocorrências mais recentes e oferece
  acesso direto ao histórico completo em `/reports/audit`.
- O cabeçalho do painel foi tornado responsivo para não comprimir o título nem
  o botão quando houver pouco espaço ou zoom do navegador.
- Data, evento, conta, endereço IP e ASN/operadora são exibidos diretamente nas
  células, sem caixas ou aparência de modal.
- Login válido e tentativa inválida continuam diferenciados por cor e ícone.
- País, bandeira, ASN e operadora continuam resolvidos localmente pelas bases
  GeoLite2, sem consulta a API externa.

Arquivos principais: `backup_manager/app.py`, `backup_manager/geoip.py`,
`static/app.css` e `static/assets/flags/`.

## Artefatos e referências preservados

- `inicial.png`: referência do Dashboard.
- `comoesta.png`: referência da página de Backups.
- `iii.jpeg`: referência da Central de Segurança.
- `GeoLite2-ASN_20260801*` e `GeoLite2-Country_20260731*`: pacotes e bases
  locais utilizados no enriquecimento de endereços IP.

## Validação

- Compilação de `backup_manager/app.py` e
  `backup_manager/lifecycle_views.py`: aprovada.
- Testes dos fluxos de Lifecycle, lixeira, IP do proxy e tentativas de login
  distribuídas: aprovados.
- Verificação de whitespace com `git diff --check`: aprovada.
- `backup-manager-local.service`: reiniciado e confirmado como ativo após a
  publicação.

## Relatórios operacionais

- A tipografia das páginas internas foi alinhada à escala da Visão Geral.
- A navegação entre Visão Geral, Backups, Equipamentos, Armazenamento, FTP,
  Telegram, Auditoria e Exportações foi mantida em uma única linha, com
  rolagem horizontal somente em telas estreitas.
- Os relatórios de Backups, Equipamentos, Armazenamento, FTP e Telegram
  receberam painéis, indicadores, estados vazios e tabelas no padrão visual
  atual.
- O relatório de Armazenamento passou a apresentar gráfico circular, barra de
  ocupação e comparação entre capacidade utilizada, livre e total.
- O bloco agregado “Ocorrências / Falhas registradas” foi removido do relatório
  de Equipamentos por solicitação; as falhas individuais continuam disponíveis
  na situação de cada equipamento.
- A ação “Ver detalhes” do relatório de Backups passou a explicar grupo, POP,
  destino, responsável e integridade SHA-256 em uma única coluna, com campos
  visualmente separados.

Arquivos principais: `backup_manager/app.py` e `static/app.css`.

## Cabeçalho global e Notificações

- O componente compartilhado do cabeçalho recebeu uma estrutura explícita
  para nome e metadados do usuário.
- A altura do topo foi fixada em 48 px e as áreas de ambiente, notificações,
  usuário e avatar passaram a permanecer no mesmo fluxo flexível, sem quebra.
- O texto do usuário deixou de ser ocultado em celulares; os espaçamentos e a
  tipografia são reduzidos de forma progressiva nas telas estreitas.
- O workspace passou a manter cabeçalho e conteúdo em fluxo vertical estável,
  fazendo todas as páginas começarem logo abaixo do topo.
- A página completa `/settings/notifications` foi modernizada, incluindo
  estado do canal, indicadores, integração Telegram, resumos, prévias, editor
  e histórico.

Arquivos principais: `templates/components/topbar.html`, `static/app.css`,
`backup_manager/notifications_page_views.py` e `tests/test_presentation.py`.

## Menu da conta e avatar

- O avatar do cabeçalho passou a abrir um menu acessível, sem alterar a altura
  fixa do topo ou deslocar o conteúdo.
- A ação **Alterar senha** reutiliza `/change-password`; o texto agora distingue
  primeiro acesso de alteração voluntária de uma conta já configurada.
- A escolha do avatar oferece a inicial do usuário e cinco SVGs internos:
  usuário, escudo, servidor, rede e ajustes.
- A preferência é individual, persistida em
  `settings.profile_avatar_icon_<user_id>` e também refletida na sidebar.
- A atualização usa `POST /profile/avatar`, exige sessão e CSRF, aceita somente
  a lista fechada de ícones e registra `profile.avatar_updated` na auditoria.
- O menu fecha por clique externo e `Esc`; em celular, permanece abaixo do
  cabeçalho e respeita a largura da tela.
- Não foi adicionado upload de imagem, dependência externa ou migração de banco.

Descrição técnica e operacional completa:
[`ACCOUNT_MENU_AND_AVATAR.md`](ACCOUNT_MENU_AND_AVATAR.md).

Arquivos principais: `backup_manager/app.py`,
`templates/components/topbar.html`, `templates/components/sidebar.html`,
`static/app.css`, `static/app.js`, `tests/test_presentation.py` e
`tests/test_settings.py`.

## Painel da integração Telegram

- O resumo de `/settings/notifications` deixou de repetir o estado “Não
  verificado” nos campos Bot e Destino.
- O estado da integração passou a aparecer uma única vez no cabeçalho do painel.
- Bot e destino agora usam cartões com SVG e, antes do diagnóstico, explicam que
  serão identificados após o teste.
- Teste mais recente, último envio bem-sucedido e última falha foram separados
  em **Atividade recente**, com ícones e cores semânticas.
- Os indicadores superiores ficaram restritos à operação da fila: pendentes,
  novas tentativas, falhas em 24 horas e último envio.
- Os botões **Testar envio** e **Editar configuração** receberam SVGs e mantêm
  as rotas, CSRF e comportamentos existentes.
- Em notebook e tablet, configuração e atividade passam para uma coluna; em
  celular, horários e ações também ficam empilhados.

Arquivos principais: `backup_manager/notifications_page_views.py`,
`static/app.css` e `tests/test_notifications.py`.

## Mapeamento de tópicos do Telegram

- `/telegram-backup?view=topics` foi alinhada ao padrão visual atual.
- O cabeçalho e as seis abas da central receberam SVG, estado ativo e rolagem
  segura em telas estreitas.
- A página mostra contadores de regras, regras ativas e destinos cadastrados.
- O formulário explica a diferença entre grupo de equipamentos e grupo do
  Telegram.
- Campos de equipamento, grupo ou valor textual aparecem conforme o tipo de
  regra escolhido; campos incompatíveis ficam desabilitados.
- O identificador técnico foi apresentado como ID do tópico, mantendo
  `message_thread_id` apenas como referência auxiliar.
- A listagem traduz os tipos, destaca tópico e estado e vira blocos rotulados em
  celulares.
- O estado vazio orienta a criação do primeiro mapeamento.

Arquivos principais: `backup_manager/telegram_backup_views.py`,
`static/app.css`, `static/app.js`, `tests/test_presentation.py` e
`docs/TELEGRAM_ADVANCED.md`.

## Ícone do resumo por ambiente

- Corrigido o SVG sem limite visual no bloco **Resumo por ambiente** da
  Dashboard principal.
- O seletor anterior estilizava apenas cabeçalhos de elementos `article`, mas o
  resumo usa um elemento `section`.
- O ícone recebeu contêiner circular de 27 px, SVG de 15 px e traço verde no
  mesmo padrão dos demais painéis.
- Adicionado teste para preservar tamanho e seletor específicos.

Arquivos principais: `static/app.css`, `templates/dashboard.html`,
`tests/test_presentation.py` e `docs/CHANGELOG_2026-08-02.md`.

## Ícone de segurança de acesso

- Corrigido o mesmo vazamento de tamanho do SVG no painel **Segurança de
  acesso**, que também usa um elemento `section`.
- O escudo agora fica limitado a 15 px dentro de um círculo de 27 px.
- Cor e fundo seguem o estado semântico atual do painel.
- Adicionado teste estrutural específico para o ícone de segurança.

Arquivos principais: `static/app.css`, `templates/dashboard.html`,
`tests/test_presentation.py` e `docs/CHANGELOG_2026-08-02.md`.

## Correção das abas da prévia

- Corrigida a regra visual que sobrescrevia o atributo `hidden` dos exemplos.
- Diário, Semanal e Operacional agora exibem somente o balão correspondente à
  aba selecionada.
- Adicionado teste de regressão para o estado oculto das mensagens.

Arquivos principais: `static/app.css`, `tests/test_notifications.py` e
`docs/CHANGELOG_2026-08-02.md`.

## Configuração, destinos e políticas da cópia Telegram

- A aba Configuração recebeu chave de estado, destino principal e limites com
  unidades explícitas para MiB, tentativas e segundos.
- A ausência de destino elegível agora gera orientação e bloqueia o salvamento.
- A aba Destinos organiza identidade da conversa, Chat ID, tópico padrão e
  finalidades em cartões selecionáveis com SVG.
- A listagem de destinos resume uso, estado e teste de arquivo sem alterar a
  proteção do Chat ID para perfis limitados.
- A aba Políticas passou a exibir campos de grupo ou equipamento conforme o
  escopo selecionado.
- Tipos de arquivo e nível de compactação aparecem somente quando aplicáveis.
- As tabelas traduzem estados e viram blocos rotulados em celulares.
- Estados vazios orientam o cadastro inicial nas duas áreas.

Arquivos principais: `backup_manager/telegram_backup_views.py`,
`static/app.css`, `static/app.js`, `tests/test_presentation.py` e
`docs/TELEGRAM_ADVANCED.md`.

## Editor da configuração Telegram

- O formulário expansível foi dividido em Estado do canal, Bot e destino,
  Regras de entrega e Agenda dos resumos.
- Campos receberam títulos, textos auxiliares e SVGs proporcionais, sem alterar
  nomes, valores ou rotas do formulário.
- O token continua protegido: a tela informa apenas se está configurado e o
  campo vazio preserva o segredo existente.
- O cooldown passou a exibir a unidade “minutos”, e o timezone foi apresentado
  como fuso horário usado também nos resumos e na manutenção.
- Diário, semanal e executivo receberam chaves visuais e controles agrupados.
- O rodapé oferece **Cancelar** e **Salvar alterações**; o cancelamento recolhe
  o editor sem submissão.
- As configurações avançadas permanecem recolhidas e separadas do fluxo comum.
- Em celulares, seções, chaves e ações passam para uma coluna.

Arquivos principais: `backup_manager/notifications_page_views.py`,
`static/app.css`, `tests/test_notifications.py` e `docs/NOTIFICATIONS.md`.

## Visibilidade do token durante a edição

- O campo Token do bot recebeu um controle SVG para mostrar ou ocultar o valor
  novo que está sendo digitado.
- O controle atualiza o rótulo acessível entre **Mostrar token** e **Ocultar
  token**.
- O token anteriormente salvo não é devolvido ao navegador; permanece
  criptografado e o campo continua vazio até o administrador informar um novo
  valor.

Arquivos principais: `backup_manager/notifications_page_views.py`,
`static/app.css`, `static/app.js`, `tests/test_notifications.py` e
`tests/test_app.py`.

## Configurações avançadas do Telegram

- A seção foi dividida em Janela de manutenção, Roteamento das mensagens e
  Diagnóstico do canal.
- A manutenção recebeu chave visual, estado e horários agrupados; os valores são
  preservados quando a função está desativada.
- Tópicos e destinos avançados passaram a usar atalhos descritivos com SVG.
- Permissão para enviar, última verificação e resultado do último teste agora
  são apresentados como informações distintas.
- O antigo rótulo “Diagnóstico: Enviado” foi substituído por “Resultado do
  último teste: Enviado”, eliminando a ambiguidade.
- Os estados de permissão são Envio permitido, Envio bloqueado ou Aguardando
  verificação.
- O layout passa para uma coluna em celulares.

Arquivos principais: `backup_manager/notifications_page_views.py`,
`static/app.css`, `static/app.js`, `tests/test_notifications.py` e
`docs/NOTIFICATIONS.md`.

## Prévia visual das mensagens

- O diálogo de prévia passou a simular uma conversa do Telegram, com cabeçalho
  do bot e mensagem em formato de balão.
- Diário, Semanal e Operacional usam abas com SVG e exemplos próprios.
- Um aviso informa que o conteúdo é fictício e que nenhuma mensagem será
  enviada ao alternar a prévia.
- A data antiga fixa foi removida para não sugerir que o exemplo corresponde a
  uma entrega real.
- Dados operacionais, identificadores e nomes reais continuam fora da prévia.
- O diálogo limita altura, permite rolagem e se adapta à largura de celulares.

Arquivos principais: `backup_manager/notifications_page_views.py`,
`static/app.css`, `tests/test_notifications.py` e `docs/NOTIFICATIONS.md`.

## Histórico de notificações

- O histórico deixou de abrir como uma lista extensa de códigos técnicos.
- A tela mostra inicialmente oito itens por grupo e oferece **Mostrar mais** ou
  **Mostrar menos**, sem apagar registros do banco.
- Foram adicionados filtros para todos, enviados e ocorrências com falha.
- O cabeçalho apresenta contadores de envios e falhas dentro da janela recente.
- Eventos conhecidos e falhas de entrega passaram a usar descrições em
  português; códigos internos como `ftp_received_file` e
  `TELEGRAM_DELIVERY_FAILED` não são mais a informação principal da interface.
- Notificações e resumos possuem grupos separados, ícones SVG, data, descrição,
  tentativa e estado.
- A consulta da página foi limitada aos 50 eventos e 30 resumos mais recentes
  para manter o carregamento rápido. O histórico persistido não é removido.
- O layout adapta metadados e controles para telas estreitas.

Arquivos principais: `backup_manager/notifications.py`,
`backup_manager/notifications_page_views.py`, `static/app.css`, `static/app.js`
e `tests/test_notifications.py`.

## Resumos automáticos do Telegram

- Diário, semanal e executivo passaram a ser exibidos como cartões individuais
  com SVG, estado, descrição, período, programação e próximo envio.
- Estados ativos e desativados receberam indicadores semânticos sem depender
  apenas do texto.
- **Editar** e **Enviar teste** permanecem no cartão correspondente, facilitando
  a associação entre a ação e o tipo de resumo.
- Os botões duplicados de teste foram removidos do painel de prévia. O painel
  agora possui uma única responsabilidade: abrir a prévia ilustrativa das
  mensagens.
- Em resolução de notebook os três cartões usam colunas proporcionais; abaixo
  de 1050 px são empilhados para preservar a leitura.

Arquivos principais: `backup_manager/notifications_page_views.py`,
`static/app.css` e `tests/test_notifications.py`.
