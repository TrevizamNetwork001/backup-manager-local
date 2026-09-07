# Telegram avançado

O Telegram possui duas funções independentes no Backup Manager Local:

- **Notificações Telegram**: alertas, recuperações e resumos em texto.
- **Cópia de backups no Telegram**: documentos anexados por uma fila própria. Essa cópia nunca substitui o storage local.

## Fila e histórico

As telas **Fila** e **Histórico** usam a mesma apresentação operacional e separam os registros pelo estado real do envio:

- a fila exibe itens pendentes, em preparação, em envio ou aguardando nova tentativa;
- o histórico concentra itens enviados, ignorados, cancelados ou com falha;
- busca textual e filtro por situação atuam imediatamente no navegador;
- estados e códigos de erro conhecidos são apresentados em português;
- repetir e cancelar aparecem somente quando a situação permite a ação;
- em telas pequenas, as colunas são reorganizadas como cartões legíveis, sem rolagem horizontal obrigatória.

As duas consultas são limitadas aos 200 registros mais recentes para manter a tela responsiva.

## Bot, chat e permissões

Crie o bot pelo BotFather, guarde o token como segredo e informe-o uma única vez em **Configurações → Notificações**. O token é criptografado, nunca é reexibido e não pertence aos destinos.

Adicione o bot ao chat desejado. Em grupos e supergrupos, o Chat ID normalmente é negativo. Em canais, conceda somente a permissão de publicar mensagens. Para tópicos, habilite o modo fórum e obtenha o `message_thread_id` de cada tópico. O bot precisa poder enviar mensagens e documentos; ele não precisa administrar usuários ou apagar mensagens.

Cadastre destinos em **Backup externo → Telegram → Destinos**. Um destino pode ser privado, grupo, supergrupo ou canal e pode ter um tópico padrão. O Chat ID é mascarado para usuários sem permissão administrativa.

## Resumos automáticos

O timezone vem da configuração geral da instalação. Não há editor de template.

- Diário: dia civil anterior completo, de 00:00:00 a 23:59:59.
- Semanal: sete dias civis completos anteriores.
- Executivo: atividade administrativa relevante do dia anterior; sem atividade, nada é enviado.

Cada execução usa uma chave lógica única por tipo, período e destino. Reinícios não recriam um resumo já registrado. Mensagens acima do limite são divididas em partes numeradas, preservando ordem e conteúdo.

Eventos de leitura, navegação e login bem-sucedido não entram no resumo executivo. Usuários, equipamentos, agendamentos, credenciais, contas FTP, retenção, notificações, HTTPS e atualizações são considerados quando alterados.

## Destinos e tópicos

As opções `use_for_alerts`, `use_for_daily_summary`, `use_for_weekly_summary` e `use_for_executive_summary` permitem destinos distintos. A resolução de tópicos para arquivos segue:

1. equipamento;
2. grupo do equipamento;
3. método ou categoria;
4. tópico padrão;
5. chat sem tópico.

### Tela de mapeamento de tópicos

Acesse `/telegram-backup?view=topics` como administrador. A página apresenta
indicadores de regras, regras ativas e destinos, além do formulário e da
listagem dos mapeamentos existentes.

Para criar uma regra:

1. escolha o destino Telegram;
2. selecione se a regra vale para equipamento, grupo de equipamentos, método,
   categoria ou evento;
3. informe somente o alvo correspondente ao tipo escolhido;
4. informe o `message_thread_id` fornecido pelo Telegram;
5. salve o mapeamento.

O formulário oculta e desabilita campos que não pertencem ao tipo selecionado,
evitando enviar simultaneamente equipamento, grupo e valor textual. “Grupo de
equipamentos” é uma organização interna do Backup Manager; não significa grupo
ou supergrupo do Telegram.

A tabela traduz os tipos técnicos, mostra o tópico como `#<id>` e separa
destino, regra, alvo e estado. Em celulares, cada linha passa a ser um bloco
rotulado. O estado vazio orienta a criação da primeira regra.

### Configuração da cópia de arquivos

Em `/telegram-backup?view=settings`, a chave **Habilitar envio** controla a fila
de cópia secundária. O formulário também define destino padrão, tamanho máximo
em MiB, total de tentativas e espera inicial em segundos. A espera aumenta
progressivamente nas falhas seguintes.

Não é possível salvar a integração sem destino elegível. A tela orienta o
cadastro e mantém acesso separado à configuração do bot e token. Políticas por
equipamento ou grupo têm prioridade sobre o destino padrão.

### Destinos

Em `/telegram-backup?view=destinations`, cada destino possui nome interno, tipo
de conversa, Chat ID e tópico padrão opcional. As finalidades são escolhidas em
cartões independentes:

- alertas;
- resumo diário;
- resumo semanal;
- resumo executivo;
- arquivos de backup.

O token não é cadastrado no destino; permanece na configuração protegida de
Notificações. A listagem mostra finalidades, estado e ação de arquivo de teste.
Usuários sem permissão administrativa continuam recebendo Chat ID mascarado.

### Políticas de envio

Em `/telegram-backup?view=policies`, o escopo pode ser global, grupo de
equipamentos ou equipamento específico. O formulário exibe apenas o seletor
compatível com o escopo escolhido.

A política pode usar tópico específico, todos os artefatos ou extensões
selecionadas, envio automático e compactação ZIP/GZIP. O nível de compactação
só aparece quando aplicável. A cópia nunca substitui o backup local.

## Operação e troubleshooting

```bash
python3 -m backup_manager.cli telegram-destinations
python3 -m backup_manager.cli telegram-test-message
python3 -m backup_manager.cli telegram-summary-run --type daily
python3 -m backup_manager.cli telegram-summary-run --type weekly
python3 -m backup_manager.cli telegram-summary-run --type executive
python3 -m backup_manager.cli telegram-summary-stats
```

Erros 401 indicam token inválido; 403, permissão insuficiente; 404, chat/endpoint inexistente; 400 costuma indicar tópico ou parâmetros inválidos; 429 informa rate limit e o worker respeita `retry_after`. Timeouts, DNS, TLS, 409 e 5xx entram em retry exponencial. Tokens, cabeçalhos de autorização e mensagens integrais não são gravados nos logs técnicos.

## Teste controlado

Use um grupo sem dados sensíveis, habilite tópicos e crie `Relatórios`, `OLTs`, `Roteadores` e `Falhas`. Adicione o bot com permissões mínimas, teste texto, depois um arquivo fictício e só então um backup pequeno de laboratório. Simule indisponibilidade, confirme o retry e verifique que o arquivo local não foi alterado.
# Correção de upgrade da fila (1.1.0-rc1)

Instalações atualizadas da v1.0 devem aplicar a migration
`020_fix_notification_queue_destinations`. Ela acrescenta, sem recriar a fila,
`notification_queue.destination_id` e `notification_queue.thread_id`. O comando
`python3 -m backup_manager.cli schema-check` deve responder `SCHEMA_OK` antes de
iniciar os workers. Um tópico só é usado quando há `thread_id` configurado; o bot
precisa ter permissão para publicar no chat e no tópico escolhidos.

Para grupos e supergrupos, `telegram-diagnose` confirma se o bot é `member` ou
`administrator`. Em canais ele precisa ser administrador e ter permissão para
publicar. O diagnóstico não testa tópicos quando `thread_id` está vazio.

## Padrão visual dos resumos

Resumos usam HTML escapado, cabeçalho “📦 Backup Manager Local”, estado geral no
topo, visão rápida e seções curtas para equipamentos, backups, FTP, cópias
externas, armazenamento, ocorrências e atividade administrativa. Valores e
nomes vindos do banco são escapados antes do envio. Mensagens extensas são
divididas por blocos e numeradas como “Parte 1 de 2”.

O estado é verde quando não há falha, equipamento ativo sem backup, pendência
externa ou storage em alerta; amarelo para uma falha isolada, equipamento sem
backup, rejeição/pêndencia ou storage a partir de 80%; vermelho para duas ou
mais falhas relevantes ou storage a partir de 90%.

Os botões de resumo de teste criam chaves `test:` exclusivas. Eles não ocupam a
chave do período real nem alteram sua idempotência.
