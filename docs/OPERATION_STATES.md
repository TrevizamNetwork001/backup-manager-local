# Estados canônicos de operações

O banco atual preserva vocabulários históricos diferentes por domínio. A aplicação
não deve comparar esses valores fora do módulo `backup_manager.operation_states`.

Estados canônicos expostos por serviços e interfaces:

- `pending`: registrado, ainda sem execução efetiva;
- `running`: trabalho em andamento, incluindo espera/validação;
- `success`: resultado concluído e utilizável;
- `failed`: resultado terminal com falha;
- `cancelled`: interrompido ou retirado do fluxo operacional;
- `expired`: terminou porque ultrapassou seu prazo.

Os valores legados continuam armazenados sem alteração. A tradução cobre testes
FTP MikroTik, operações de artefatos, uploads MikroTik, recebimentos FTP e backups.
Uma migration futura somente poderá substituir esses estados após auditoria dos
dados reais, backup, `integrity_check`, `foreign_key_check` e plano de rollback.
