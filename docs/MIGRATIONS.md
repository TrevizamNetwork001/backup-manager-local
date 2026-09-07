# Controle explícito de migrations

O schema nunca é alterado pela aplicação web, requisições, workers,
importadores ou timers. Esses processos fazem somente uma verificação SQLite
em modo read-only e encerram ou respondem `503` com `SCHEMA_OUTDATED`,
`SCHEMA_AHEAD` ou `SCHEMA_INVALID` quando o banco não é compatível.

As migrations são autorizadas pelo `migrations/manifest.json`, que fixa versão,
nome e SHA-256. A presença de um arquivo `.sql` fora do manifesto não autoriza
sua execução. A versão-alvo é obrigatória:

```bash
python3 -m backup_manager.cli migrate --to-version 31
```

Instalação e deploy obtêm esse alvo por
`python3 -m backup_manager.cli schema-release-version`; não mantêm uma segunda
cópia manual do número do schema.

O migrator adquire um `flock` exclusivo antes de reler o manifesto, o banco e a
lista pendente. O caminho principal é
`/run/lock/backup-manager-migrate.lock`; instalações sem permissão usam o
diretório confinado `data/locks`, nunca uma opção fornecida pela CLI.

Bancos históricos registram somente o nome da migration, sem o hash aplicado.
Assim, não é possível provar retroativamente quais bytes foram executados. O
manifesto autentica os arquivos atuais e toda execução futura, sem bloquear um
histórico antigo válido. Um registro persistente de hashes aplicados poderá ser
adicionado de forma aditiva em uma migration futura.

Antes do primeiro DDL em banco existente, a API de backup do SQLite cria uma
cópia consistente em `data/migration-backups` e executa `integrity_check` nela.
Cada migration é confirmada separadamente. Se uma migration falhar, sua
transação é revertida e as anteriores permanecem registradas para retomada.
Ao final são exigidos `foreign_key_check`, `integrity_check` e a versão-alvo.

Instalação e deploy chamam o migrator explicitamente antes de iniciar os
serviços. O deploy interrompe a aplicação e os workers antes da atualização de
schema e só os reinicia após as validações.

## Health checks

- `GET /health/live`: confirma que o processo HTTP responde, sem consultar o
  banco.
- `GET /health/ready`: retorna sucesso somente quando o schema corresponde ao
  manifesto do release.

Com schema incompatível, as demais rotas exibem manutenção sanitizada e não
executam operações normais.
