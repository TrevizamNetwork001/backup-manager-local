# RC1 — Relatório de estabilização e validação

FASE 10.1: confirmar migration 012, integridade SQLite, unit sem rede, fila/retry/janela/cancelamento/auditoria, revalidação SHA-256/tamanho e ausência de mudanças no backup local, Nginx, firewall, FTP, SSH e Lifecycle.

Data: 2026-07-11  
Plataforma do upgrade: Debian GNU/Linux 13.5 (`trixie`), VM VMware.

## Resultado geral

O RC1 foi aplicado com `scripts/deploy-update.sh`. O fluxo criou backup SQLite,
aplicou migrations, instalou as units versionadas, reiniciou somente aplicação
e timers previstos e validou login HTTPS. Nginx foi apenas validado e não foi
recarregado.

Foram corrigidos:

- mensagens técnicas que poderiam expor SQLite ou caminhos internos;
- limpeza de temporários em falha de importação;
- feedback consistente para operações longas;
- toasts, foco visível, breadcrumbs, estados vazios e responsividade;
- documentação desatualizada sobre FTP, Lifecycle, NOC e Telegram;
- comandos ausentes `jobs-check` e `notification-check`;
- índices das consultas operacionais mais frequentes;
- permissão do SQLite para `0640` e `UMask=0027` nas units web/worker.
- apresentação dos horários UTC no timezone configurado, inclusive no dashboard,
  auditoria e atualização automática das execuções.
- remoção de imports, rotas internas, protótipos e assets sem referência;
- padronização da acentuação dos textos operacionais;
- fechamento explícito das conexões SQLite usadas pelos testes, eliminando
  `ResourceWarning`;
- supressão do access log duplicado do servidor WSGI para respostas bem-sucedidas;
  acessos continuam registrados pelo Nginx e erros HTTP permanecem no journal.

## Testes automatizados

```text
Ran 44 tests
OK
```

A instalação lógica limpa foi validada aplicando, em banco vazio, todas as
migrations `001` a `011`, seguida de `PRAGMA integrity_check`. O teste de upgrade
aplicou `001` a `010`, inseriu equipamento e segredo criptografado, aplicou
`011_rc1_performance_indexes.sql` e comprovou preservação dos dados.

Não havia uma segunda VM descartável limpa disponível no ambiente. Portanto,
instalação limpa com APT/systemd/Nginx não foi executada destrutivamente; essa
parte foi coberta por migrations do zero, testes estáticos do instalador e pela
implantação real em Debian existente. Antes de cliente novo, recomenda-se ainda
um smoke test em snapshot descartável da imagem Debian de destino.

## Upgrade e preservação

Hashes agregados antes/depois ficaram idênticos para:

- `/etc/nginx`;
- `/etc/letsencrypt`;
- `/etc/ufw`;
- `pureftpd.passwd` e `pureftpd.pdb`;
- backups permanentes;
- uploads/área FTP;
- configuração criptografada de notificações.

Backups SQLite do upgrade:

```text
data/deploy-backups/backup_manager.sqlite3.deploy-20260711181542.bak
data/deploy-backups/backup_manager.sqlite3.deploy-20260711181817.bak
```

## Checklist operacional

| Verificação | Resultado |
|---|---|
| `integrity-check` | `ok` |
| `PRAGMA foreign_key_check` | 0 violações |
| `storage-check --hash` | `ok` |
| `ftp-config-check` | `ok` |
| `jobs-check` | `ok` |
| `notification-check` | `ok`, canal desabilitado |
| `nginx -t` | aprovado |
| Login HTTPS | HTTP 200, 0,256 s na medição |
| Aplicação | ativa |
| Worker timer | ativo; última oneshot `SUCCESS` |
| FTP importer timer | ativo |
| Pure-FTPd | ativo |
| Lifecycle timer | ativo |
| Observability timer | ativo |

O teste SSH real do equipamento ativo `MK-TESTE` retornou
`SSH_CONNECTION_REFUSED`. O erro foi apresentado de forma operacional, sem
traceback ou segredo. Credencial, porta e equipamento não foram modificados;
conectividade SSH do cliente permanece como pendência externa ao RC1.

## Segurança

- sudoers FTP: `visudo` aprovado, modo `0440 root:root`;
- helper FTP: `0750 root:root`;
- chave de segredos: `0640 root:root`;
- PureDB/passwd: `0600 root:root`;
- SQLite: `0640 root:root`;
- storage/backups/trash: `0750 root:root`;
- nenhum symlink encontrado em backups ou lixeira;
- logs recentes sem traceback, token, senha, segredo ou erro SQLite;
- token Telegram não configurado e nunca exibido.

## Desempenho

`EXPLAIN QUERY PLAN` confirmou uso dos índices RC1 para:

- último backup por equipamento/status/data;
- fila de jobs por status/data;
- timeline de auditoria por ação/ID;
- deduplicação de notificações.

O refresh do NOC permanece em 30 segundos e não executa shell, hashes ou chamadas
externas na requisição.

## Interface e limitações visuais

Os testes verificam breakpoints em 1280, 1050, 860 e 520 px, além do layout base
para telas maiores, foco visível, redução de movimento, labels, breadcrumbs,
toasts e mensagens vazias. Não havia Chromium/Chrome instalado na VM; por isso
não foram produzidas capturas reais em 1920/1600/1366. A validação final em
navegadores dos clientes deve fazer parte do smoke test da imagem descartável.
