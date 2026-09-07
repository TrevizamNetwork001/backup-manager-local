# Modernização consolidada da interface — 18/07/2026

## Escopo

Esta entrega aplica um padrão visual comum às páginas de equipamento, FTP, backups, agendamentos, execuções e relatórios. A referência funcional e visual foi `abas.png`; os PNGs de referência permanecem fora do versionamento.

## Equipamento

- Histórico com calendário funcional, filtros compactos, ações alinhadas e novo detalhe de backup.
- Diagnóstico com estado da conexão, credencial ativa e último diagnóstico sem ações duplicadas.
- Detalhes técnicos apresentados em modal, com datas no fuso da aplicação e textos operacionais.
- Configuração avançada reduzida a backup automático, integração FTP e agendamentos.
- Navegação e redirects preservam a aba selecionada.

## FTP

- Wizard de quatro etapas com conclusão funcional, validação de senha orientativa e confirmação moderna.
- Pasta remota opcional; ausência de pasta usa a raiz `/`.
- Conta existente abre a administração; ausência de conta direciona ao cadastro.
- Administração dividida entre configuração de recebimento, estado, alteração de senha e exclusão.
- Modais destrutivos e botões foram compactados; arquivos e histórico são preservados ao desvincular.

## Backups e agendamentos

- Histórico e detalhes de backup seguem a nova hierarquia de cartões e SVGs.
- Criação e edição de agendamento foram modernizadas e usam nomes operacionais.
- Frequência manual, diária e semanal possuem controles coerentes de horário e dia.
- Listagens agrupam ações secundárias, diminuindo ruído visual.
- Execuções e detalhes técnicos usam indicadores compactos e mensagens seguras.

## Relatórios

- Navegação compartilhada com SVGs, filtros compactos e opções avançadas recolhíveis.
- Visão geral com indicadores consolidados e gráfico diário legível.
- Backups e Equipamentos priorizam dados operacionais; campos técnicos ficam em detalhes.
- Armazenamento, FTP e Telegram usam indicadores semânticos e ícones de estado.
- Auditoria omite heartbeats vazios por padrão e transforma JSON em resumos curtos.
- Exportações mantêm CSV, XLSX e PDF com os mesmos filtros.
- A visão geral passou a seguir `visaogeral.png`, com linha temporal pontuada, eixo vertical, distribuição por método em rosca, alertas e ranking de falhas.
- O período oferece 1, 7, 10, 15, 20, 25 e 30 dias, além de datas personalizadas; equipamento, status e período são aplicados aos agregados.
- Comparações usam o período anterior equivalente e não dividem por zero quando uma série não possui sucessos ou falhas.

## Retenção e limpeza

- Indicadores, política global, manutenção, exceções por equipamento, relatório e lixeira receberam a nova hierarquia visual.
- Confirmações destrutivas ficam recolhidas e continuam exigindo os textos de segurança existentes.
- Restauração, exclusão individual, esvaziamento controlado e auditoria preservam o comportamento anterior.

## Central de Configurações

- Todas as abas usam SVGs, cards, tabelas e ações compactas no mesmo padrão visual.
- Geral oferece timezones brasileiros por região; Logo e Favicon foram removidos do formulário e o idioma é informação fixa em Português (Brasil).
- Usuários possui modal de cadastro; criação aceita seis caracteres com alerta de senha fraca, enquanto redefinição exige dez caracteres.
- HTTPS separa teste sem gravação de validação persistente, preserva domínio/e-mail válidos em falhas e apresenta certificado, portas 80/443 e Nginx.
- O domínio e contato históricos do certificado foram reconciliados com a instalação local do Certbot; renovação automática usa o timer do sistema.
- Backup e Retenção, Google Drive, Notificações, Atualizações e Backup da Configuração possuem resumos modernos e atalhos para os fluxos completos.
- Sistema segue `sistema.png`: CPU por `/proc/stat`, atualização a cada minuto, faixas de alerta, memória, serviços, rede, DNS, SQLite, migrações e armazenamento detalhado.
- A rosca de armazenamento é alimentada por sistema, backups, lixeira e espaço livre, com valores reais e legenda semântica.

## Acessibilidade e consistência

- SVGs decorativos usam `aria-hidden`.
- Botões mantêm rótulos textuais e áreas de clique proporcionais.
- Tabelas preservam cabeçalhos semânticos e estados vazios descritivos.
- Cores reforçam significado, mas os estados continuam identificados por texto e forma.
- Layouts possuem ajustes para telas menores.

## Arquivos alterados

- `backup_manager/app.py`
- `backup_manager/lifecycle_views.py`
- `backup_manager/report_views.py`
- `backup_manager/reports.py`
- `backup_manager/settings_center.py`
- `backup_manager/settings_views.py`
- `scripts/configure-https.sh`
- `static/app.css`
- `static/app.js`
- `static/settings.css`
- `tests/test_reports.py`
- `tests/test_app.py`

## Verificação

- compilação Python dos módulos alterados;
- `tests.test_app`: 56 testes aprovados na consolidação anterior;
- `tests.test_reports`: inclui regressão do gráfico sem resultados;
- testes de Configurações, Apresentação e Observabilidade aprovados;
- `git diff --check` sem erros;
- reinício e confirmação do estado ativo de `backup-manager-local.service`.

Antes de liberar em outro ambiente, recomenda-se executar a suíte completa e revisar manualmente as páginas com `Ctrl+F5` para invalidar CSS em cache.

## Fechamento da tela `/backups` — 20/07/2026

A listagem principal de backups foi alinhada à referência `aba-backup.png`, mantendo consultas e ações sobre dados reais:

- cabeçalho operacional, filtros principais e filtros avançados;
- indicadores de total, concluídos, falhas e importações manuais;
- tabela compacta com equipamento, método, status, duração, tamanho e acesso aos detalhes;
- endereços IP omitidos da apresentação da origem;
- SVGs próprios para indicadores, filtro, SSH, FTP e importação manual;
- resumo de métodos sempre exibe SSH, importação manual e FTP, inclusive com valor zero;
- cartão de exportações sem ícone no título, conforme a referência;
- barra horizontal de relatórios removida por decisão de interface;
- item Relatórios do menu lateral destacado ao acessar `/backups`;
- comportamento responsivo preservado para desktop, tablet e celular.

Validação final: compilação dos módulos Python, testes direcionados de aplicação e relatórios, `git diff --check` e serviço reiniciado em estado ativo.
