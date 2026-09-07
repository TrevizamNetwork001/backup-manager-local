# Notificações operacionais do Telegram

## Objetivo

As mensagens operacionais destinadas ao usuário final apresentam somente o resultado da operação, equipamento, arquivo, quantidades, espaço liberado e horário. UUIDs, entidades, códigos e demais dados técnicos permanecem na fila, nos logs e na auditoria.

## Eventos e mensagens

- `backup.created_from_ftp`: “Backup recebido por FTP” ou “Backup recebido sem identificação”. A associação usa primeiro o backup persistido e seu `equipment_id`; nomes de arquivo não são usados para inferência ambígua.
- condição `equipment_without_backup`: “Equipamento sem backup”, com hostname e horário da verificação.
- condição `backup_late`: “Backup atrasado”, com hostname e data confiável do último backup.
- `lifecycle.trash_emptied`: “Limpeza da lixeira concluída”, com itens removidos e bytes liberados.
- `lifecycle.executed`: “Limpeza de backups expirados concluída”, com arquivos removidos por purga e bytes liberados.
- limpezas sem remoção não geram notificação operacional; o evento original continua na auditoria.

Os campos dinâmicos são escapados no momento do envio em modo HTML. O corpo não contém caminhos internos, segredos, identificadores técnicos ou repetição do título.

## Deduplicação

A causa visual da duplicidade era o uso do UUID da conta FTP como “Identificador” em todas as mensagens, somado à geração de uma fila por registro de auditoria. Arquivos distintos pareciam ter o mesmo identificador e o reprocessamento de eventos equivalentes podia criar mensagens repetidas.

A chave operacional agora combina tipo do evento, identidade estável do backup/arquivo, destino e tópico. A fila persistente é consultada antes da inclusão:

- retry atualiza a mesma entrega;
- reinício ou retrocesso do cursor não cria outra entrega;
- eventos com backups diferentes continuam independentes;
- nenhum histórico foi removido ou preenchido retroativamente.

## Compatibilidade

O processamento de resumos e o envio de arquivos de backup não foram alterados. A resolução existente de destino e tópico continua sendo usada, incluindo tópico `1411` em supergrupos e ausência de tópico em grupos simples.

## Operação segura

Esta alteração não modifica token, chat ID, credenciais FTP/SSH, Nginx, firewall, jobs ou arquivos reais. Testes usam transportes falsos; não há envio para a API do Telegram durante o desenvolvimento.

## Evidência da suíte completa

Em 20/07/2026, a suíte da entrega executou 322 testes e reproduziu sete falhas fora do escopo em equipamentos/MikroTik e reset administrativo. O commit-base `fe9d463`, em worktree limpo e sem este patch, executou 319 testes e apresentou exatamente os mesmos sete testes com falha. Todos os sete também falham isoladamente, descartando dependência dos testes de Telegram como causa.

Os seis testes de equipamentos verificam textos, marcação e status HTTP anteriores à linha de modernização da interface. O teste de reset remove uma migração intermediária, cenário classificado pelo verificador atual como `SCHEMA_INVALID`, enquanto a asserção antiga ainda espera `SCHEMA_OUTDATED`. Nenhum traceback aponta para os arquivos de notificações ou lifecycle desta entrega.

Uma execução apresentou adicionalmente uma oscilação no teste concorrente dos dois importadores MikroTik. O teste passou isoladamente dez vezes consecutivas, passou com os oito casos reunidos e não falhou na suíte do worktree limpo; não foi classificado como regressão desta entrega.

Há ainda uma fragilidade preexistente ao combinar módulos manualmente no mesmo processo: módulos de teste configuram variáveis `BACKUP_MANAGER_*` durante a importação, enquanto `backup_manager.db.DB_PATH` é resolvido uma única vez. A ordem pode fazer uma classe usar o banco temporário criado por outra. Os conjuntos focados são executados em processos separados para preservar o isolamento pretendido.
