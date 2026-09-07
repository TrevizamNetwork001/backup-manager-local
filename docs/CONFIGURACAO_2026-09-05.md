# Configuração e validação — 05/09/2026

## Google Drive

Foi configurada a integração OAuth do Google Drive pelo painel do Backup
Manager.

- Projeto Google Cloud: aplicação `Backup Manager`.
- API ativada: **Google Drive API**.
- Cliente OAuth: tipo **Aplicativo da Web**.
- Conta autorizada: `cristrevizam@gmail.com`.
- Escopo concedido: `https://www.googleapis.com/auth/drive.file`.
- URL de retorno autorizada:
  `https://backup.trevizamnetwork.com.br/cloud/rclone/google/callback`.
- Remote rclone: `meudrive`.
- Estado validado: **verified**.
- A chave secreta e os tokens permanecem protegidos no servidor; não registrar
  esses valores nesta documentação.

O token antigo estava expirado e foi renovado pelo fluxo OAuth do painel. O
remote foi testado com sucesso pelo rclone e respondeu ao Google Drive.

## Organização dos backups

Novos backups enviados ao Google Drive usam o nome da instalação como pasta
raiz, seguido do equipamento e da data do recebimento:

```text
PEDRAS-TELECOM/
└── CE-VPN/
    └── 05-09-2026/
        └── ce-vpn_20260905140806_mikrotik_routeros.rsc
```

O sistema sanitiza espaços e caracteres especiais, por isso `PEDRAS TELECOM`
é criado como `PEDRAS-TELECOM`. O grupo do equipamento continua disponível
para filtros e políticas, mas não cria uma pasta intermediária.

O agendamento define quando o backup é executado. A pasta de data representa o
dia em que o arquivo foi recebido. O backup local continua sendo preservado.

## Validação realizada

Foi executado um backup real do equipamento `CE-VPN`:

- arquivo local criado: `ce-vpn_20260905140806_mikrotik_routeros.rsc`;
- backup local: disponível;
- cópia Google Drive: estado **synced**;
- caminho remoto confirmado:
  `meudrive:PEDRAS-TELECOM/CE-VPN/05-09-2026/`;
- arquivo listado no Google Drive com o mesmo nome.

## Telegram

O aviso de conclusão já estava funcionando. Durante a configuração da cópia de
arquivos, o destino existente foi reaproveitado porque o chat e o tópico já
estavam cadastrados.

Destino configurado:

- nome: `Backup Telegram`;
- tipo: **Supergrupo**;
- tópico padrão: `1411`;
- arquivos de backup: habilitado;
- alertas: habilitado;
- destino ativo: sim.

Foi criada uma política global ativa para todos os equipamentos:

- novos backups: envio automático;
- SSH: incluído;
- FTP: incluído;
- manual: incluído;
- todos os tipos de arquivo: incluídos;
- compactação: nenhuma.

O aviso textual e a cópia do arquivo são funções independentes. A cópia de
arquivo aparece no Telegram como documento para download.

## Acompanhamento

- Google Drive: `/cloud`.
- Telegram, destinos: `/telegram-backup?view=destinations`.
- Telegram, políticas: `/telegram-backup?view=policies`.
- Telegram, fila: `/telegram-backup?view=queue`.

O worker processa as filas automaticamente. Após executar um backup, aguarde
até um minuto e confirme o estado **Sincronizado** no Google Drive ou **Enviado**
na fila do Telegram.

## Observações

- Horários gravados no banco usam UTC; a interface converte para
  `America/Sao_Paulo`.
- A mensagem `redirect_uri_mismatch` foi resolvida cadastrando a URL de retorno
  exata no cliente OAuth.
- O erro de destino Telegram duplicado ocorreu porque o chat `-1002726015554`
  e o tópico `1411` já existiam; não era necessário criar outro destino.

## OLT Huawei — validação automática

A OLT `OLT-HUAWUEI-BASE` (`172.21.254.106`) foi cadastrada com o método
`Huawei — OLT (SSH + FTP)`. A credencial SSH foi testada com sucesso e a conta
FTP `olt_base` foi vinculada ao equipamento.

A senha FTP da Huawei deve usar somente `A-Z a-z 0-9 . _ @ % + ! / -`. A
interface valida esse formato ao criar ou redefinir a senha da conta vinculada.

`ftp set` e `save` ficam reservados ao cadastro ou à alteração dos dados FTP.
Nas execuções recorrentes, o sistema prepara a sessão SSH, envia os comandos
de backup e aguarda a confirmação Huawei antes de iniciar o próximo arquivo.
As confirmações `Are you sure to continue? (y/n)` são respondidas
automaticamente.

Em 05/09/2026, o agendamento automático com método `FTP` foi concluído com
sucesso. Os dois arquivos foram recebidos e validados:

```text
OLT-HUAWUEI-BASE_20260905183850.cfg — 1.472.595 bytes
OLT-HUAWUEI-BASE_20260905183850.dat — 11.400.188 bytes
```

A operação FTP terminou em `success`.
