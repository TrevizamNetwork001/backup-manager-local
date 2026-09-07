# Simplificação estrutural — relatório consolidado

Data: 17/07/2026  
Branch analisada: `feature/telegram-backup-v1.1`

## Atualização de 17/07/2026

Sessão atual focada em quebrar o monólito de `app.py` sem alterar o
comportamento funcional.

Extrações concluídas nesta rodada:

- teste SSH -> `backup_manager/ssh_test_service.py`;
- execução manual de backup -> `backup_manager/equipment_run_service.py`;
- histórico de backups -> `backup_manager/backups_views.py`.

O detalhe do equipamento continua sendo o maior bloco residual em `app.py`, com
corpo legado ainda presente após os `return` atuais. A próxima sessão deve
remover esse bloco morto e seguir com as rotas restantes de equipamento e FTP.

Situação validada ao final da rodada:

- `python3 -m py_compile` passou nos módulos tocados;
- nenhum fluxo SSH estável foi alterado;
- a simplificação segue incremental, sem migrations nem deploy.

## Resumo executivo

A etapa reduziu caminhos duplicados e corrigiu falhas concretas sem reescrever
o sistema e sem alterar o fluxo SSH estável. O FTP agora possui serviço central
de ciclo de vida, diagnóstico central, importador dividido em etapas e teste
com término obrigatório. Histórico deixou de ser confundido com integração
ativa. Contas e equipamentos podem ser arquivados sem apagar backups de forma
implícita.

A simplificação ainda não está completa. `app.py` continua concentrando muitas
rotas, SQL e HTML. A quantidade de executáveis e units não caiu porque nenhum
deles foi comprovado como morto; removê-los apenas para melhorar a métrica
criaria risco operacional. A próxima redução deve decompor a web por domínio e
extrair o SQL administrativo restante do CLI em commits pequenos.

## Entregas concluídas

- interface do equipamento reduzida a Resumo, Backup, Histórico e Diagnóstico;
- cadastro de equipamento recolhido em modal;
- duração de atraso amigável e datas Telegram no fuso configurado;
- regra de remoção FTP baseada em integração realmente ativa;
- desativação segura de integração mesmo com conta excluída;
- histórico de contas excluídas e lixeira visíveis;
- expurgo definitivo protegido por confirmação administrativa;
- arquivamento de equipamento com escolha entre preservar ou enviar backups à lixeira;
- teste FTP com timeout, resultado final e mensagem de senha recusada;
- ciclo de vida de contas centralizado em `FTPProvisioningService`;
- diagnóstico e sincronização centralizados em `ftp_diagnostics.py`;
- importador separado em pipeline, correlação, storage e eventos;
- claim atômico contra dois importadores processando o mesmo arquivo;
- visão canônica de estado para operações, mantendo compatibilidade legada;
- aliases hierárquicos no CLI sem quebrar comandos antigos;
- manifesto canônico de units systemd usado por instalação e deploy;
- conta FTP `file_server` sem equipamento, fora do importador de backups;
- migration 029 testada com preservação de dados, `integrity_check` e
  `foreign_key_check`.

## Métricas atuais

| Item | Fase 0 | Atual | Leitura |
|---|---:|---:|---|
| `backup_manager/app.py` | 5.251 | 5.357 | cresceu por UX e segurança; ainda deve ser dividido |
| `backup_manager/cli.py` | 735 | 732 | FTP saiu do CLI, outros domínios ainda têm SQL |
| `static/app.js` | 475 | 511 | cliente de operações e modais compartilhados |
| Scripts Python em `scripts/` | 4 | 4 | todos possuem uso identificado |
| Scripts Shell em `scripts/` | 6 | 6 | consolidação adicional depende de deploy controlado |
| Entradas `__main__` em aplicação/scripts | 15 | 14 | uma entrada eliminada/consolidada |
| Comandos legados do parser | 56 | 56 | mantidos por compatibilidade |
| Serviços systemd | 10 | 10 | manifesto único; nenhum serviço morto comprovado |
| Timers systemd | 7 | 7 | nenhum timer morto comprovado |
| Rotas web literais | 59 | 60 | nova ação segura; handlers ainda concentrados |

Contagem de linhas não deve ser usada isoladamente: foram adicionados testes,
contratos e proteções. A meta seguinte é reduzir responsabilidade e caminhos
duplicados antes de buscar redução bruta de linhas.

## Fluxos atuais simplificados

```text
Web / CLI / worker
        |
        +--> FTPProvisioningService --> banco --> helper allowlisted --> PureDB
        |
        +--> ftp_diagnostics -------> relatório estruturado

Pure-FTPd --> incoming
                 |
                 +--> conta backup --> discovery --> stabilization --> validation
                 |                                      |
                 |                                 correlation --> storage --> events
                 |
                 +--> conta file_server --> permanece isolada; não vira backup
```

O backup SSH permanece separado e protegido. Nenhuma refatoração FTP alterou o
executor SSH.

## Riscos e pendências reais

1. `app.py` ainda mistura controller, consulta e HTML em diversos domínios.
2. `cli.py` ainda contém SQL de storage, jobs, Telegram, updates e cloud.
3. Os estados legados continuam no banco; a visão canônica não é migration de
   dados e não autoriza apagar tabelas históricas.
4. `file_server` não significa FTP irrestrito: o Pure-FTPd continua com política
   segura de uploads write-only. Publicação/download exige desenho específico.
5. A migration 029 está no código, mas não foi aplicada em produção.
6. Warnings de conexões SQLite não fechadas aparecem em alguns testes antigos;
   não causam falha, mas devem ser corrigidos por módulo.
7. MariaDB não é necessária para resolver a lógica FTP. Migrar agora aumentaria
   superfície operacional; só deve ser reconsiderado com concorrência ou escala
   comprovadamente incompatível com SQLite.

## Componentes: decisão atual

| Componente | Decisão |
|---|---|
| Backup MikroTik via SSH | MANTER e proteger |
| `FTPProvisioningService` | MANTER |
| módulos do pipeline FTP | MANTER |
| SQL FTP no CLI | REMOVIDO/FUNDIDO |
| SQL restante no CLI | FUNDIR em serviços por domínio |
| HTML e SQL em `app.py` | REESCREVER DE FORMA MENOR, por domínio |
| comandos legados | MANTER como aliases temporários |
| scripts de build/publicação/helper | MANTER |
| `deploy-ui.sh` versus `deploy-update.sh` | INVESTIGAR antes de fundir |
| tabelas FTP históricas | MANTER até migração de dados validada |
| MariaDB | INVESTIGAR MELHOR; não migrar agora |

## Ordem recomendada restante

1. Extrair consultas e apresentações Cloud/Telegram/Updates do CLI.
2. Separar rotas FTP e equipamento de `app.py`, reutilizando os serviços atuais.
3. Criar testes de caracterização para cada grupo antes da extração.
4. Corrigir warnings de conexões SQLite por módulo.
5. Validar FTP em laboratório com OLT/switch real e registrar matriz de
   compatibilidade, inclusive modo passivo e mensagens de autenticação.
6. Somente depois medir scripts/units absorvíveis e remover aliases sem usuários.

Cada item deve ser commit isolado, com testes focados e suíte completa. Migration
em ambiente real exige backup SQLite, `integrity_check`, `foreign_key_check`,
relatório de impacto e rollback pelo backup pré-migration.

## Compressão futura

Manter arquivos originais por enquanto. Se houver necessidade de espaço,
compactar após validação somente conteúdo textual (`.rsc`, `.cfg`, `.xml`, logs
rotacionados) e apenas quando a economia superar um limiar medido. Firmware e
formatos binários já compactados não devem passar por BZ2 automaticamente.
