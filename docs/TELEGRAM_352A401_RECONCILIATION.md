# Reconciliação funcional de `352a401`

| Área | Classificação | Decisão |
|---|---|---|
| Pipeline de arquivo e `sendDocument` | presente | Mantido o transporte atual com HTML seguro. |
| Políticas global/grupo/equipamento | equivalente, com lacuna de override | Recuperado o override explícito por equipamento. |
| Enqueue automático | presente | Mantido. |
| Deduplicação | ausente parcialmente | Recuperada chave idempotente pela migration 026. |
| Estados e retry | presente | Mantidos os estados atuais e acrescentado `last_attempt_at`. |
| Caption premium | superior no HEAD | Descartado o template curto de `352a401`. |
| Arquivo fictício | superior no HEAD | Mantidos nome, conteúdo seguro e remoção atuais. |
| Interface Backup externo | presente, configuração incompleta | Recuperados configuração global, filtros e enqueue manual. |
| Prévia de backup | ausente em ambos | Não recuperada de `352a401`. |
| Gestão de destinos | superior no HEAD | Mantida a desativação/auditoria atual. |
| Defaults por finalidade | ausente em `352a401` | Não recuperado. |
| Dois artefatos MikroTik/message_ids | ausente em `352a401` | Não recuperado; o commit antigo criava entregas independentes. |
| Link Abrir mensagem | ausente em `352a401` | Não recuperado. |
| Histórico | presente | Mantido e exibido também no detalhe do backup. |
| Testes de entrega | parcialmente úteis | Recriados testes menores contra o desenho atual. |
| Units/timers | já presentes | Nenhuma unit foi copiada. |
| `025_telegram_backup_delivery.sql` | conflitante | Não recuperada; substituída funcionalmente pela 026 tolerante. |

A migration 026 aceita tanto o schema normal com 024/025 atuais quanto bancos que já possuam as quatro colunas da variante histórica. Nenhum segredo, token, banco ou arquivo de backup faz parte da reconciliação.

## Estado operacional e schema histórico

O commit `352a401` não integra a ancestralidade desta linha: ele foi substituído
semanticamente pelos commits de reconciliação que culminaram no HEAD operacional
anterior `7bcb082`. A sequência final é 024 (lifecycle FTP), 025 (limpeza de destinos),
026 (entrega de backups) e 027 (reconciliação do schema de destinos).

A primeira variante aplicada da 025 criou triggers de unicidade de nomes ativos;
o arquivo 025 versionado posteriormente deixou de contê-los. Esses triggers são
incompatíveis com a troca segura do destino legado, que cria o substituto antes
de desativar o registro anterior. A migration 027 não reescreve a história:
valida que o estado atual não tenha nomes ativos duplicados e remove explicitamente
os triggers históricos. A gestão de destinos mantém a validação equivalente na
aplicação usando `lower(trim(name))`; destinos inativos ou excluídos podem repetir
o nome e todo o histórico permanece preservado.

O envio de arquivos está implantado, mas permanece desabilitado. O tópico padrão
1411 é preservado e nenhum backup real foi enviado até este registro.
