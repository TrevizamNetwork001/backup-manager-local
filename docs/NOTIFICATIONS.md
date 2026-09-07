# Alertas e relatórios operacionais

## Arquitetura

O Notification Manager é separado dos produtores de eventos e do transporte:

```text
audit_log / snapshot NOC -> Notification Manager -> fila SQLite -> Worker -> Telegram
```

SSH, FTP, Lifecycle e Observabilidade não chamam Telegram. Eventos já existentes
em `audit_log` são consumidos incrementalmente por cursor. Condições atuais são
avaliadas a partir do snapshot somente leitura do Centro de Operações.

A arquitetura expõe um protocolo de transporte e começa com
`TelegramTransport`; outros canais podem ser adicionados sem alterar os
produtores ou o modelo de fila.

## Configuração

Acesse **Configurações → Notificações** (`/settings/notifications`) como Admin.
A tela permite:

- habilitar ou desabilitar o canal;
- informar o token do Telegram Bot e Chat ID;
- definir cooldown;
- configurar timezone e janela de manutenção;
- habilitar e agendar resumos diário e semanal;
- enfileirar uma notificação de teste;
- consultar status, pendências, falhas e histórico de tentativas.

Ao editar, deixe o token vazio para manter o segredo atual. A aplicação exibe
somente “configurado” ou “não configurado”; o token nunca volta ao HTML.

## Interface administrativa

A página `/settings/notifications` usa uma visão operacional única e não faz
chamadas à Bot API durante o carregamento. O estado exibido é derivado dos
diagnósticos e tentativas já persistidos.

### Indicadores e integração Telegram

A faixa superior apresenta somente informações da fila:

- itens pendentes;
- itens aguardando nova tentativa;
- falhas nas últimas 24 horas;
- horário do último envio.

O estado da integração aparece uma única vez no cabeçalho do painel Telegram.
Antes do primeiro diagnóstico, Bot e Destino exibem **Identificado após o
teste**, evitando repetir “Não verificado” em vários campos.

O painel é dividido em:

- **Configuração atual:** bot e destino principal;
- **Atividade recente:** último teste, último envio bem-sucedido e última falha;
- **Ações:** testar envio e editar configuração.

Os itens usam SVGs internos e cores semânticas. Os ícones não dependem de fonte,
biblioteca ou serviço externo. Em notebooks e tablets, os blocos passam para
uma coluna quando necessário; em celulares, horários e ações também são
empilhados.

### Editor da configuração

O editor expansível é organizado em quatro blocos:

1. **Estado do canal:** chave para habilitar ou desabilitar novas mensagens;
2. **Bot e destino:** token preservado e Chat ID;
3. **Regras de entrega:** intervalo entre alertas semelhantes e fuso horário;
4. **Agenda dos resumos:** diário, semanal e executivo.

O token existente é indicado somente como **configurado**. Deixar o campo vazio
mantém o segredo atual, e o valor persistido nunca retorna ao HTML. O intervalo
mostra explicitamente a unidade em minutos. O fuso horário informa que também é
utilizado nos resumos e na janela de manutenção.

O campo do token possui um botão **Mostrar token / Ocultar token**. Ele revela
somente o novo valor presente no campo durante a edição. O token já persistido
continua criptografado, não é descriptografado para a página e não aparece no
HTML, logs ou auditoria.

Cada resumo possui uma chave visual e seus controles de horário; o semanal
também apresenta o dia da semana. **Cancelar** recolhe o editor sem enviar o
formulário, enquanto **Salvar alterações** mantém o `POST` e a proteção CSRF já
existentes. As configurações avançadas permanecem recolhidas e separadas do
fluxo principal.

### Configurações avançadas

Ao expandir **Configurações avançadas**, a página apresenta três áreas:

- **Janela de manutenção:** chave de ativação, início, fim e indicação do estado;
- **Roteamento das mensagens:** atalhos para tópicos e destinos avançados;
- **Diagnóstico do canal:** permissão para enviar, última verificação e resultado
  do último teste.

Os horários de manutenção são preservados mesmo quando a janela está
desativada. Alertas críticos continuam sendo processados durante a janela. A
permissão usa os estados **Envio permitido**, **Envio bloqueado** ou
**Aguardando verificação**.

O antigo campo genérico “Diagnóstico” foi renomeado para **Resultado do último
teste**, evitando interpretar o estado “Enviado” como diagnóstico de permissão.
Em telas estreitas, manutenção, roteamento e diagnóstico são empilhados.

### Resumos automáticos

Diário, semanal e executivo são apresentados em cartões separados. Cada cartão
informa:

- estado ativo ou desativado;
- finalidade e período considerado;
- dia e horário programados;
- próximo envio calculado;
- ações **Editar** e **Enviar teste**.

O editor continua centralizado na configuração Telegram. Ao clicar em
**Editar**, a seção é aberta e o foco segue para o resumo correspondente. O
teste continua assíncrono e protegido por CSRF.

O bloco **Prévia das mensagens** serve apenas para visualizar exemplos
ilustrativos. Os antigos botões duplicados “Testar diário”, “Testar semanal” e
“Testar executivo” foram removidos desse bloco; cada teste permanece no cartão
correto.

A prévia abre em um diálogo com aparência de conversa do Telegram. Ela contém
cabeçalho do bot, abas com SVG para Diário, Semanal e Operacional, aviso de
conteúdo fictício e uma mensagem em formato de balão. Datas e identificadores
reais não são usados; a data fixa antiga foi substituída por “período anterior”.
Alternar as abas modifica somente o exemplo local e não enfileira mensagens.

Em larguras maiores, os três cartões ficam lado a lado. Abaixo de 1050 px, são
empilhados para evitar texto comprimido e ações desalinhadas.

### Histórico recente

O histórico é fechado inicialmente e separado em **Notificações** e
**Resumos**. Ao abrir, cada grupo mostra oito registros. Os controles **Mostrar
mais** e **Mostrar menos** expandem ou recolhem os itens já carregados.

Os filtros disponíveis são:

- Todos;
- Enviados;
- Com falha.

O cabeçalho mostra totais de enviados e falhas dentro da janela carregada. Cada
registro apresenta SVG de estado, data, título, descrição, tentativa e resultado.
Eventos conhecidos são traduzidos para português. Por exemplo:

| Código interno | Texto apresentado |
| --- | --- |
| `ftp_received_file` | Backup recebido por FTP |
| `backup_completed` | Backup SSH concluído |
| `backup_late` | Backup atrasado |
| `backup_late_recovery` | Backup normalizado |
| `telegram.basic_test` | Teste do Telegram |
| `TELEGRAM_DELIVERY_FAILED` | Falha na entrega pelo Telegram |

A página consulta os 50 eventos e 30 resumos mais recentes. Esse limite reduz o
HTML e o tempo de renderização; não apaga nem altera o histórico armazenado no
banco. A retenção dos dados continua sendo responsabilidade das políticas e
rotinas próprias do sistema.

Os filtros e a expansão são aplicados no navegador sobre essa janela recente.
Não são paginação global nem pesquisa sobre todo o banco.

## Segurança e entrega assíncrona

O token é criptografado com Fernet usando a mesma chave local protegida das
credenciais SSH. Ele não é incluído em auditoria, histórico, erros sanitizados
ou respostas web.

Salvar e testar pela interface não faz chamadas de rede. O teste cria um item
na fila, e `backup-manager-worker.service` realiza a entrega posteriormente.
O transporte usa a biblioteca padrão, requisição POST e timeout de 10 segundos,
sem `shell`, dependência externa ou processo adicional.

## Eventos

São suportados:

- backup manual, SSH ou FTP concluído;
- job, backup SSH ou importação FTP com falha;
- backup atrasado há mais de 24 horas;
- equipamento ativo sem backup;
- Worker, Scheduler, FTP importer ou Pure-FTPd offline;
- uso de storage em 80%, 90% e 95%;
- limpeza/retenção executada;
- restauração da lixeira;
- rejeição FTP;
- recuperação das condições anteriores.

Eventos pontuais usam o ID imutável da auditoria para idempotência. Condições
usam uma chave estável, como `service:worker` ou `equipment:7:late`.

## Cooldown e recuperação

Na primeira detecção de uma condição, uma notificação é enfileirada. Enquanto a
condição persistir, um novo envio só é permitido depois do cooldown configurado.
Quando a condição deixa de existir, é enfileirada uma recuperação e o estado é
reiniciado; uma falha futura pode alertar imediatamente.

Itens suprimidos por canal desabilitado ou manutenção ficam registrados no
histórico. Durante manutenção, a condição continua sendo observada e poderá ser
enviada ao término da janela se ainda estiver ativa.

## Retry

Falhas de entrega não expõem a resposta bruta do Telegram. A fila usa até cinco
tentativas com backoff exponencial:

```text
1 min, 2 min, 4 min, 8 min
```

O atraso é limitado a uma hora. Cada tentativa registra apenas status, código
HTTP quando bem-sucedida e código de erro sanitizado. Após a última tentativa,
o item fica `failed` para inspeção administrativa.

## Resumos

O resumo diário inclui as últimas 24 horas; o semanal, os últimos sete dias.
Ambos mostram backups recebidos, falhas, rejeitados FTP e itens na lixeira. A
chave de deduplicação por período/data impede mais de um resumo equivalente.

O dia semanal segue `0=segunda` até `6=domingo`, no timezone configurado.

O resumo executivo é enviado somente quando houver atividade administrativa
relevante, conforme a configuração persistida em `telegram_summary_settings`.

## Auditoria e histórico

São auditadas alterações de configuração, testes enfileirados e lotes de
entrega. A fila mantém evento, severidade, assunto, origem, tentativas e estado;
`notification_history` registra cada envio, retry, falha ou supressão.

O cursor é posicionado no evento mais recente quando o canal é habilitado pela
primeira vez, evitando uma avalanche de notificações históricas.

Na interface, códigos de erro conhecidos são convertidos em descrições seguras.
Códigos desconhecidos são apresentados como **Falha no processamento**, sem
expor respostas brutas do Telegram.

## Arquivos da implementação visual

- `backup_manager/notifications.py`: consultas, contexto da página, fila e
  histórico recente;
- `backup_manager/notifications_page_views.py`: estrutura dos painéis, cartões,
  traduções e SVGs;
- `static/app.css`: estados visuais e responsividade;
- `static/app.js`: filtros, expansão do histórico, prévias e atalhos do editor;
- `tests/test_notifications.py`: contrato estrutural, segurança e apresentação;
- `tests/test_app.py`: fluxo funcional, CSRF, permissões e diagnóstico.

## Validação da interface

Depois de alterar a página, execute:

```bash
python3 -m py_compile backup_manager/notifications.py backup_manager/notifications_page_views.py
python3 -m backup_manager.presentation_check
python3 -m unittest tests.test_notifications.NotificationArchitectureTests.test_notification_ui_is_collapsed_translated_and_responsive
python3 -m unittest tests.test_app.BackupManagerAppTest.test_notification_settings_hide_token_and_test_is_asynchronous tests.test_app.BackupManagerAppTest.test_notification_settings_enforce_csrf_and_admin_rbac
git diff --check
```

Como `notifications.py` e `notifications_page_views.py` são módulos Python, a
publicação exige reinício controlado de `backup-manager-local.service`. Confirme
o estado `active` depois do reinício.

## Operação

```bash
systemctl status backup-manager-worker.timer
journalctl -u backup-manager-worker.service --since today
python3 -m backup_manager.cli jobs-run-once
```

O comando de jobs não força chamadas Telegram; a entrega normal pertence à
execução de `backup_manager.worker`. Para testar, use a interface e aguarde a
próxima rodada do timer.

## Diagnóstico seguro da Bot API

`python3 -m backup_manager.cli telegram-diagnose` executa somente `getMe`,
`getChat` e `getChatMember`. A saída contém apenas ID/username do bot, nome/tipo
do destino, vínculo e permissão. O envio só ocorre com a flag explícita
`--send-test`. Erros armazenam apenas código HTTP/API, descrição sanitizada,
`retry_after` e `migrate_to_chat_id`; token, URL autenticada, headers e corpo
bruto nunca são registrados.

Erros comuns: 401 indica token inválido/revogado; `chat not found` pede revisão
do Chat ID e participação do bot; `kicked` indica remoção; `not enough rights`
indica falta de permissão; 429 agenda nova tentativa. Se houver
`migrate_to_chat_id`, confirme o novo ID antes de alterar a configuração.

Alertas imediatos usam título, ícone de severidade e texto operacional escapado
em HTML. Status técnicos são traduzidos na interface. A prévia do resumo diário
é fictícia e não consulta nomes ou dados operacionais reais.
# Telegram v1.1

Alertas e resumos são mensagens e continuam separados da fila de arquivos. A v1.1 adiciona destinos independentes, tópicos, resumo diário, semanal e executivo, idempotência por período e divisão numerada de mensagens longas. Consulte [TELEGRAM_ADVANCED.md](TELEGRAM_ADVANCED.md).
# Teste e diagnóstico do Telegram

“Enviar mensagem de teste” apenas grava uma linha com estado “Aguardando envio”;
a requisição web não acessa a rede. O worker realiza a entrega em segundo plano e
registra “Enviado”, “Falhou” ou “Aguardando nova tentativa”. Se aparecer
`missing destination_id` ou `missing thread_id`, execute o deploy de atualização
e confirme `SCHEMA_OK`. Preserve o token e o Chat ID durante esse procedimento.
