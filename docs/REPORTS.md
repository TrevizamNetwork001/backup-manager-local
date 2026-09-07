# Relatórios e operação

O módulo de Relatórios transforma os registros já existentes no Backup Manager Local em visões administrativas. Ele é estritamente de leitura: não agenda nem executa backups, não altera equipamentos e não modifica os motores de SSH, FTP, Lifecycle, rclone, storage, notificações ou atualização.

## Acesso

O menu **Relatórios** está disponível para todo usuário autenticado e contém:

- Visão Geral;
- Backups;
- Equipamentos (incluindo falhas);
- Armazenamento;
- FTP;
- Telegram;
- Auditoria;
- Exportações.

Administrador, operador e perfil de leitura podem consultar. A exportação exige a permissão já existente de download (`can_download`) ou administração (`can_admin`). Assim, administradores e operadores habilitados exportam; leitura sem download apenas consulta. Nenhuma página do módulo oferece exclusão ou alteração.

## Origem dos indicadores

As informações são calculadas em consultas agregadas sobre as tabelas existentes:

| Visão | Fontes |
|---|---|
| Backups e taxas | `backups`, `backup_job_runs`, `cloud_sync_items` |
| Equipamentos e continuidade | `equipment`, `backups`, `backup_job_runs` |
| Falhas | execuções, recebimentos FTP, itens de Lifecycle, backups e sincronizações |
| Armazenamento | metadados de backups/FTP/rclone e uso do filesystem configurado |
| FTP | `ftp_accounts`, `ftp_received_files` |
| Restaurações e auditoria | `audit_log` |
| Remoção pelo Lifecycle | `lifecycle_runs` |

Equipamento arquivado corresponde ao cadastro com `is_active = 0`. A taxa de sucesso considera arquivos disponíveis; falha considera registros falhos ou em quarentena. A seção de backups também apresenta execuções sem arquivo resultante, permitindo visualizar falhas, cancelamentos e itens em andamento.

O campo **Empresa** está presente como preparação para multiempresa. Na versão atual ele identifica o relatório exportado, mas não restringe registros, pois o modelo de dados ainda não associa registros a empresas.

## Interface, filtros e paginação

As abas compartilham navegação com SVG, cabeçalho, filtros compactos e hierarquia visual. Os filtros principais aparecem em uma única faixa; opções menos frequentes ficam em **Mais filtros**. O bloco oferece **Limpar filtros** e mantém controles de data nativos e funcionais.

Os relatórios aceitam período, empresa, equipamento, grupo, fabricante, POP e status. Backups e exportações também aceitam método (SSH, FTP, Manual ou rclone); auditoria aceita usuário e evento. Datas inválidas e identificadores não numéricos são descartados com segurança.

O filtro **Status** não aparece em Auditoria porque não participa daquela consulta. Os controles dessa aba priorizam período e equipamento; usuário e evento permanecem em **Mais filtros**.

As listagens usam consultas únicas com joins/CTEs e agregações para evitar N+1. A paginação pode mostrar 25, 50 ou 100 registros. Exportações respeitam os mesmos filtros, mas incluem todo o conjunto filtrado.

## Exportações

Em **Relatórios → Exportações**, selecione o conjunto (Backups, Equipamentos ou Auditoria) e o formato:

- **CSV:** UTF-8 com BOM para compatibilidade com planilhas; separador configurável entre ponto e vírgula, vírgula ou tabulação;
- **XLSX:** cabeçalho destacado, primeira linha congelada, filtro automático e largura calculada por coluna;
- **PDF:** título, identificação visual do produto, empresa, período, data de geração, resumo, tabela, rodapé e numeração de páginas.

Os downloads usam `Cache-Control: no-store`, `Content-Disposition: attachment` e `X-Content-Type-Options: nosniff`.

## Gráficos

Os gráficos são HTML/CSS e não carregam bibliotecas JavaScript. A Visão Geral mostra backups por dia com data brasileira, dia da semana, legenda, barras proporcionais e totais. Armazenamento mostra ocupação, espaço utilizado e espaço livre.

## Apresentação por aba

- **Visão geral:** seis indicadores consolidados com SVGs coloridos e gráfico diário;
- **Backups:** tabela principal reduzida a data, equipamento, método, status, duração e tamanho; grupo, POP, destino, operador e SHA-256 ficam em detalhes;
- **Equipamentos:** indicadores de continuidade, situação, último backup e falhas, com detalhes secundários recolhidos;
- **Armazenamento:** métricas agrupadas, barra de capacidade e ícone de nuvem para rclone;
- **FTP:** recebimentos, importações, rejeições e falhas; rejeição usa círculo com X e falha usa alerta;
- **Telegram:** envios, sucesso, falhas e ignorados, com falha representada por alerta;
- **Auditoria:** quatro colunas — data, responsável, evento e resumo — sem JSON ou identificadores técnicos na listagem;
- **Exportações:** formulário dedicado para conjunto, formato e separador.

## Auditoria operacional

Eventos `scheduler.executed` com `queued=0` são ocultados da consulta padrão para evitar que o heartbeat por minuto domine a listagem. Eles continuam disponíveis ao pesquisar explicitamente pelo evento.

O resumo converte dados persistidos em frases operacionais, por exemplo:

- “Alterou os dados do equipamento MK-TESTE.”;
- “Backup do equipamento MK-TESTE concluído com sucesso.”;
- “Alterou as configurações gerais do painel.”.

O registro original permanece no banco. A simplificação afeta apenas a apresentação do relatório.

## Operação e limites

- Indicadores refletem o banco e o filesystem no instante da consulta.
- Um backup sincronizado via rclone aparece como evento próprio no relatório.
- “Maior tempo sem backup” usa o backup disponível mais antigo entre os equipamentos filtrados; equipamentos que nunca tiveram backup são sinalizados como “Nunca”.
- Motivos de falha são os textos seguros já persistidos pelos motores. O relatório não abre arquivos nem consulta serviços externos.

## Validação

Execute a partir da raiz do projeto:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile backup_manager/*.py run.py
bash -n scripts/*.sh
git diff --check
```

Os testes de `tests/test_reports.py` cobrem CSV, XLSX, PDF, validação de filtros, paginação, permissões e relatórios vazios.
