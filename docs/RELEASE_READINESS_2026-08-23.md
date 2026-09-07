# Situação para fechamento da v1.1.0 — 23/08/2026

Registro histórico. A promoção de 07/09/2026 está documentada em
[RELEASE_1.1.0.md](RELEASE_1.1.0.md).

## Concluído

- interface revisada e sem páginas operacionais no layout antigo;
- storage reconciliado: 194 backups verificados, sem ausentes ou órfãos;
- integridade SQLite, foreign keys, lifecycle, jobs e FTP aprovados;
- guardião de recuperação executado manualmente com resultado `success` e timer ativo;
- SSH real aprovado nos equipamentos `CE-VPN` e `CE-BASE-CONECTA`;
- contas FTP `trevizam` e `user2020`, diretórios e Pure-FTPd aprovados;
- entrega Telegram real aprovada no tópico configurado (`message_id=1945`);
- cabeçalhos HTTPS deduplicados: CSP, HSTS, `DENY`, `nosniff`, Referrer e
  Permissions Policy são entregues uma única vez;
- serviço web e timers essenciais ativos; login HTTPS respondeu HTTP 200;
- suíte completa aprovada: 340 testes;
- construtor `.bmu` corrigido e validado de ponta a ponta com chave temporária.
  O pacote e a chave temporários foram removidos após o teste.
- Google Drive reconectado e validado com OAuth; um backup real foi confirmado
  como `synced` em `PEDRAS-TELECOM/CE-VPN/05-09-2026`.
- OLT `OLT-SANCA` homologada com backups reais disponíveis.
- Cópia de arquivos Telegram habilitada para todos os equipamentos, com
  política global ativa no tópico `1411`.

## O que ainda falta

### 1. Criar a assinatura oficial e promover a versão

A chave pública `/etc/backup-manager-local/update-public-key.pem` foi instalada
e validada no servidor. Ainda falta gerar a chave privada oficial em uma estação
de release isolada, guardá-la offline e usá-la para promover a versão para
`1.1.0`, rodar os checks finais, criar a tag e publicar o pacote `.bmu` assinado.

### 2. Ajustar o tópico Geral do Telegram (opcional)

O teste genérico do canal recebeu `TOPIC_CLOSED`, pois o tópico Geral do grupo
está fechado. O destino ativo usa o tópico `1411` e a entrega real funcionou.
Abra o tópico Geral apenas se também quiser receber mensagens sem mapeamento de
tópico; o fluxo configurado atual não depende disso.

O tópico Geral pode permanecer fechado; o destino configurado usa o tópico
`1411` e já foi validado.
