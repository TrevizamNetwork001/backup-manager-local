# Auditoria de consistência da interface — 23/08/2026

## Objetivo

Revisar as páginas e funções visuais da `v1.1.0-rc9`, localizar fluxos que
ainda usavam a apresentação genérica antiga e alinhá-los ao padrão atual sem
alterar regras operacionais.

## Referência adotada

Foram preservados o shell compartilhado, a navegação lateral, o topbar e os
padrões documentados em `UI_RESTORATION_2026-08-01.md` e
`UI_MODERNIZATION_2026-07-18.md`: cabeçalho operacional, eyebrow, cards `panel`,
badges semânticos, tabelas responsivas e ações com hierarquia clara.

## Fluxos migrados

### Uploads FTP

A rota `/ftp/uploads` ainda apresentava somente um cabeçalho e uma tabela
genérica. A tela passou a oferecer:

- cabeçalho com contexto e atalho para as contas FTP;
- indicadores de registros, importados e itens que requerem atenção;
- filtro por estado com rótulos em português;
- badges semânticos para processamento, sucesso, rejeição e falha;
- datas no timezone configurado, estado vazio e tabela responsiva.

### Roteiros manuais MikroTik e OLT

As respostas auxiliares usavam o contêiner genérico `narrow`. Agora usam um
layout operacional de duas colunas, com resumo/etapas e comandos separados,
aviso de segurança preservado, botão de cópia destacado e adaptação para telas
menores. Cabeçalhos, textos funcionais e proteção `no-store` foram mantidos.

### Resultado de troca de senha FTP

A confirmação de senha foi migrada do bloco genérico para um cartão de
credencial temporária coerente com o wizard FTP. A senha continua sendo exibida
uma única vez, com aviso explícito e retorno para a conta correspondente.

## Páginas preservadas

Dashboard, equipamentos, detalhes de equipamentos, backups, detalhes de backup,
agendamentos, execuções, retenção, configurações, notificações, relatórios,
auditoria, Telegram, rclone e atualizações já possuem wrappers e componentes
modernos específicos. Não foram reescritas para evitar regressão visual.

Respostas curtas de erro HTTP continuam usando o componente compartilhado
simples. Elas não são páginas operacionais antigas e permanecem dentro do shell
atual.

## Validação

- compilação de `backup_manager/app.py`;
- `backup_manager.presentation_check`;
- teste direcionado do contrato do roteiro MikroTik;
- suíte completa: 337 testes aprovados antes da publicação;
- `git diff --check`;
- readiness e estado do serviço após o deploy.

Ao repetir a suíte dentro de `deploy-ui.sh`, o teste concorrente
`test_artifacts_v2_two_importer_processes_are_idempotent` falhou uma vez por
timing do importador (`0` registros no instante da asserção). O deploy abortou
antes do restart, conforme projetado. O mesmo teste passou imediatamente quando
executado isoladamente; nenhuma alteração foi feita no importador. O serviço foi
então reiniciado de forma controlada e respondeu `READY` e HTTP 200 no login.

## Storage

A reconciliação foi executada na etapa posterior e está documentada em
`RECOVERY_AND_RECONCILIATION_2026-08-23.md`. A verificação final não encontrou
arquivos ausentes nem órfãos.

## Correção das abas de sincronização externa

As abas `/cloud?view=queue` e `/cloud?view=history` compartilhavam a mesma lista
base e exibiam registros duplicados entre as duas visões. A fila agora mostra
somente `queued`, `processing` e `retry_wait`. O histórico mostra somente
`synced`, `failed`, `skipped` e `cancelled`. Estados, títulos e vazios foram
traduzidos e as ações aparecem apenas quando ainda são aplicáveis.

## Segunda varredura

Uma nova busca em todas as rotas HTML e nos seletores CSS confirmou que o
contêiner legado `narrow` não é mais usado. Os componentes genéricos restantes
(`page-head` e `table-panel`) aparecem apenas dentro de páginas modernas e são
parte do design system atual.

Foram encontrados e modernizados dois pontos residuais:

- `/setup`: o formulário genérico recebeu página própria, cabeçalho contextual,
  cartões numerados, grade responsiva, resumo e barra de conclusão;
- `/jobs/<uuid>/edit`: o formulário já era moderno, mas passou a usar também o
  wrapper específico da página de edição.

Os estados HTTP curtos (CSRF inválido, recurso ausente e formato inválido) foram
mantidos no componente compartilhado do shell atual. Eles não carregam estrutura
ou estilos da interface antiga.
