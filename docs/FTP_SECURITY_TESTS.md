# Testes de segurança FTP — FASE 6

Data da execução: 2026-07-11 (America/Sao_Paulo)

## Ambiente e método

Os testes foram executados diretamente contra o Pure-FTPd local na porta 21,
com duas contas virtuais temporárias (`phase6-test-a` e `phase6-test-b`). Elas
não foram inseridas no SQLite da aplicação. O timer do importador foi suspenso
durante o ensaio para que as respostas fossem exclusivamente do daemon.

Antes do teste, `pureftpd.passwd` e `pureftpd.pdb` foram copiados. Ao final,
ambos foram restaurados byte a byte, os diretórios temporários foram removidos
e Pure-FTPd/importer voltaram a `active`.

## Política `upload_only`: resultado real

| Operação | Resultado observado |
|---|---|
| `STOR` | permitido (`226 File successfully transferred`) nas duas contas |
| `LIST` | permitido (`226`, conteúdo restrito à raiz chroot da conta) |
| `NLST` | permitido, restrito à raiz chroot da conta |
| `RETR` | negado (`550 Permission denied`) |
| `DELE` | negado (`550 Operation not permitted`) |
| `RNFR/RNTO` | negado (`550 Rename/move failure`) |
| `CWD ..` | responde `250 OK`, mas permanece em `/`; nenhum diretório pai é exposto |

Somente envio; listagem pode estar disponível por compatibilidade.

Arquivos novos foram observados com modo `0200` (`--wx------`), resultado de
`Umask 477 077`. Isso mantém `STOR` funcional, impede `RETR` pelo usuário
virtual e permite que o importador executado como root leia o arquivo.
`KeepAllFiles=yes` impediu exclusão e `NoRename=yes` impediu renomeação.

O comportamento de `CWD ..` merece registro explícito: o Pure-FTPd considera
o comando bem-sucedido na raiz do chroot, porém normaliza o destino para a
própria `/`. Assim, a exigência de não acessar o pai foi atendida, embora a
resposta FTP não seja um erro `550`.

## Isolamento entre duas contas

As duas contas fizeram upload apenas em sua própria raiz. Foram criados links
simbólicos temporários apontando da conta A para B, de B para A, e da conta A
para `/etc`, `/etc/pure-ftpd` e `/var/backups/backup-manager-local`.

Todos os `CWD` pelos links falharam com `550 No such file or directory`. Logo:

- A não acessou B e B não acessou A;
- nenhuma conta escapou para o diretório pai;
- `/etc`, PureDB e backups definitivos não foram acessíveis;
- `STOR` funcionou somente dentro da própria conta.

## Preservação e ativação

`scripts/enable-ftp.sh` concluiu após validar `visudo` e
`pure-ftpd-wrapper --show-options`, antes dos restarts. O backup definitivo da
execução bem-sucedida é
`/var/backups/backup-manager-local/enable-ftp/20260711T165230Z-94569`, com
`MANIFEST.txt` e `MANIFEST.sha256`.

Após o ensaio:

- a única conta permanente no PureDB continuou sendo `trevizam`;
- hashes de `pureftpd.passwd` e `pureftpd.pdb` voltaram, respectivamente, a
  `9de220c67aad867da43e8144c6f4d63009f81e018f4758cc85203ac50b70ca47` e
  `ece4bb76e08591847adea510611ea11dfcad105b540dc2d616dd4009e1d79d7e`;
- os diretórios e uploads permanentes não foram usados nem modificados pelo
  teste;
- o fluxo não contém operações sobre Nginx, Let's Encrypt, UFW, interface ou
  SSH.

Uma segunda execução terminou com sucesso e produziu hashes antes/depois
idênticos para Nginx, Let's Encrypt, UFW, interface, uploads, PureDB,
`pureftpd.passwd` e `trevizam`, comprovando idempotência e preservação. Os
serviços `pure-ftpd` e `backup-manager-ftp-importer.timer` ficaram `active`.

## Testes automatizados

Os testes de repositório cobrem root/dry-run, conteúdo do backup, manifesto de
hashes, rollback, MinUID dinâmico, ordem das validações, controles de
`upload_only` e ausência de gerenciamento dos serviços fora do escopo.
