# Redesenho de equipamentos, diagnóstico e FTP

## Objetivo

Este trabalho reorganiza as áreas de Histórico, Diagnóstico e Configuração avançada do equipamento, moderniza o fluxo de contas FTP e reduz controles duplicados.

A referência visual usada foi `abas.png`, com os quadros de Histórico, Diagnóstico, Configuração avançada e assistente de backup. A implementação preserva a arquitetura existente de abas e as permissões operacionais, aproximando hierarquia, cartões, filtros, modais, cores e espaçamentos da referência.

## Página do equipamento

### Histórico

- título e descrição simplificados;
- filtros visuais por período, método, status e nome de arquivo, incluindo seleção funcional por calendário;
- tabela preserva as ações e os backups do equipamento atual;
- ações possuem dimensões e alinhamento consistentes;
- detalhes do backup usam o novo cabeçalho, cartões com SVG e agrupamento entre equipamento, origem, tamanho e acesso;
- o botão **Gerenciar backup** abre o Histórico quando já existe configuração.

### Diagnóstico

- cartões para estado da conexão, credencial ativa e último diagnóstico;
- **Testar conexão** valida conexão SSH e autenticação da credencial ativa;
- o teste SSH fica somente no cartão **1. Estado da conexão**, sem ação duplicada no cartão da credencial;
- a aba permanece selecionada depois de formulários e redirecionamentos;
- dados do último teste usam o timestamp real da credencial, convertido para o fuso configurado;
- **Ver detalhes do diagnóstico** abre um modal próprio e não mistura dados de execução de backup;
- o cartão **2. Credencial ativa** segue a referência `abas.png`, mostrando nome, usuário SSH completo, porta, estado e data de criação;
- a senha nunca é exibida nesse cartão;
- credencial é gerenciada no cartão por **Editar**, **Substituir** e **Desativar**;
- ações e blocos redundantes foram removidos: Mais ações, Recebimento FTP, FTP Push não configurado, importação, agendamentos e detalhes da última execução de backup.

O diagnóstico testa SSH e credencial. Ele não representa um teste completo de FTP, armazenamento, agendamento ou execução real de backup.

> Estado: aba revisada e aprovada em 18/07/2026.

### Configuração avançada

- a aba concentra somente backup automático, integração FTP e agendamentos;
- os cartões duplicados de credencial e informações do MikroTik foram removidos; esses dados permanecem no Diagnóstico, Resumo geral e edição do equipamento;
- importação manual e zona de perigo foram removidas da aba por pertencerem a fluxos próprios;
- **Gerenciar agendamento** leva à seção Agendamentos de backup dentro da própria aba, sem abrir o Histórico;
- MikroTik sem integração abre diretamente o modal FTP Push;
- conta FTP ausente apresenta confirmação antes do cadastro;
- conta existente abre sua administração por **Gerenciar FTP**;
- agendamentos foram movidos do Diagnóstico e o cadastro abre em modal;
- formulários e modais respeitam as permissões de operador e administrador.

### Agendamentos

- listagem e formulários de criação/edição seguem o novo padrão visual;
- nomes técnicos foram traduzidos, como **Simulação de teste** no lugar de `dry_run`;
- execução manual, diária e semanal possuem textos operacionais;
- frequência semanal aceita um dia da semana por agendamento;
- horário é selecionado por um controle compacto;
- ações secundárias foram agrupadas para reduzir a quantidade de botões;
- listagem de execuções, indicadores e detalhes técnicos usam cartões compactos e SVGs proporcionais.

## Contas FTP

### Listagem

- cabeçalho, indicadores, filtros e tabela foram redesenhados;
- status da conta e da sincronização aparecem separadamente;
- equipamento e ambiente são agrupados;
- a ação principal é **Gerenciar conta**.

### Wizard de cadastro

O cadastro possui quatro etapas:

1. dados da conta;
2. usuário e permissões;
3. pasta e armazenamento;
4. revisão e conclusão.

A etapa final contém uma única ação, **Concluir cadastro**. Em caso de falha, o usuário retorna ao wizard com uma mensagem, em vez de cair silenciosamente na listagem.

Senhas entre 8 e 11 caracteres são aceitas com aviso de senha fraca. Senhas com 12 ou mais caracteres são indicadas como fortes. A aplicação e o helper administrativo usam o mesmo mínimo técnico de 8 caracteres.

### Pasta remota opcional

A migração `030_ftp_upload_subdirectory.sql` adiciona `ftp_accounts.upload_subdirectory`.

- vazio significa raiz remota `/`;
- um valor como `backups` resulta em `/backups`;
- são aceitos apenas letras, números, `_` e `-`, com até 64 caracteres;
- caminhos absolutos, `..` e múltiplos níveis arbitrários não são aceitos;
- a pasta é criada dentro da raiz isolada da conta pelo helper;
- o importador varre com segurança arquivos da raiz e das subpastas.

O diretório físico continua isolado em `ftp-incoming/accounts/<uuid>/incoming`. Para o cliente FTP, esse diretório aparece como `/`.

### Confirmação e detalhes da conta

- a credencial criada é apresentada como a conclusão do wizard;
- senha é exibida somente nessa resposta;
- o usuário pode voltar ao equipamento ou abrir a conta FTP;
- a página da conta mostra servidor, usuário, pasta, último upload, configuração e histórico;
- ações administrativas são separadas em estado da conta, regeneração de senha e exclusão/desvinculação.

### Gerenciamento da conta

- **Editar** abre um modal para origem permitida, pasta de salvamento, quota, limite de arquivos e observações;
- mudanças de pasta valem para os próximos envios e preservam arquivos já recebidos;
- se a preparação da pasta no servidor falhar, a configuração anterior é restaurada;
- **Desativar** usa botão compacto, confirmação e preserva arquivos e histórico;
- **Alterar senha** abre um modal próprio; a senha atual deixa de funcionar após a troca;
- **Excluir conta** abre uma confirmação compacta e informa que backups, arquivos e histórico serão preservados;
- os botões administrativos usam ícones SVG e rótulos curtos para evitar que símbolos e textos fiquem colados.

## Execuções de backup

A página de detalhes da execução foi modernizada com:

- status, método, duração e resultado;
- origem e métodos traduzidos para textos operacionais;
- identificadores preservados;
- mensagens claras na ausência de erros;
- log seguro dividido em etapas;
- retorno direto ao equipamento.

Essa página descreve uma execução de backup. Ela não deve ser usada como detalhe de um teste de conexão SSH.

## Decisões de interface

- controles duplicados são removidos quando já existe uma ação equivalente na mesma aba;
- dados técnicos ficam em detalhes ou modais, preservando a leitura principal;
- ícones são SVG inline, alinhados e proporcionais ao texto;
- ações destrutivas permanecem isoladas, compactas e com confirmação;
- redirects preservam equipamento e aba selecionados;
- datas apresentadas ao operador usam o fuso configurado pela aplicação.

## Arquivos principais

- `backup_manager/equipment_detail_views.py`: composição das abas, cartões e modais;
- `backup_manager/app.py`: páginas FTP, credenciais e detalhes de execução;
- `static/app.css`: componentes e responsividade;
- `static/app.js`: seleção persistente de abas, âncoras e abertura de diálogos;
- `backup_manager/ftp.py`: validação e persistência da subpasta;
- `backup_manager/ftp_provisioning.py`: provisionamento da conta;
- `backup_manager/ftp_pipeline.py` e `backup_manager/ftp_importer.py`: descoberta e importação em subpastas;
- `scripts/ftp-admin-helper.py`: criação e permissão da pasta remota;
- `migrations/030_ftp_upload_subdirectory.sql`: alteração de esquema.

## Implantação realizada

- migração 30 aplicada explicitamente;
- backup pré-migração criado pelo migrador;
- helper atualizado em `/usr/local/sbin/backup-manager-ftp-admin`;
- serviço `backup-manager-local.service` reiniciado após as alterações;
- Pure-FTPd verificado como ativo durante o diagnóstico do cadastro.

## Validação manual recomendada

1. Recarregar a interface com `Ctrl+F5`.
2. Abrir cada aba do equipamento e confirmar persistência após ações POST.
3. Testar conexão SSH e conferir horário no modal do diagnóstico.
4. Criar conta FTP usando `/` e usando uma subpasta.
5. Enviar arquivo para a subpasta e confirmar importação no Histórico.
6. Abrir, editar senha, desativar e excluir uma conta FTP.
7. Criar um agendamento pelo modal de Configuração avançada.
8. Importar manualmente um backup pelo modal.
