# Repositório remoto de atualizações

A FASE 11.2 acrescenta consulta e download explícito à atualização local da FASE 11.1. Ela não implementa atualização automática. O check lê `updates.json` e `updates.json.sig`, valida Ed25519, schema fechado, produto, canal e compatibilidade, e persiste somente a release elegível mais nova. O timer apenas consulta o índice.

## Configuração e canais

As chaves `update_repository_enabled`, `update_repository_url`, `update_channel`, `update_check_interval_hours` e os campos `update_last_check_*`/`update_available_*` ficam em `settings`. O padrão é desabilitado, canal `stable` e 24 horas. `stable` nunca oferece pré-release; `rc` considera stable e rc. Versão igual, downgrade, incompatível ou deprecated são ignorados. `mandatory` e `security_update` são apenas indicadores.

A URL deve ser HTTPS, sem credenciais, localhost ou IP literal. DNS é resolvido e todos os endereços precisam ser globais. Cada redirect é revalidado e são aceitos no máximo três. Uma allowlist opcional restringe hosts. `update_repository_lab_mode=1` libera endereços não globais/IP literal exclusivamente para laboratório isolado. Certificados TLS são verificados normalmente. Proxy do ambiente é deliberadamente ignorado; proxy HTTPS administrado fica para fase futura.

## Assinatura e fluxo

`updates.json.sig` assina os bytes JSON canônicos (UTF-8, chaves ordenadas, separadores compactos). Nesta versão, a mesma chave pública do `.bmu` valida índice e pacote. O `.sig` destacado do pacote assina os 64 caracteres ASCII do SHA-256 hexadecimal. Depois de tamanho, hash e assinatura externa, o motor local revalida integralmente a assinatura interna do `.bmu`.

`python3 -m backup_manager.cli update-check` retorna `UP_TO_DATE`, `UPDATE_AVAILABLE <versão>` ou `CHECK_FAILED <código>`. **Baixar atualização** apenas cria um item `queued`; o worker move atomicamente um pacote válido a `updates/incoming`, cria `update_operation` e o deixa `ready`. Parciais são apagados em falha/cancelamento; não há retomada por Range nesta fase. Aplicação continua sem rede e exige `ATUALIZAR PARA <VERSAO>`.

Auditoria e UI não expõem query string, headers, assinatura, conteúdo, token, caminho interno ou stack trace. Para recuperar, corrija configuração/rede, faça novo check e enfileire novamente.
