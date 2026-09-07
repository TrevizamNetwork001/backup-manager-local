# Relatório técnico — FTP Push, causa raiz e possível migração para MariaDB

Data da análise: 17/07/2026  
Escopo: ambiente local `/opt/backup-manager-local`; nenhuma alteração em produção.

## 1. Resumo executivo

O SSH e a geração de backups estão operando corretamente e não devem ser reescritos. Os problemas observados estão concentrados no subsistema FTP, que atualmente combina cinco responsabilidades:

1. cadastro e ciclo de vida da conta FTP;
2. sincronização da conta virtual com Pure-FTPd/PureDB;
3. configuração e execução do script RouterOS;
4. recepção, estabilização e importação dos arquivos;
5. apresentação do estado e polling na interface web.

A causa principal não é a linguagem Python. O problema é a ausência de uma única máquina de estados e de reconciliação entre o banco da aplicação, o backend de autenticação do Pure-FTPd, o RouterOS e a interface web. Uma reescrita direta em PHP/MySQL levaria as mesmas ambiguidades para outra plataforma.

Recomendação:

- preservar SSH, backup, importador e histórico existentes;
- estabilizar o ciclo de vida FTP já corrigido localmente;
- avaliar primeiro MariaDB somente como backend de autenticação do Pure-FTPd;
- migrar o banco completo da aplicação apenas em uma segunda decisão, depois de testes de carga, restauração e compatibilidade SQL.

## 2. Evidências do MK-BASE

Equipamento analisado: `equipment_id=2`, hostname `MK-BASE`.

### 2.1 Contas FTP

| ID | Usuário | Estado | Exclusão | Observação |
|---:|---|---|---|---|
| 2 | `bm-mt-2-991c` | inativa | 14/07/2026 21:45:49 | conta originalmente vinculada à integração |
| 3 | `user2020` | inativa | 17/07/2026 03:34:38 | conta substituta sem vínculo com a integração antiga |

As duas contas estavam corretamente marcadas com `is_active=0` e `deleted_at` preenchido. Portanto, nenhuma delas deveria ser apresentada como conta ativa.

### 2.2 Integração e histórico

| Entidade | Quantidade | Estado relevante |
|---|---:|---|
| `mikrotik_ftp_integrations` | 1 | integração `id=1` ainda ativa |
| `mikrotik_ftp_tests` | 28 | histórico; um teste ainda estava em `waiting_upload` |
| `mikrotik_ftp_uploads` | 9 | histórico de testes e validações |
| `ftp_received_files` | 24 | arquivos importados, rejeitados ou com falha |

A integração `id=1` permanecia com `is_active=1` e `ftp_account_id=2`, embora a conta 2 já estivesse excluída. Isso caracteriza vínculo órfão lógico: a chave estrangeira ainda existe, mas o recurso operacional foi excluído.

## 3. Causa raiz

### 3.1 Exclusão incompleta da conta

O fluxo de exclusão da conta executava:

- `is_active=0`;
- preenchimento de `deleted_at`;
- desativação no Pure-FTPd.

Porém, ele não desativava a integração FTP Push vinculada. Assim, a conta deixava de existir operacionalmente, mas a integração continuava ativa.

### 3.2 Painel misturava atividade e histórico

O painel de remoção contava todas as linhas das tabelas FTP sem separar:

- contas ativas;
- contas inativas;
- contas excluídas;
- integrações ativas;
- integrações inativas;
- testes e uploads históricos.

Além disso, selecionava apenas uma conta e uma integração sem representar corretamente múltiplos registros.

### 3.3 Teste FTP escondia o erro real

O script temporário do RouterOS capturava qualquer falha e retornava somente `backup-manager ftp test failed`. Um erro real como `invalid user name or password` era descartado.

O backend interpretava a execução como tentativa iniciada e aguardava a chegada do arquivo. Como uma autenticação recusada nunca produziria arquivo, a interface permanecia carregando até o timeout defensivo.

## 4. Correções locais realizadas

- listagem de todas as contas e integrações do equipamento;
- conta classificada como ativa, inativa ou excluída;
- integração classificada como ativa ou inativa;
- indicação explícita de vínculo órfão;
- integração ativa é o bloqueador operacional FTP;
- testes, uploads e arquivos recebidos são apresentados como histórico;
- ação `Desativar integração FTP Push` disponível no modal de remoção;
- integração órfã pode ser desativada sem tentar recriar ou remover novamente o usuário do Pure-FTPd;
- testes e uploads permanecem preservados;
- erro de autenticação classificado como `FTP_AUTH_FAILED`;
- mensagem segura: `Autenticação FTP recusada. Verifique o usuário e a senha FTP configurados.`;
- erro de autenticação encerra o polling e reabilita o botão na interface.

Validação executada: 78 testes automatizados aprovados, incluindo ciclo de vida da conta, integração órfã, múltiplas contas, preservação de histórico, scripts RouterOS, importador e polling web.

## 5. Modelo de estados recomendado

### 5.1 Conta FTP

| Estado | Condição |
|---|---|
| ativa | `is_active=1 AND deleted_at IS NULL` e sincronização confirmada |
| pendente | ativa no banco, mas `sync_status=pending` |
| divergente | estado do banco diferente do backend Pure-FTPd/MariaDB |
| inativa | `is_active=0 AND deleted_at IS NULL` |
| excluída | `deleted_at IS NOT NULL`; deve também ter `is_active=0` |

### 5.2 Integração FTP Push

| Estado | Condição |
|---|---|
| ativa e íntegra | `is_active=1` e conta vinculada ativa |
| ativa e degradada | `is_active=1` e conta inativa, excluída ou ausente |
| inativa | `is_active=0`; pode permanecer como histórico |

Uma restrição ou serviço de domínio deve impedir a criação permanente do estado `ativa e degradada`. A reconciliação deve detectá-lo e oferecer correção segura, sem apagar histórico.

### 5.3 Teste FTP

Estados transitórios: `pending`, `running`, `waiting_upload`, `validating`.  
Estados terminais: `validated`, `failed`, `expired`, `cancelled`.

Qualquer erro conhecido de conexão, autenticação, permissão ou script deve produzir imediatamente um estado terminal. O polling só pode continuar para estados transitórios.

## 6. Opções de arquitetura com MariaDB

### Opção A — MariaDB somente para autenticação do Pure-FTPd

O banco principal da aplicação e o histórico permanecem no SQLite. Uma tabela pequena no MariaDB passa a ser a fonte de autenticação das contas virtuais do Pure-FTPd.

Vantagens:

- elimina a geração manual do arquivo PureDB;
- ativações e desativações ficam transacionais no backend de autenticação;
- permite consultar diretamente se o usuário está habilitado;
- impacto pequeno sobre SSH, backups, histórico e aplicação;
- rollback simples para PureDB.

Desvantagens:

- ainda existem dois bancos a reconciliar;
- exige disponibilidade, backup e monitoração do MariaDB;
- a aplicação precisa de uma fila/outbox ou processo de reconciliação confiável.

Esta é a opção recomendada como primeira etapa.

### Opção B — migrar todo o banco da aplicação para MariaDB

SQLite, autenticação FTP, inventário, histórico, auditoria e filas passam para MariaDB.

Vantagens:

- uma única transação pode controlar conta, integração e autenticação;
- melhor concorrência entre web, workers, scheduler e importador;
- operação e consultas centralizadas.

Desvantagens:

- migração ampla, incluindo todas as tabelas que não são FTP;
- várias consultas usam sintaxe e funções específicas do SQLite;
- índices parciais precisam ser redesenhados;
- migrações, testes, backup, restore e ferramentas administrativas precisam ser adaptados;
- aumenta muito o raio de impacto sobre partes que hoje funcionam.

Não é recomendada como primeira correção do FTP.

### Opção C — reescrita em PHP + shell + MariaDB

Não há benefício técnico demonstrado em reescrever o fluxo web em PHP neste momento. Pure-FTPd pode autenticar em MariaDB independentemente da linguagem da aplicação. Shell deve ficar restrito a integração operacional privilegiada e idempotente, nunca como fonte de estado.

Uma reescrita total adicionaria risco de segurança de senha, divergência de histórico, perda de idempotência e regressão no SSH/backup já estáveis.

## 7. Proposta de schema MariaDB para autenticação

A tabela de autenticação deve ser separada do histórico da aplicação e conter somente o necessário ao Pure-FTPd:

```sql
CREATE TABLE ftp_virtual_users (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    account_uuid CHAR(36) NOT NULL,
    username VARCHAR(64) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    uid INT UNSIGNED NOT NULL,
    gid INT UNSIGNED NOT NULL,
    home_directory VARCHAR(255) NOT NULL,
    upload_bandwidth INT UNSIGNED NULL,
    download_bandwidth INT UNSIGNED NULL,
    quota_files INT UNSIGNED NULL,
    quota_megabytes INT UNSIGNED NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    disabled_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ftp_virtual_users_uuid (account_uuid),
    UNIQUE KEY uq_ftp_virtual_users_username (username),
    KEY idx_ftp_virtual_users_enabled (enabled)
) ENGINE=InnoDB;
```

Regras:

- senha nunca em texto puro;
- usuário desativado permanece para auditoria, mas a consulta do Pure-FTPd filtra `enabled=1`;
- `account_uuid` é a chave estável de correlação com a aplicação;
- `version` permite atualização otimista e reconciliação;
- o usuário do MariaDB usado pelo Pure-FTPd terá somente `SELECT` na tabela/visão de autenticação;
- o usuário usado pela aplicação terá somente as permissões necessárias nessa tabela;
- o importador FTP não precisa acessar credenciais.

## 8. Diferenças SQLite → MariaDB que exigem adaptação

1. `INTEGER PRIMARY KEY AUTOINCREMENT` deve virar `BIGINT ... AUTO_INCREMENT`.
2. Booleanos devem usar `TINYINT(1)` com validação de domínio.
3. Timestamps textuais devem virar `DATETIME(6)` ou `TIMESTAMP(6)`, sempre tratados em UTC.
4. `GLOB` não existe; validações devem usar `CHECK`, expressão regular compatível ou validação da aplicação.
5. `ON CONFLICT ... DO NOTHING` deve virar `INSERT IGNORE` ou `ON DUPLICATE KEY UPDATE` conscientemente.
6. Funções como `datetime('now', ...)` precisam ser substituídas.
7. Índices parciais do SQLite não existem da mesma forma no MariaDB.

Os índices que hoje garantem uma conta e uma integração ativas por equipamento precisam ser implementados por coluna gerada anulável:

```sql
ALTER TABLE ftp_accounts
  ADD COLUMN active_equipment_id BIGINT UNSIGNED
    AS (CASE WHEN is_active = 1 AND deleted_at IS NULL THEN equipment_id ELSE NULL END) PERSISTENT,
  ADD UNIQUE KEY uq_ftp_active_equipment (active_equipment_id);

ALTER TABLE mikrotik_ftp_integrations
  ADD COLUMN active_equipment_id BIGINT UNSIGNED
    AS (CASE WHEN is_active = 1 THEN equipment_id ELSE NULL END) PERSISTENT,
  ADD UNIQUE KEY uq_integration_active_equipment (active_equipment_id);
```

A compatibilidade exata dessas expressões deve ser validada na versão de MariaDB escolhida antes da implantação.

## 9. Plano incremental de migração

### Fase 0 — congelar contrato e observar

- documentar estados e transições;
- adicionar métricas de contas divergentes, integrações órfãs e testes expirados;
- criar comando somente leitura de reconciliação;
- registrar baseline de quantidade e hashes lógicos por tabela;
- manter SQLite e PureDB como fontes atuais.

Critério de saída: nenhum estado desconhecido durante sete dias de observação.

### Fase 1 — laboratório MariaDB

- instalar MariaDB somente em laboratório;
- criar banco e usuários com privilégios mínimos;
- configurar Pure-FTPd SQL em porta/rede de laboratório;
- criar credencial sintética exclusiva;
- testar login válido, senha inválida, usuário desativado, chroot, upload, bloqueio de leitura e limpeza;
- não usar backups reais nem conta de produção.

Critério de saída: testes repetíveis e restauração validada.

### Fase 2 — escrita espelhada com outbox

- toda alteração de conta grava o estado da aplicação e um evento `ftp_account_sync_requested` na mesma transação SQLite;
- worker idempotente aplica o evento no MariaDB;
- confirmação atualiza `sync_status`, versão e data;
- falha mantém conta como não operacional e gera alerta;
- MariaDB ainda não autentica tráfego de produção.

Não se recomenda dual-write direto sem outbox, pois uma falha entre os dois commits recriaria exatamente a divergência atual.

### Fase 3 — leitura comparativa

- reconciliar diariamente SQLite/PureDB/MariaDB;
- comparar contas ativas, usernames, homes, versões e estados;
- nunca comparar ou registrar senha em texto puro;
- corrigir todas as divergências antes do corte.

### Fase 4 — canário

- selecionar um equipamento de laboratório, nunca o MK-BASE inicialmente;
- apontar somente esse fluxo para Pure-FTPd SQL;
- acompanhar autenticação, upload, importação e rollback;
- manter configuração PureDB pronta para reversão.

### Fase 5 — corte controlado

- janela de mudança aprovada;
- backup consistente de SQLite, PureDB, configuração Pure-FTPd e MariaDB;
- bloquear temporariamente criação/rotação de contas;
- reconciliação final com contagens e checksums;
- trocar backend do Pure-FTPd;
- testar uma conta sintética e depois um equipamento canário;
- liberar gradualmente.

### Fase 6 — estabilização

- manter rollback por período definido;
- monitorar falhas de login sem registrar senhas;
- validar restore em ambiente isolado;
- somente depois decidir se outras tabelas da aplicação devem migrar.

## 10. Rollback

O rollback deve ser testado antes do corte:

1. interromper alterações de contas;
2. restaurar a configuração PureDB anterior;
3. reconstruir `pureftpd.pdb` a partir da cópia validada;
4. reiniciar/recarregar Pure-FTPd;
5. validar conta sintética;
6. manter eventos da outbox para reprocessamento posterior;
7. não apagar registros MariaDB nem históricos SQLite durante rollback.

## 11. Segurança

- preferir rede privada, VPN ou FTPS quando o RouterOS suportar;
- restringir MariaDB a socket/rede administrativa e firewall;
- TLS entre Pure-FTPd/aplicação e MariaDB quando houver tráfego em rede;
- usuários MariaDB distintos por função;
- segredos fora do repositório, com rotação e permissões restritas;
- logs nunca devem conter senha, comando RouterOS com segredo ou hash reutilizável;
- backups do MariaDB devem ser criptografados e ter restauração testada;
- conta de teste deve ser temporária, marcada e removida/desativada ao final;
- shell privilegiado deve aceitar identificadores validados e nunca SQL ou comandos arbitrários.

## 12. Critérios de aceite

- conta excluída sempre fica inativa;
- exclusão/desativação da conta não deixa integração ativa silenciosamente;
- integração ativa sem conta operacional é detectada em uma reconciliação;
- integração inativa e histórico são preservados;
- senha inválida termina o teste imediatamente com mensagem clara;
- UI nunca faz polling infinito;
- criação, rotação, desativação e exclusão são auditadas;
- nenhuma senha aparece em HTML persistente, log, auditoria ou argumentos de processo;
- contagens e relações do histórico são idênticas antes e depois da migração;
- rollback é executável dentro da janela definida;
- SSH e backup continuam passando nos testes sem alteração funcional.

## 13. Decisão recomendada

1. aprovar as correções locais do FTP após homologação;
2. não alterar o MK-BASE até executar o procedimento de mudança e backup;
3. construir o laboratório da Opção A: Pure-FTPd + MariaDB somente para autenticação;
4. criar uma conta sintética de laboratório e testar o fluxo completo;
5. implementar outbox e reconciliação antes de qualquer canário;
6. reavaliar a migração completa do SQLite somente após estabilidade comprovada.

PHP não é necessário para a Opção A. A aplicação Python pode continuar controlando inventário, histórico e testes, enquanto o Pure-FTPd consulta o MariaDB diretamente. Isso preserva as partes estáveis e reduz o risco da mudança.
