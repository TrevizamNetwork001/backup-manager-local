# Ciclo de vida das contas FTP

A migration `024_ftp_account_lifecycle.sql` libera a identidade de uma conta depois de sua desativação ou exclusão lógica. O histórico permanece associado ao registro original, enquanto uma nova conta ativa pode reutilizar o mesmo equipamento ou username.

## Garantias

- somente uma conta ativa por equipamento;
- username único apenas entre contas ativas;
- contas inativas e excluídas logicamente permanecem consultáveis;
- uploads e backups históricos não são apagados nem transferidos;
- falhas ao sincronizar com o Pure-FTPd revertem a mudança lógica;
- recriação gera um novo registro e preserva o anterior;
- a execução repetida pelo mecanismo de migrations não reaplica a transformação.

O deploy não remove diretórios de backup e não modifica arquivos recebidos. A migration foi validada em banco limpo, esquema anterior à 024 e banco com histórico FTP.
