# Centro de Operações e observabilidade

## Visão geral

O Dashboard funciona como um Centro de Operações (NOC) usando exclusivamente
dados já registrados no SQLite, uso do filesystem e um snapshot local de
serviços. Nenhuma requisição web executa shell, `systemctl` ou subprocessos.

A página atualiza a cada 30 segundos e reúne:

- estado global e indicadores de saúde;
- alertas atuais, sem popups;
- equipamentos ordenados por criticidade;
- backups SSH e FTP, rejeitados e lixeira;
- jobs, worker e scheduler;
- serviços e armazenamento;
- timeline baseada na auditoria existente.

## Estado global

O estado global assume a maior severidade atualmente detectada:

- **VERDE**: nenhum problema atual;
- **AMARELO**: condição de atenção, como backup com mais de 24 horas, disco a
  partir de 85%, rejeição FTP, health-check ainda não executado ou snapshot de
  serviços desatualizado;
- **VERMELHO**: equipamento sem backup ou com backup acima de 72 horas, job/SSH/
  FTP falhando nas últimas 24 horas, serviço esperado inativo, disco a partir
  de 95% ou falha de integridade.

Os indicadores mostram separadamente SSH, FTP, Worker, Scheduler, Storage,
disco, equipamentos e jobs, permitindo identificar a origem do estado global.

## Equipamentos

Somente equipamentos ativos são considerados. A tabela usa o último backup
disponível e identifica o método pelo backup mais recente. Quando ainda não há
backup, usa a existência de conta FTP ou driver SSH configurado como indicação
do método esperado.

A ordenação é: vermelho, amarelo, verde; dentro da severidade, os backups mais
antigos aparecem primeiro.

## Timeline

A timeline traduz eventos existentes de `audit_log`, incluindo:

- backup manual recebido;
- backup SSH ou FTP concluído;
- upload FTP rejeitado ou com erro;
- erro SSH e falha/timeout de job;
- execução de limpeza/retenção;
- restauração da lixeira;
- alteração da política de retenção.

Nenhum novo sistema de eventos ou dependência externa é necessário.

## Snapshot seguro de serviços

`backup-manager-observability.timer` executa a cada 30 segundos. A unit oneshot
consulta propriedades fixas das seguintes units e grava
`data/service-status.json` de forma atômica:

- `backup-manager-local.service`;
- `backup-manager-worker.service`;
- `backup-manager-worker.timer` (Scheduler);
- `backup-manager-ftp-importer.timer`;
- `pure-ftpd.service`.

O coletor usa `subprocess.run` com lista fixa de argumentos, sem `shell=True`.
A aplicação web apenas lê JSON regular, rejeita symlink e arquivos acima de
256 KiB, e considera snapshots com mais de 90 segundos desatualizados.

O Worker é uma unit oneshot: `inactive` com último resultado `success` é normal.
FTP importer e Pure-FTPd aparecem como “não habilitado” quando `ftp_enabled=0`.

```bash
systemctl status backup-manager-observability.timer
journalctl -u backup-manager-observability.service --since today
```

## Estatísticas e armazenamento

As estatísticas apresentam contagem de backups SSH/FTP disponíveis, rejeitados,
lixeira, jobs ativos/em fila, auditoria, bytes de backups e espaço total/usado/
livre. O último relatório de integridade do Lifecycle é reutilizado; o Dashboard
não recalcula hashes nem altera arquivos.

## Responsividade

O layout possui adaptações para desktop, notebook, tablet e celular. Tabelas
mantêm rolagem horizontal quando necessário; indicadores, serviços, alertas e
timeline reorganizam-se em menos colunas conforme a largura disponível.

## Limites e segurança

- Não há chamadas externas, agentes ou bibliotecas adicionais.
- O refresh é uma recarga GET simples a cada 30 segundos.
- O Dashboard não inicia testes SSH/FTP nem executa limpeza ou Lifecycle.
- Nenhuma configuração de SSH, FTP, PureDB, Nginx, SSL, Storage ou Lifecycle é
  modificada pela observabilidade.
