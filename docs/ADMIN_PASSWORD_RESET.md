# Reset seguro da senha administrativa

`temp-password` não redefine senhas. Ele existe apenas para revelar a senha
inicial antes da conclusão do setup de uma instalação nova. Depois de
`setup_complete=1`, o comando não revela a senha e orienta o operador a usar o
reset seguro.

O comando de recuperação é:

```bash
python3 -m backup_manager.cli reset-admin-password
```

Sem confirmação, ele é estritamente diagnóstico: valida o arquivo apontado por
`BACKUP_MANAGER_DB`, schema, integridade, perfil do banco e administrador, e
mostra somente caminho, tamanho, versão e contagens. Ele não cria banco, schema,
usuário, seed ou migration.

Para executar, pare aplicação, workers e timers que escrevem no SQLite. Se houver
mais de um administrador, selecione exatamente um com `--username` ou
`--user-id`. Use entrada interativa:

```bash
python3 -m backup_manager.cli reset-admin-password \
  --username admin --confirm RESETAR_SENHA_ADMIN
```

Ou gere uma senha criptograficamente segura, exibida uma única vez no terminal:

```bash
python3 -m backup_manager.cli reset-admin-password \
  --username admin --generate --confirm RESETAR_SENHA_ADMIN
```

Nunca passe senha como argumento. Antes da escrita, o comando cria e valida uma
cópia pela API de backup do SQLite em `data/password-reset-backups/`. A transação
altera somente `users.password_hash`, `users.must_change_password`, sessões do
usuário selecionado e um evento sanitizado `auth.admin_password_reset`.

Se o comando falhar, preserve o banco e o backup, mantenha os serviços parados e
investigue o código sanitizado. Não apague, mova ou renomeie o banco para tentar
resetar uma senha: se o arquivo correto não estiver presente, o reset deve ser
cancelado, nunca inicializado novamente.

Em uma recuperação excepcional, valide a cópia `.pre-password-reset-*.bak` com
`integrity_check` e `foreign_key_check` antes de considerar qualquer rollback.
Não restaure automaticamente sobre um banco ativo.
