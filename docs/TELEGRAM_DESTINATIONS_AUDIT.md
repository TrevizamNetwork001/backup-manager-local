# Auditoria de destinos Telegram — v1.1.0-rc1

Auditoria realizada em 15/07/2026 sem exibir token ou Chat ID completo.

## Diagnóstico

| ID | UUID | Nome | Chat mascarado | Ativo | Finalidades | Enviados | Falhas | Resultado |
|---:|---|---|---|---:|---|---:|---:|---|
| 1 | `d9fb234f-c598-46f4-a544-1ed0a8122fb9` | Destino principal | `…5554` | sim | alertas, diário, semanal, executivo | 15 | 373 | funcional; sucesso posterior às falhas |
| 2 | `e42355e8-aa03-49d8-8cd1-e57f905b82fc` | Destino principal | `…2712` | sim (antes da correção) | alertas, diário, semanal, executivo | 11 | 3 | grupo antigo/inválido |

A duplicidade surgiu porque a configuração legada sincronizou um novo Chat ID sem desativar o registro anterior e não havia unicidade de nome ativo nem aviso de múltiplos destinos. Ambos ficaram habilitados para as mesmas finalidades. O destino 1 recebeu mensagens às 21:27 (horário local da auditoria), depois de suas falhas históricas. O destino 2 falhou nos testes de resumo diário, semanal e executivo mais recentes.

Não havia `telegram_backup_items` para os destinos, portanto nenhum backup real foi cancelado ou transferido. O mapeamento de tópico existente pertence ao destino 1. A correção recomendada é desativar o destino 2, preservar todo o histórico e cancelar somente testes/resumos ainda pendentes, sem reenvio automático.

## Proteções implementadas

- nome obrigatório, normalizado e sem duplicidade case-insensitive entre ativos;
- desativação lógica auditada, sem exclusão física;
- CLI para consultar, desativar e cancelar pendências por tipo;
- confirmação especial para `all`;
- novos enfileiramentos já ignoram destinos inativos;
- histórico e referências permanecem preservados.

Tokens, credenciais, caminhos absolutos e Chat IDs completos foram omitidos deste documento.
