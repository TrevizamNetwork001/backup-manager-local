# Security

Este projeto mantem a documentacao de seguranca principal em portugues:

- [SEGURANCA_SSL.md](SEGURANCA_SSL.md)

Resumo da FASE 5:

- senhas SSH sao criptografadas no SQLite;
- a chave local fica em `/etc/backup-manager-local/secret.key`;
- a chave nao deve ir para Git e precisa de backup seguro;
- logs, auditoria e HTML nao devem conter senha ou conteudo de backup;
- usuarios de leitura/download nao criam, editam nem testam credenciais.
