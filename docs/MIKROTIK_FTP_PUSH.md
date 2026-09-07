# MikroTik FTP Push

O FTP Push é opcional e deixa o MikroTik gerar o backup e enviá-lo para uma
conta FTP exclusiva do equipamento. O servidor só considera o backup concluído
depois de duas leituras estáveis, validação do nome/extensão/tamanho e cálculo
do SHA-256. Arquivos inválidos permanecem em quarentena e recebem um estado
sanitizado (`invalid_file`, `incomplete`, `timeout` ou `failed`).

## Implantação

Na tela do equipamento MikroTik, escolha RouterOS v6 ou v7, `.backup`, `.rsc`
ou ambos, e modo manual ou diário. O modal mostra o template compatível e o
script de remoção. No primeiro provisionamento (e em cada rotação) a senha é
aleatória, exclusiva e exibida uma única vez; ela não é gravada em texto puro,
logs ou respostas posteriores.

No terminal do MikroTik: copie o script, cole, aguarde a execução e volte ao
Backup Manager. Em modo manual execute `backup-manager` quando necessário. Em
modo diário, o scheduler gerenciado também usa o nome `backup-manager`.

## Teste e estados

Use **Testar envio FTP** depois que a instalação SSH estiver válida. O servidor
abre uma operação temporária, envia um pequeno `.rsc` ao MikroTik por SFTP e o
executa com `/import`; esse comando gera o arquivo de teste e o envia por FTP.
A expectativa expira em 15 minutos. Estados da operação: `pending`, `running`,
`waiting_upload`, `validating`, `validated`, `failed`, `expired` e `cancelled`.

O sucesso exige integração, conta, diretório, nome esperado, extensão, tamanho e
arquivo estável corretos. Path traversal, sobrescrita conflitante, arquivos
vazios, extensões inesperadas e arquivos acima do limite são rejeitados. O
arquivo recebido primeiro vai para a área temporária e só é validado depois das
leituras estáveis; o artefato efêmero do teste é removido ao final.

## Segurança e operação

FTP não possui criptografia. Use rede privada ou VPN; não exponha a porta à
Internet. A conta é chrootada no diretório do equipamento e não acessa outros
backups. O diretório e a disponibilidade do serviço FTP continuam sendo uma
dependência operacional administrada pelo serviço FTP existente.

Para alterar a senha, use **Rotacionar credencial**, reinstale o script no
MikroTik e guarde a nova senha. **Desativar integração** desativa a conta e
oferece um script que remove somente o script e o scheduler `backup-manager`.

O script de instalação v5.8 detecta a versão completa antes do preflight, da
transferência SFTP ou de qualquer alteração remota. RouterOS abaixo de 6.43 é
bloqueado; versões 6.43–6.49.x usam o template v6; versões 7.0 ou superiores
usam o template v7. Versões não reconhecidas também são bloqueadas sem enviar
arquivos. Ambos os templates evitam `show-sensitive` e exportam configuração
compacta sem dados sensíveis por padrão.

A revisão v5.1 remove `:tolower`, indisponível no RouterOS 6.49.6, e não faz
normalização textual no equipamento. O nome cadastrado é sanitizado no servidor
antes da geração e inserido como literal seguro (`mk-teste`, por exemplo). Isso
reduz a sintaxe executada no RouterOS e funciona em toda a faixa 6.43–6.49.x;
instalações v5 são detectadas como desatualizadas pelo preflight.
Quando o preflight encontra uma versão anterior, o modal não trata o resultado
como falha: apresenta **Atualização disponível** e exige a ação explícita
**Aplicar atualização v&lt;versão atual&gt;** antes de substituir os objetos gerenciados.
O número exibido no modal vem da mesma constante usada para gerar o script,
evitando que a interface apresente uma revisão antiga.

A revisão v5.3 inclui a política `sensitive` no script e no scheduler. O
RouterOS exige essa permissão para gerar e acessar o arquivo binário `.backup`;
sem ela, a execução termina com `not enough permissions (9)`. O preflight
considera schedulers sem essa política desatualizados e oferece a reparação.
A revisão v5.4 também inclui `policy`, exigida pelo RouterOS 6 ao executar o
objeto por sua própria ação no Winbox. Sem ela, o mesmo script pode funcionar
no terminal herdando os direitos do usuário e falhar pelo botão **Run Script**.
A revisão v5.5 introduziu limites explícitos no `/tool fetch`. Eles foram
retirados do template RouterOS v6 na revisão v5.7 após causarem execuções presas
em equipamentos v6; nesse template o comando voltou à forma compatível que já
era usada nas transferências validadas.
A revisão v5.6 publica em `backupManagerFtpLastBase` o nome-base exato da última
execução. A confirmação do servidor aceita somente `.backup`/`.rsc` com esse
nome e ID de upload posterior ao início, impedindo que um arquivo antigo atrasado
faça uma execução nova chegar incorretamente a 100%.
A revisão v5.8 grava uma assinatura não secreta da configuração no objeto
gerenciado. Alterações no nome do equipamento, destino FTP, usuário, diretório ou
formato passam a marcar o script como divergente e a instalação guiada oferece a
reparação, mesmo quando a versão do modelo continua igual.
Em falhas de reparo, o modal preserva o percentual da etapa alcançada e mostra a
mensagem sanitizada. Divergências após importação informam estado, quantidade de
scripts/schedulers e qual validação falhou, sem expor o conteúdo do script.

O teste FTP temporário da v5 usa `/export file=`, comando tradicional disponível
em toda a linha RouterOS 7 suportada, em vez de depender de `/file add`. A tela
do equipamento lê a versão diretamente do gerador e apresenta a compatibilidade
mínima, evitando divergência entre a interface e o script instalado.
O arquivo esperado passa a usar a extensão `.rsc`; arquivos `.txt` do teste v4
continuam reconhecidos temporariamente para não invalidar operações em andamento.

## Assistente web de instalação v5.8

Na tela do equipamento, **Instalação guiada via SSH** abre um wizard que mostra
o MikroTik de destino (nome e endereço), as etapas e o percentual de progresso.
O operador pode solicitar um backup completo depois da instalação. Nesse modo,
o servidor executa o objeto gerenciado `backup-manager` via SSH. O fim do comando
confirma apenas o envio no MikroTik; a barra permanece entre 92% e 98% com
**Aguardando confirmação do servidor FTP**. O sucesso só é informado quando o
importador registra e valida todos os tipos esperados (`backup`, `rsc` ou ambos)
em uma nova operação posterior ao início da execução.

A lista fixa do modal apresenta apenas as ações administráveis: verificar os
objetos existentes, instalar script/scheduler e validar a instalação. Conexão
SSH e detecção do RouterOS continuam ocorrendo antes de qualquer alteração, mas
só aparecem no resultado quando forem relevantes para o diagnóstico.

Durante a operação, a barra mostra o percentual estimado e a etapa atual. Em
sucesso, a barra e as etapas concluídas ficam verdes. Em falha, o avanço para no
ponto alcançado, a barra fica vermelha e o modal permanece aberto. O botão passa
de **Cancelar** para **Fechar**, permitindo que o operador leia a orientação e
corrija a configuração antes de tentar novamente.

Ao atingir 100% com sucesso, o modal de instalação mantém a confirmação visível
por três segundos e fecha automaticamente. Esse tempo começa somente depois da
validação final — e, quando selecionado, depois de o backup completo chegar e
ser validado no servidor FTP.
Ele não reduz nem mascara o tempo real da operação, que pode levar cerca de 15
segundos ou mais conforme conexão, RouterOS e transferência. Falhas e estados
que exigem reparação nunca fecham automaticamente.

Falhas de conexão, autenticação, versão incompatível, transferência, importação,
validação ou execução são mostradas no próprio wizard. Quando o RouterOS devolve
um erro de execução, a aplicação o captura, remove dados sensíveis e persiste o
resultado sanitizado na auditoria; credenciais e comandos com segredo não são
expostos na página nem nos logs.

As falhas SSH incluem contexto operacional seguro. Conexão recusada informa
endereço e porta e recomenda conferir porta, serviço SSH e firewall; timeout
recomenda conferir endereço, rota, porta e firewall; autenticação recusada
informa o usuário e recomenda conferir usuário/senha. Senhas nunca entram na
mensagem. RouterOS incompatível informa a versão detectada e a mínima suportada.
A explicação aparece dentro do bloco da própria barra, logo abaixo do título da
falha, e também no registro detalhado da operação.

Os códigos internos são traduzidos na interface: `done` e `completed` aparecem
como **Concluído**, `validated` como **Validado/Aprovado**, `installed_valid`
como **Instalação válida**, `pending` como **Pendente** e `failed` como
**Falhou**. Os códigos permanecem inalterados na API e no banco.

Quando uma reparação é aplicada, o resultado compara o preflight anterior com
a validação final e lista as correções identificadas, como **script atualizado**,
**scheduler atualizado** ou **objetos duplicados removidos**. Uma verificação
sem divergências continua registrando **Alterações: nenhuma**.

Quando o equipamento já possui uma conta FTP comum, mas ainda não possui a
integração MikroTik FTP Push, a aba **Configuração avançada** mostra o cartão
**Backup no MikroTik — Não configurado**. A ação **Configurar FTP Push** permite
reutilizar a conta existente ou rotacionar sua senha; após o vínculo, o mesmo
cartão passa a oferecer a instalação guiada v5.

Ao salvar a configuração, o sistema retorna ao equipamento e mantém a área de
configuração como contexto de continuidade. A antiga página intermediária que
exibia automaticamente o script completo foi removida desse caminho; a geração
manual continua disponível como ação explícita no cartão da integração.
O cadastro do FTP Push também usa um assistente de três etapas — conta FTP,
RouterOS/execução e destino/confirmação — com progresso visível e validação por
etapa, substituindo o formulário longo legado.
O cartão do equipamento segue o padrão compacto da integração FTP: título, selo
de estado, descrição curta e botão **Configurar/Gerenciar backup**. Os dados
técnicos ficam no assistente; o teste FTP só aparece depois de uma instalação
válida e a geração manual permanece em opções avançadas.

## Operações com múltiplos artefatos (opt-in)

A migration 028 adiciona um modelo isolado para uma operação agregar
artefatos esperados, obrigatórios ou opcionais. Cada artefato possui estado,
tamanho, SHA-256, resultado sanitizado de validação, deadline e vínculo opcional
com um backup já armazenado. A operação pode ser reduzida para
`waiting_upload`, `validating`, `success`, `failed` ou `expired` a partir dos
estados dos artefatos obrigatórios.

No redutor desta fundação, um artefato obrigatório classificado como
`duplicate` encerra a operação como `failed`. A decisão é conservadora e
consciente: sem a futura correlação transacional da Fase 1B, a fundação não
presume que um conteúdo duplicado possa satisfazer a expectativa atual.
Artefatos opcionais ausentes não impedem `success`.

O modelo permanece desabilitado por padrão com
`backup_artifacts_v2_enabled=0`. Nesse estado, importador, deadlines,
notificações e registros continuam no fluxo legado e nenhuma operação ou
expectativa nova é criada. Não há backfill.

Quando a flag vale `1`, o primeiro arquivo reconhecido cria de forma idempotente
a operação e todas as expectativas configuradas: `backup`, `rsc` ou ambas. O
servidor não conhece antecipadamente cada execução recorrente do scheduler do
RouterOS; portanto este é o primeiro ponto em que o FTP push permite criar a
expectativa. Em `both`, o primeiro artefato válido não conclui a operação.

Scripts v5.8 novos usam nomes legíveis no formato
`<identidade>.<AAAA-MM-DD-HH-MM-SS>.<sequência>.<tipo>`, por exemplo
`mk-teste.2026-07-19-12-27-11.1.rsc`. A conta FTP e a integração fornecem o
namespace interno; data, hora e sequência monotônica distinguem as execuções.
A chave persistida combina a integração e o identificador validado. O bloqueio
impede execuções simultâneas no mesmo equipamento.

Para RouterOS 6.x e 7.x, a configuração recomendada é **`.backup e .rsc`**. Os
dois arquivos usam exatamente a mesma base e sequência, variando somente a
extensão; assim o importador consegue correlacionar, validar e concluir o par sem
depender do nome técnico longo usado pelas versões anteriores. O v6 exporta com
`/export compact`; o v7 usa `/export`.

Em alguns equipamentos RouterOS 6, arquivos enviados por SFTP para a raiz são
expostos pelo subsistema de arquivos com o prefixo `disk/`. A instalação SSH
valida primeiro o nome na raiz e, quando necessário, tenta `disk/<arquivo>`;
o caminho efetivamente encontrado também é usado na importação e na limpeza.
Como o índice de `/file` do RouterOS 6 pode atualizar depois do término do SFTP,
o instalador repete essa validação por até três tentativas com intervalo curto.

Nomes legados continuam aceitos e são correlacionados pela integração mais a
data/hora exata presente no nome. Como o formato antigo não possui identificador
de execução, duas execuções históricas com o mesmo segundo não podem ser provadas
distintas: o servidor as trata conservadoramente como a mesma operação; hash
igual é retry e hash diferente é conflito, nunca mistura silenciosa. Um par
incompleto aguarda até o deadline e depois expira; chegada posterior fica `late`.
A correlação forte com sequência vale somente para scripts novos.

Cada artefato válido recebe seu próprio registro em `backups`. A estratégia de
notificação é por artefato liberado após o sucesso agregado: em `both`, Cloud,
Telegram e `backup.created_from_ftp` recebem dois itens idempotentes, um para
`.backup` e outro para `.rsc`, somente na transição da operação para `success`.
Retries não reenfileiram. Conteúdo idêntico em outra operação é um novo artefato
`valid`, com operação, backup lógico, timestamps, retenção e auditoria próprios.
Mesmo tipo e operação com hash divergente é conflito `suspicious` e não
sobrescreve SHA-256, vínculo ou arquivo válido anterior.

Um artefato recebido depois do deadline é validado e pode ser armazenado como
`late`, mas a operação original permanece `expired`, sem sucesso retroativo ou
ações de conclusão. Arquivos `invalid` ou `suspicious` seguem para rejeitados e
nunca são colocados entre backups válidos.

Uma interrupção normal depois do rename e antes do vínculo SQLite é compensada
por savepoint e retorno ao diretório `processing`. Uma finalização abrupta do
processo pode deixar um arquivo nesse diretório; o registro permanece com status
`processing` e fornece diagnóstico, mas ainda não existe recuperação automática
desse diretório. Implementar um reconciliador idempotente de `processing` é dívida
técnica explícita; até lá o arquivo não é considerado válido nem vinculado como
backup e requer intervenção operacional controlada.

Os validadores iniciais de `.rsc` e `.backup` são funções locais sem acesso a
banco, rede ou comandos externos. Eles retornam somente estado, código,
mensagem sanitizada, tamanho, SHA-256 e metadados não sensíveis; o conteúdo do
artefato nunca integra o resultado. O `.rsc` é validado e armazenado sem qualquer
alteração. RouterOS v6 usa `/export compact`; RouterOS v7 usa `/export`, ambos
sem `show-sensitive`.

Datas persistidas seguem o padrão UTC textual já usado pelo projeto
(`YYYY-MM-DD HH:MM:SS`). O redutor exige objetos `datetime` com timezone e os
normaliza para UTC antes de comparar o deadline.

## Retorno do teste na interface

O botão **Testar envio FTP** inicia o comando no MikroTik e acompanha a operação
por consultas periódicas ao servidor. A execução ocorre em um modal temporário
com barra, percentual e estas etapas: teste iniciado, arquivo criado no MikroTik,
comando FTP executado, arquivo recebido, arquivo validado, limpeza e conclusão.

Quando todas as etapas terminam, o modal chega a 100%, mostra **Aprovado** e
fecha automaticamente depois de três segundos. Em falha ou expiração, permanece
aberto, usa destaque vermelho, preserva o percentual alcançado e mostra causa e
recomendação sanitizadas; o operador deve fechá-lo explicitamente.

### Estados dos botões do teste FTP

O botão **Testar envio FTP** da página apenas abre o modal; abrir o modal não
cria operação nem chama o MikroTik. Dentro do modal, o botão de execução funciona
como confirmação visual contra cliques duplicados:

- ao abrir o modal sem teste ativo: estado **Pronto**, progresso em 0% e botão
  verde **Iniciar teste FTP**, habilitado;
- imediatamente após o primeiro clique: botão vermelho **Teste em execução…**,
  desabilitado e marcado como ocupado (`aria-busy=true`);
- enquanto o backend executa e aguarda o arquivo: permanece vermelho,
  desabilitado e visível, evitando que o operador clique duas ou três vezes por
  achar que a ação não começou;
- em falha ou expiração: o modal permanece aberto e oferece o botão verde
  **Tentar novamente**;
- ao clicar em **Tentar novamente**: esse botão também muda para vermelho,
  mostra **Teste em execução…** e fica bloqueado até o novo resultado;
- em sucesso: o botão de repetição é ocultado, o modal mostra **Aprovado** em
  100% e fecha automaticamente após três segundos.

Somente uma tentativa pode ficar ativa por integração. Além do bloqueio visual,
o backend rejeita a criação de outro teste enquanto existir uma operação nos
estados `pending`, `running`, `waiting_upload` ou `validating`.

O log detalhado do último teste não permanece expandido na tela do equipamento.
A tela mantém apenas o resumo operacional (estado, horário e mensagem), enquanto
o histórico completo continua persistido no banco e na auditoria. Se a página
for recarregada durante um teste ainda ativo, o modal reabre e retoma as consultas.

O teste temporário cria e envia somente um `.rsc`; ele valida geração de arquivo,
autenticação FTP, transferência, recebimento e processamento no servidor. Ele
não substitui a execução do script permanente, que pode gerar `.backup`, `.rsc`
ou ambos conforme a configuração.

Ao final, a rotina remove tanto o `.rsc` gerado pelo comando de teste quanto o
arquivo temporário usado no `/import`. No RouterOS 6, arquivos enviados por SFTP
podem aparecer como `disk/<nome>`; a limpeza tenta explicitamente o nome na raiz
e com o prefixo `disk/`, inclusive em falhas. Esses arquivos são efêmeros e não
devem permanecer no MikroTik.

## Fluxo operacional completo e responsabilidade do agendamento

O FTP Push gerenciado usa o próprio MikroTik como origem da execução diária. A
instalação guiada grava no RouterOS dois objetos com o mesmo nome gerenciado:

- `/system script` `backup-manager`: cria os artefatos configurados, envia-os ao
  FTP e faz a limpeza local;
- `/system scheduler` `backup-manager`: executa `/system script run
  backup-manager` diariamente no horário escolhido quando o modo é agendado.

O fluxo recorrente não depende de uma conexão SSH iniciada pelo servidor:

```text
Scheduler do RouterOS
→ script backup-manager
→ criação de .backup e/ou .rsc
→ envio ao servidor FTP
→ importação e validação pelo Backup Manager
→ armazenamento por equipamento
→ limpeza dos arquivos temporários no MikroTik
```

SSH é usado para detectar o RouterOS, instalar/reparar os objetos, executar uma
validação completa sob demanda e retornar erros administrativos. Depois da
instalação, o scheduler do RouterOS continua funcionando mesmo sem uma sessão
SSH aberta pelo Backup Manager.

Não crie um agendamento SSH adicional no painel geral para um MikroTik que já
usa FTP Push agendado. Os dois agendamentos executariam o mesmo objetivo por
caminhos diferentes e poderiam gerar backups duplicados. O painel geral agenda
somente backups no modelo tradicional em que o Backup Manager inicia a operação
via SSH; no FTP Push, horário e ativação pertencem ao scheduler instalado no
RouterOS.

Para conferir os objetos diretamente no MikroTik:

```routeros
/system script print where name="backup-manager"
/system scheduler print detail where name="backup-manager"
```

Em modo diário, o scheduler esperado apresenta `interval=1d`, o horário
configurado, `disabled=no` e `on-event=/system script run backup-manager`. Em
modo manual o objeto pode permanecer instalado com `disabled=yes`; nesse caso a
execução ocorre apenas por ação explícita.

Na web, depois da configuração, o operador normalmente apenas acompanha os
arquivos recebidos, histórico e diagnósticos. Use as ações gerenciadas quando
precisar testar o FTP, trocar credencial, alterar formato/horário, reparar o
script ou remover a integração.

### Validação de referência em RouterOS v6 e v7

O modelo v5.8 foi validado de ponta a ponta em RouterOS v6 e v7: conexão SSH,
instalação, geração de `.backup` e `.rsc`, envio FTP, recebimento, importação,
existência física no armazenamento e limpeza local. No RouterOS v7, mensagens
como `Upload to <servidor> FINISHED` são saída nativa do `/tool fetch` e indicam
sucesso; as mensagens prefixadas por `backup-manager:` pertencem ao script
gerenciado.

O modal de instalação só chega a 100% depois da confirmação exata dos artefatos
no servidor. Os três segundos finais servem apenas para manter o sucesso visível
antes de fechar; não substituem nem antecipam a validação FTP.

## Onboarding de um MikroTik novo

Ao cadastrar um equipamento com o driver **MikroTik RouterOS**, o wizard pergunta
qual fluxo será usado. As opções são mutuamente exclusivas:

- **Backup via SSH — agendado pelo Backup Manager**;
- **FTP Push — agendado pelo MikroTik**.

Nos dois caminhos, o cadastro cria o equipamento ativo e a credencial SSH
criptografada, exige a validação da conexão e preserva o equipamento se uma etapa
posterior falhar. O operador pode corrigir os dados e continuar sem cadastrar o
equipamento novamente.

No caminho SSH, depois da validação o wizard abre o formulário de agendamento já
preenchido com método SSH e frequência diária. Ao salvar, cria apenas o job do
Backup Manager, enfileira um primeiro backup e abre o acompanhamento da execução.
Conta FTP, script e scheduler do RouterOS não são criados nesse caminho.

No caminho FTP Push, depois da validação o wizard abre a configuração vinculada
ao próprio equipamento, sem pedir uma nova seleção. Ele cria ou reutiliza a conta
FTP, define formato/horário e segue automaticamente para a instalação SSH. O
modal instala o script e o scheduler no RouterOS, executa um backup completo e
aguarda os artefatos exatos no servidor antes de concluir.

Essa bifurcação evita que o mesmo MikroTik receba simultaneamente um job SSH no
painel e um scheduler FTP Push no RouterOS. Fora do onboarding, as telas
administrativas continuam disponíveis para reparação, alteração e diagnóstico.

## Referência para o menu Gerenciar backup

O novo layout de **Gerenciar backup** deve preservar os contratos operacionais
descritos neste documento, mesmo que cartões, abas ou ordem visual mudem:

- instalação e reparação são operações distintas do teste FTP;
- a instalação mostra versão do script, equipamento de destino, progresso e
  correções aplicadas;
- falhas permanecem abertas e exibem causa provável e orientação segura;
- o teste FTP usa modal temporário, bloqueio contra cliques repetidos e
  fechamento automático apenas em sucesso;
- a tela pode mostrar um resumo do último teste, mas não deve recolocar o log
  detalhado persistente no fluxo principal;
- credenciais, source completo do script e comandos com senha nunca devem ser
  exibidos em mensagens, timelines ou logs visuais;
- ações manuais, rotação de senha e remoção continuam em opções avançadas;
- os códigos internos da API permanecem estáveis e são traduzidos apenas na
  apresentação.

Ao implementar o novo layout, mantenha os atributos usados pelo JavaScript
(`data-mikrotik-operation`, `data-operation-result`, `data-operation-progress`,
`data-retry-ftp`, `data-install-wizard-form` e `data-dialog-close`) ou atualize
os seletores e seus testes na mesma alteração.

## Diagnóstico de permissão no RouterOS 6

Um comando `/system backup save` pode funcionar no terminal e o mesmo conteúdo
falhar pelo botão **Run Script** com `not enough permissions (9)`. O terminal
herda os direitos do usuário conectado; a ação do objeto usa as políticas do
script. Na v5.4, script e scheduler são instalados com:

```text
policy=ftp,read,write,policy,test,sensitive
```

Para diagnosticar sem expor o conteúdo do script, confira os objetos e o grupo:

```routeros
/system script print detail where name="backup-manager"
/system scheduler print detail where name="backup-manager"
/user print detail where name="USUARIO"
/user group print detail
```

O parâmetro `use-script-permissions` existe em versões mais novas do RouterOS,
mas não é aceito por toda a linha 6.x. O teste definitivo para esse cenário é
executar pelo terminal e pelo botão **Run Script**, comparar os logs e conferir
as políticas efetivamente instaladas. Saídas de `print detail` podem conter a
senha FTP dentro do source; antes de compartilhar o resultado, oculte a senha e,
se ela for exposta, rotacione a credencial no Backup Manager.
