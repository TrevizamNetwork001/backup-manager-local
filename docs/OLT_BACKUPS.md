# Backups de OLT

## Visão geral

O módulo de OLT separa esses equipamentos de roteadores e switches. O método
selecionado define os comandos, o transporte de acesso e os arquivos que devem
ser recebidos pelo FTP. MikroTik continua no grupo **Roteadores**.

Há três formas de operação:

1. **Roteiro manual:** abre em nova aba, permite copiar os comandos e oferece
   fechar/voltar. A resposta usa `Cache-Control: no-store`.
2. **Executar agora:** acessa a OLT por SSH ou Telnet e mantém uma sessão única
   durante todo o roteiro.
3. **Agendamento:** usa o método de job `ftp`; a execução permanece em andamento
   até o importador confirmar todos os arquivos esperados.

FTP não fornece criptografia. Use esses fluxos somente em rede privada, VPN ou
ambiente controlado.

## Pré-requisitos

- equipamento ativo com o método correto;
- credencial de acesso ativa;
- conta FTP ativa vinculada ao equipamento;
- `ftp_public_ip` ou `ftp_public_ipv6` configurado;
- conectividade da OLT até a porta `control_port` da conta FTP;
- chave local de criptografia disponível para recuperar as senhas.

A porta da conta vinculada tem prioridade sobre a porta FTP global. Para
FiberHome Telnet, o cadastro sugere a porta de acesso 23.

## Métodos implementados

| Fabricante/família | Acesso | Ação principal | Arquivos esperados |
|---|---|---|---|
| FiberHome OLT | Telnet + FTP | `upload ftp config` e `upload ftp system` | `.txt` e `.db` |
| Huawei OLT | SSH + FTP | sessão preparada, `ftp set`, `backup configuration ftp` e `backup data ftp` | `.cfg` e `.dat` |
| ZTE OLT | SSH + FTP | teste `upload cfg` ou instalação `file-server auto-backup` | `.dat` no teste |
| Intelbras GPON 8820i/G08/G16 | SSH + FTP interativo | `backup network ftp` e prompts `User`/`Password` | `.cfg` |
| Intelbras EPON 4840 E | SSH + FTP | `upload configuration ftp inet` | `.cfg` |
| VSOL OLT | SSH + FTP | configuração `ftp-server` e `upload configuration ftp` | `.bin` |
| Parks 100xx/200xx | SSH + FTP | `copy startup-config ftp://... usuário senha` | `.bin` |
| Parks 300xx/400xx | SSH + FTP | `copy startup-config ftp://usuário:senha@...` | `.bin` |
| C-DATA GPON | SSH + FTP | `backup save-config format gz ftp` | `.gz` |
| Datacom DM461X/DmOS | SSH Pull | `show running-config` | `.cfg` local |

Huawei router e switch permanecem separados:

- router: `display current-configuration | no-more`;
- switch: `screen-length 0 temporary` e
  `display current-configuration`.

### Huawei OLT: sequência de sessão

O fluxo Huawei usa a credencial SSH para abrir uma sessão na OLT e a conta FTP
vinculada ao equipamento para receber os arquivos. Antes de configurar o FTP,
o executor envia os comandos de preparação da sessão para evitar paginação e
confirmações durante a automação:

```text
enable
config
undo interactive
undo smart
scroll
ftp set
<usuário FTP>
<senha FTP>
quit
save
config
backup configuration ftp <servidor FTP> <arquivo>.cfg
backup data ftp <servidor FTP> <arquivo>.dat
```

`ftp set` e `save` são executados somente durante a configuração inicial ou
quando os dados FTP forem alterados. Nas execuções recorrentes, a sessão apenas
prepara o terminal e envia os dois comandos `backup`. O backup só é considerado
concluído depois que os dois arquivos chegam ao diretório da conta FTP e são
importados pelo servidor.

## Estados e confirmação

Antes de enviar comandos, o sistema cria uma operação `olt_ftp` e registra os
nomes exatos dos artefatos esperados. O importador FTP correlaciona pelo
equipamento e nome do arquivo.

- `waiting_upload`: aguardando arquivos;
- `validating`: ao menos um arquivo está sendo processado;
- `success`: todos os arquivos obrigatórios chegaram e foram validados;
- `failed`: o acionamento ou um artefato falhou;
- `expired`: o prazo terminou sem todos os arquivos.

O job fica `running` enquanto aguarda FTP, muda para `success` após todos os
artefatos e para `timeout` quando o prazo expira. FiberHome e Huawei só concluem
com sucesso depois dos dois arquivos.

## Segurança

- senhas de acesso e FTP ficam criptografadas no banco;
- comandos com senha não entram em auditoria, logs ou mensagens de erro;
- roteiros manuais são deliberadamente visíveis apenas ao operador autenticado;
- geração e execução exigem token CSRF válido;
- páginas de roteiro não podem ser armazenadas em cache;
- erros do worker são restritos a exceções operacionais conhecidas;
- extensões aceitas para OLT: `.txt`, `.cfg`, `.dat`, `.db`, `.bin` e `.gz`;
- restauração, reinicialização e comandos destrutivos não fazem parte do módulo.

Ao terminar um roteiro manual, feche a aba para reduzir a exposição da senha.

## Compatibilidade legada

Chaves antigas como `parks_ssh`, `intelbras_ssh` e `fiberhome_ssh` continuam
executando pelo comportamento genérico anterior. Ao editar o equipamento, a
interface mostra **Método legado — revisão necessária** e exige a escolha do
modelo correto. Casos ambíguos não são convertidos automaticamente.

## Diagnóstico

Na página do equipamento, confira:

- conta FTP e porta efetiva;
- credencial e último teste de acesso;
- estado da última operação OLT;
- nome e estado de cada arquivo esperado;
- histórico de uploads e backups importados;
- execução do job e eventual código `FTP_FILE_NOT_RECEIVED`.

## Validação de desenvolvimento

```bash
python3 -m compileall -q backup_manager tests
python3 -m unittest discover -s tests
git diff --check
git status --short
```

## Histórico Git da implementação

- `ed44f35`: fluxos FTP iniciais FiberHome, Huawei e ZTE;
- `f8c4922`: Intelbras GPON e EPON;
- `28f5871`: VSOL;
- `973f012`: Parks, C-DATA e Datacom;
- `f52e869`: grupos OLT, roteador e switch;
- `30cd225`: planos operacionais unificados;
- `e96cc3a`: roteiro manual na interface;
- `8eaa539`: execução remota e correlação de artefatos;
- `4c16e94`: agendamento pelo motor de jobs;
- `b6d924f`: serviço único para execução manual e agendada;
- `13cdaa2`: compatibilidade de métodos legados;
- `dde4963`: extensões de artefatos OLT;
- `41eb6c6`: porta específica da conta FTP;
- `55d9cb1`: ajustes FiberHome Telnet;
- `f966cbc`: proteção CSRF das ações OLT.
