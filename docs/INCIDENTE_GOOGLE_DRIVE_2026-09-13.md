# Google Drive: recuperação da sincronização em 13/09/2026

## Diagnóstico

Os avisos de retry e falha durante a madrugada correspondiam a envios reais
via rclone para o destino Backup externo. O título “Sincronização simulada
falhou” é uma mensagem genérica incorreta para esse caminho e não prova que
houve simulação. Esse título de falha não foi alterado nesta intervenção.

Foram identificados nove itens com falha no dia, com três tentativas por item,
e erro persistido `RCLONE_COMMAND_FAILED`. Dois arquivos às 04h explicam os
quatro avisos de retry e os dois avisos finais nesse intervalo. Os dois backups
locais consultados constavam como disponíveis no banco.

Um teste de leitura do remote `meudrive` fora da restrição de rede do ambiente
confirmou `invalid_grant` ao renovar o token OAuth. A configuração continuava
existindo; o Google recusava a autorização. Não foi possível determinar se a
causa original foi expiração ou revogação. O usuário relatou o aviso de app em
modo de teste, mas sua relação causal com a expiração não foi confirmada.

## Recuperação realizada

1. O usuário acessou Backup externo → Conectar com Google, informou os dados
   do cliente OAuth existente e autorizou novamente a mesma conta.
2. O destino ficou ativo e verificado; novo teste de leitura confirmou acesso.
3. Os nove itens antigos continuavam com falha e precisavam de nova tentativa.
4. O usuário acionou Tentar novamente. A passagem do Histórico para a aba Fila
   é esperada: o worker processa os itens e o resultado retorna ao Histórico.
5. O usuário apresentou nove notificações de cópia externa concluída entre
   09h38 e 09h40, referentes a envios concluídos entre 09h37 e 09h39.

Os testes de diagnóstico não enviaram nem excluíram arquivos. A recuperação
da autorização foi feita pelo usuário; os reenvios foram acionados pelo painel.
Nenhuma credencial, token ou chave secreta integra este registro.

## Alteração solicitada no Telegram

O título de sucesso passou de “Cópia externa concluída” para
“Backup enviado ao Google Drive”. O evento `cloud.upload_completed` passou a
usar ✅ em vez do ícone amarelo padrão. Equipamento, arquivo, destino, pasta,
tamanho e horário permanecem no corpo da mensagem.

A mudança está em `backup_manager/notifications.py`, no catálogo do evento,
na composição do título e na escolha do ícone de envio. Mensagens já enviadas
não são editadas. O título é compartilhado pelos envios rclone, portanto
atualmente também aparecerá para outros provedores se forem configurados;
o ambiente investigado utiliza Google Drive.

## Validação e pendências

- `python3 -m unittest tests.test_rclone_backend -q`: 10 testes aprovados.
- `python3 -m unittest tests.test_notification_visual_format tests.test_notifications -q`: 10 testes aprovados.
- A expectativa do título no teste existente foi atualizada.
- Permanece pendente melhorar o título de falha que menciona simulação.
- Permanece pendente verificar a configuração OAuth no Google Cloud para
  determinar a causa da invalidação e reduzir o risco de recorrência.

## Procedimento se voltar a ocorrer

Consultar o erro do rclone sem expor credenciais. Se houver `invalid_grant`,
renovar a autorização no formulário Conectar com Google usando o cliente
existente. Os dados do cliente ficam no Google Cloud, em APIs e serviços →
Credenciais. Após autorizar, testar o destino e só então reenfileirar as falhas.
Não é necessário criar outro projeto ou apagar os backups para reconectar.
