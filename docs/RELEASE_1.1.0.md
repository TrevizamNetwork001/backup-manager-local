# Backup Manager Local 1.1.0

Data: 07/09/2026. Canal: `stable`. Build: `stable-1.1.0-20260907`.

## Alterações

- Promoção da versão RC9 para 1.1.0.
- Backup Huawei: respostas de falha FTP interrompem o roteiro; comandos
  recusados são reportados imediatamente, sem aguardar a conclusão do backup.
- Rclone: preservação de subpastas e espaços na pasta base do layout por grupo;
  o layout por nome da instalação permanece disponível.
- Consolidação dos ajustes locais de OLT, Google Drive e apresentação de backups.

## Validação e implantação

Implantação concluída pelo `scripts/deploy-update.sh` em 07/09/2026:

- 363 testes aprovados; compilação Python e sintaxe do instalador aprovadas.
- Banco íntegro (`ok`) e schema atualizado (`SCHEMA_OK`, versão 31).
- Storage validado com hashes; jobs sem atraso indicado pelo diagnóstico.
- Notificações configuradas e sem pendências atrasadas; 369 falhas históricas
  permanecem registradas, sem apagar evidências.
- Backup SQLite: `data/deploy-backups/backup_manager.sqlite3.deploy-20260907141310.bak`.
- Aplicação reiniciada e timers operacionais ativos.
- Readiness e login responderam HTTP 200 após a implantação.
- Configuração FTP e sintaxe Nginx aprovadas; configuração Nginx preservada.
- Log da execução: `/tmp/backup-manager-stable-deploy-20260907.log`.
- Pacote RC9 → 1.1.0 validado com chave temporária: assinatura, hashes,
  versão/canal/build do payload e índice assinado consistentes. Chave e pacote
  de teste foram descartados. Esse teste não constitui assinatura oficial.

Não foram disparados backups manuais em equipamentos nem envios de teste a
serviços externos nesta promoção. Os timers habituais foram retomados.

## Distribuição assinada

A publicação externa ainda depende da chave privada oficial correspondente à
chave pública instalada e da URL HTTPS de distribuição. Não substituir a chave
pública para contornar a ausência da chave privada.

Na estação de release, usando o código desta versão:

```bash
python3 scripts/build-update-package.py --version 1.1.0 \
  --minimum-version 1.1.0-rc9 --channel stable \
  --build-id stable-1.1.0-20260907 \
  --private-key /segredos/update-private-key.pem --output /pacotes \
  --release-notes "1.1.0: versão estável; correções Huawei FTP e caminhos rclone."
python3 scripts/publish-update-index.py --packages /pacotes --output /publicavel \
  --base-url https://SEU-REPOSITORIO/backup-manager \
  --private-key /segredos/update-private-key.pem
```

O caminho de atualização previsto para este lançamento é RC9 → 1.1.0.
Os comandos acima usam caminhos ilustrativos e não representam publicação já feita.
