# Central de Configurações

A versão 1.0 concentra a administração em **Configurações** (`/settings`). A central não cria motores de backup nem substitui as telas operacionais; ela organiza preferências e fornece atalhos para as configurações consolidadas.

## Abas

- **Geral:** nome da instalação, empresa, timezone brasileiro, formatos, texto institucional, contato e página inicial.
- **Usuários:** perfis Administrador, Operador e Leitura, ativação e redefinição de senha.
- **Acesso e HTTPS:** validação do domínio, instalação opcional de Let's Encrypt e resumo seguro do certificado.
- **Backup e Retenção:** resumo e acesso aos agendamentos e políticas existentes.
- **rclone, Notificações e Atualizações:** estado atual e acesso às respectivas telas consolidadas.
- **Sistema:** informações administrativas do equipamento hospedeiro.
- **Backup da Configuração:** exportação e importação para migração.

Somente Administradores acessam a central. As ações POST usam token vinculado à sessão e são registradas na auditoria.

## Configurações gerais

Os formatos suportados são `DD/MM/AAAA` ou `AAAA-MM-DD` e horário de 24 ou 12 horas. O idioma permanece fixo em Português do Brasil. O timezone é selecionado por estado/região brasileira, incluindo exceções de Amazonas, Pará e Fernando de Noronha. Logo e Favicon não são editados nessa central.

As páginas iniciais disponíveis são Centro de Operações, Backups, Equipamentos e Relatórios. Uma rota inválida nunca é utilizada após o login.

## Perfis

| Perfil | Descrição |
|---|---|
| Administrador | Acesso completo. |
| Operador | Executa backups e administra equipamentos. |
| Leitura | Consulta informações e baixa backups. |

Os perfis usam o modelo simples existente. Não há matriz de permissões nem regras por recurso. Alteração de senha encerra as demais sessões do usuário; o administrador conectado não pode desativar ou rebaixar a própria conta.

O cadastro ocorre em modal. A senha inicial aceita no mínimo seis caracteres e exibe alerta quando curta; uma redefinição administrativa exige no mínimo dez caracteres.

## HTTPS opcional

Para HTTPS, informe domínio público e e-mail. **Testar sem salvar** consulta DNS, portas 80/443, certificado e Nginx sem alterar a configuração. **Salvar alterações** persiste somente após DNS e porta 80 válidos. Emissão malsucedida também não substitui domínio ou contato anteriormente válidos. O fluxo utiliza exclusivamente Let's Encrypt; certificados autoassinados não são criados.

O helper preserva a configuração Nginx anterior, emite o certificado, monta uma configuração candidata, executa `nginx -t` e somente então recarrega o serviço. Falha de validação restaura a configuração anterior. Renovação usa o certificado identificado pelo domínio.

A tela exibe domínio vinculado, e-mail administrativo, emissor, emissão, expiração, dias restantes, renovação automática e última renovação. Chave privada, cadeia e conteúdo do certificado nunca são retornados pela aplicação. O timer do Certbot cuida da renovação automática; quando o certificado ainda é válido, a operação informa isso sem alegar renovação inexistente.

## Backup da configuração

O arquivo JSON possui formato `backup-manager-configuration`, versão própria e indicador explícito de ausência de segredos. Inclui:

- preferências administrativas permitidas;
- grupos, fabricantes, POPs e ambientes;
- equipamentos, agendamentos e retenção;
- parâmetros não secretos de notificações e rclone;
- usuários e perfis.

Não inclui backups, uploads, rejeitados, lixeira, logs, caminhos de armazenamento, certificados, sessões, hashes de senha, credenciais SSH, tokens ou segredos OAuth.

Na importação, registros são conciliados por identificadores estáveis. Usuários novos recebem senha aleatória indisponível e precisam de redefinição administrativa. Notificações, destinos e políticas externas permanecem desabilitados até que seus segredos sejam reconfigurados.

Fluxo recomendado:

1. instalar a versão 1.0 no servidor de destino;
2. executar o Setup inicial;
3. importar o JSON em **Backup da Configuração**;
4. revisar equipamentos e agendamentos;
5. redefinir senhas de usuários migrados;
6. configurar novamente credenciais, tokens e HTTPS;
7. executar backups de validação.

## Informações do sistema

A aba Sistema mostra versão, sistema operacional, kernel, arquitetura, hostname, interfaces, DNS, endereços, Nginx, SQLite, última migração, CPU, memória, disco, tempo ligado e timezone. A CPU é amostrada diretamente de `/proc/stat`; a aba recarrega os indicadores a cada minuto.

CPU e memória usam verde abaixo de 50%, amarelo de 50% a 69,9% e vermelho a partir de 70%. A saúde dos serviços combina o snapshot operacional com Banco de dados e Telegram. O armazenamento apresenta capacidade, uso do sistema, backups, lixeira e espaço livre em uma rosca alimentada pelos valores reais.
# Configurações Telegram v1.1

As chaves `telegram_*_summary_*` controlam resumos; `telegram_backup_*`, compactação, tentativas, item preso e modo da API controlam documentos. `telegram_api_mode=local` nunca é ativado automaticamente. O timezone dos resumos é sempre o timezone geral da instalação.
