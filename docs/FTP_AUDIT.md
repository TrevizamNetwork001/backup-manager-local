# Auditoria FASE 6 — FTP / Pure-FTPd

Data: 2026-07-10

## Estado observado

- Pure-FTPd: ativo.
- `backup-manager-ftp-importer.timer`: ativo e aguardando.
- Última execução do importer: `detected=0 waiting=0 imported=0 rejected=0 failed=0`.
- `ftp-config-check`: OK.
- Conta virtual: 1 ativa (`trevizam`), vinculada ao equipamento 1.
- UID/GID do usuário de sistema: `backupftp` = `988:989`.
- `MinUID`: `988`, compatível.
- PureDB e `pureftpd.passwd`: presentes, `root:root`, modo `0600`.
- Pure-FTPd usa PureDB, PAM, modo passivo `30000:30100`, porta 21 e `-u 988`.
- UFW preserva 80/443 e possui 21 e 30000:30100 em IPv4/IPv6.

## Arquivos encontrados

Uma conta possui diretórios `incoming`, `processing` e `rejected`. Os arquivos
rejeitados permanecem no diretório `rejected` e não foram removidos.

O banco registra 7 uploads: 1 importado e 6 rejeitados por `invalid_filename`.
O arquivo importado criou um backup com `source_method=ftp`, SHA-256 registrado
e vínculo com o equipamento 1. Os seis PDFs foram preservados e aguardam uma
decisão explícita sobre a política de extensões/nomes permitidos.

## Permissões

O diretório raiz da conta está `root:root`, enquanto `incoming` está
`backupftp:backupftp`; `processing` e `rejected` estão `root:backupftp`.
Isso permite o upload observado, mas requer validação específica do usuário do
importador antes de qualquer normalização. Nenhuma permissão foi alterada nesta
auditoria.

## Riscos/pêndencias

- `enable-ftp.sh` ainda não contém o fluxo completo de reconciliação/idempotência.
- Não existe ainda `ftp-accounts-reconcile` no CLI.
- A configuração Pure-FTPd mantém PAM habilitado; precisa de decisão/teste antes
  de remover o link PAM.
- O teste ponta a ponta e o teste de isolamento não foram executados nesta
  auditoria para não criar contas ou alterar dados de produção.
- A interface, Nginx, certificados e UFW não foram alterados.

## Hashes da interface

Os hashes atuais foram coletados por `ui-integrity-check` e permanecem no
relatório de auditoria/backup pré-refatoração. Nenhuma alteração de interface
foi feita durante esta auditoria.
