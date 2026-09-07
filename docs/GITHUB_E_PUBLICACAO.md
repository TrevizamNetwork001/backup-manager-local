# GitHub e publicação de atualizações

Registro de 07/09/2026. Este documento é o ponto de retomada da publicação.

## Situação confirmada

| Item | Situação |
| --- | --- |
| Aplicação no servidor | 1.1.0, canal stable, implantada e validada |
| Testes da implantação | 363 aprovados |
| Código-fonte no GitHub | Enviado a `TrevizamNetwork001/backup-manager-local` |
| Branch | `main` |
| Commit inicial no GitHub | `b602b5e35744c8cb254f27e0098960dfa67dba25` |
| Tag de código-fonte | `v1.1.0`, confirmada remotamente |
| Acesso | SSH por deploy key exclusiva, com escrita |
| Chave de assinatura | Par Ed25519 validado pelo proprietário no Windows |
| Pacote oficial `.bmu` | Ainda não assinado nem publicado |
| Repositório de updates | Ainda não criado/configurado por este trabalho |
| Consulta automática de atualizações | Desabilitada até existir distribuição validada |

O repositório de código deve permanecer privado. O acesso SSH autenticado
funcionou e a consulta pública à API retornou 404. A integração de plugins não
expôs instalações/repositórios; o envio foi concluído diretamente com Git.

Código: https://github.com/TrevizamNetwork001/backup-manager-local

Tag: https://github.com/TrevizamNetwork001/backup-manager-local/tree/v1.1.0

Uma tag de Git não é uma release com pacote anexado. O histórico inicial do
GitHub contém uma importação dos 537 arquivos selecionados da versão validada.
Os 316 commits anteriores continuam no repositório original do servidor.

## Organização no servidor

- Projeto em execução: `/opt/backup-manager-local`.
- Checkout de publicação: `/opt/backup-manager-local/data/github-preparation/source`.
- Inventário inicial: `data/github-preparation/upload-files.txt`.
- Relatório da preparação: `data/github-preparation/review.json`.
- Evidências da versão: `data/releases/1.1.0/` (código, checksums, log e status).
- Backup SQLite da implantação:
  `data/deploy-backups/backup_manager.sqlite3.deploy-20260907141310.bak`.

Os dois repositórios Git têm históricos diferentes. O repositório original
continua preservado e sem `origin`; o checkout de publicação acompanha
`origin/main`. Não executar um `git pull` cego no diretório em produção, nem
usar force-push para substituir o histórico publicado.

O `.gitignore` exclui dados, bancos, chaves PEM/KEY, arquivos `.env`, referências
locais e staging. Isso não remove arquivos já rastreados: a importação foi feita
a partir de uma lista explícita. Bases GeoLite2, capturas fora dos assets e
arquivos alheios ao produto não foram incluídos. A verificação por padrões não
encontrou chaves privadas ou tokens GitHub/AWS/Telegram nos arquivos atuais;
não foi uma auditoria de todo o histórico antigo.

## Duas chaves com funções distintas

### SSH para enviar código ao GitHub

Privada: `data/github-access/backup-manager-local-ed25519`, permissão 0600,
dentro de diretório 0700 ignorado pelo Git. A pública correspondente foi
cadastrada pelo proprietário em **Settings → Deploy keys**, com escrita.
O checkout de publicação já configura a identidade SSH exclusiva e um arquivo
`known_hosts` com a chave de host Ed25519 publicada pelo GitHub.

A deploy key só permite operar o repositório ao qual está vinculada. Ela não
permite criar outro repositório, configurar Pages ou criar Releases pela API.
Não reutilizar esta chave em `backup-manager-updates`; esse repositório precisará
de acesso próprio ou de uma integração/API autorizada.

Se houver suspeita de comprometimento, revogar a deploy key em Settings e
cadastrar outra. Isso não modifica a assinatura dos pacotes de atualização.

### Ed25519 para assinar atualizações

As chaves oficiais permanecem com Cristiano, no Windows:

```text
C:\Users\Cristiano\Dropbox\backup-manager-release\update-private-key.pem
C:\Users\Cristiano\Dropbox\backup-manager-release\update-public-key.pem
```

Python 3.13.14 e `cryptography` foram instalados. O teste local retornou
`OK: chaves conferem.` A chave pública corresponde à instalada no servidor em
`/etc/backup-manager-local/update-public-key.pem`.

Fingerprint SHA-256 do DER SubjectPublicKeyInfo:

```text
9e65a39702966069d3d68dfbb6ae14c6842173ae70084db8f5c71354e18ee857
```

A chave privada não foi enviada ao servidor nem ao GitHub. Não a colocar em
commits, releases, Pages, chat ou dentro da pasta de código. Guardar também
uma cópia offline protegida; a pasta atual está sob sincronização do Dropbox.
Não substituir a chave pública instalada: o par atual já foi confirmado.

## Preparar a primeira atualização no Windows

O assistente [prepare_1_1_0.py](release-tools/prepare_1_1_0.py) é exclusivo da
versão 1.1.0. Ele confere a chave pública fixada acima e o hash agregado do
payload original, assina e valida o pacote e prepara um índice compatível com
GitHub Releases. Não faz upload, não acessa equipamentos, não altera chaves e
não sobrescreve uma pasta de saída existente.

1. No repositório privado, abrir **Code → Download ZIP** da branch `main`.
2. Extrair a pasta `backup-manager-local-main` dentro de `backup-manager-release`,
   ao lado das chaves, sem colocar as chaves dentro dela.
3. Abrir o PowerShell em `backup-manager-release` e executar esta linha curta:

```powershell
python .\backup-manager-local-main\docs\release-tools\prepare_1_1_0.py
```

Se o ZIP tiver outro nome de pasta, ajustar somente esse nome no comando.
O ZIP do GitHub preserva os bytes originais. Um clone com conversão automática
LF → CRLF pode ser recusado pela verificação do hash; não ignorar a verificação.
Usar o ZIP sem editar os arquivos ou fazer clone com `core.autocrlf=false`.

Saída esperada:

```text
OK: pacote e indice assinados e validados. Nenhum arquivo foi enviado.
```

O resultado fica ao lado das chaves, em outra pasta:

```text
publicacao-1.1.0/
  validation.json
  releases/
    backup-manager-local-1.1.0.bmu
    backup-manager-local-1.1.0.bmu.sig
  pages/
    .gitattributes
    .nojekyll
    index.html
    updates.json
    updates.json.sig
```

Somente os artefatos gerados serão publicados. A chave privada não é copiada.
Se a saída já existir, preservá-la. Para gerar outro conjunto, usar
`--output publicacao-1.1.0-nova`; não substituir arquivos de uma release já
publicada, porque seus hashes e assinaturas podem estar em uso pelos clientes.

O assistente gera apenas o índice inicial da 1.1.0. **Não usá-lo para versões
futuras nem sobrepor um índice que já contenha outras releases.**

## Criar a distribuição externa

Destino proposto: `TrevizamNetwork001/backup-manager-updates`.
Este nome é uma configuração preparada, não prova de que o repositório existe.

O desenho proposto usa Releases públicas para `.bmu`/`.sig` e Pages para o
índice. Como o `.bmu` contém Python, a publicação torna esse código acessível.
Não tornar público o repositório principal. Se a distribuição tiver que ser
restrita a clientes, definir autenticação e hospedagem apropriadas antes de
publicar: o atualizador atual não faz login em um repositório GitHub privado.

Ordem de publicação, depois de o proprietário definir a distribuição pública:

1. Criar o repositório de updates e inicializá-lo com README.
2. Em **Releases → Draft a new release**, criar a tag `v1.1.0` e o título
   `Backup Manager Local 1.1.0`.
3. Anexar os dois arquivos da pasta `publicacao-1.1.0/releases` à mesma release.
4. Conferir os anexos e publicar a release estável.
5. Copiar o **conteúdo** de `publicacao-1.1.0/pages` para a raiz da branch `main`
   do repositório de updates. Incluir `.gitattributes` e `.nojekyll`; não editar
   o JSON nem suas assinaturas. `* -text` evita conversão de fim de linha pelo Git.
6. Em **Settings → Pages**, selecionar publicação pela branch `main`, pasta raiz.
7. Validar os downloads e as assinaturas remotas antes de habilitar o atualizador.

A URL preparada para o índice é:

```text
https://trevizamnetwork001.github.io/backup-manager-updates/updates.json
```

Os pacotes apontam para:

```text
https://github.com/TrevizamNetwork001/backup-manager-updates/releases/download/v1.1.0/backup-manager-local-1.1.0.bmu
```

O assistente adapta as URLs e assina novamente o índice. O script genérico
`scripts/publish-update-index.py` usa um layout de hospedagem estática
`BASE/releases/arquivo.bmu`; ele sozinho não produz URLs de GitHub Releases.

## Habilitar a consulta no servidor

Somente depois de publicar e validar:

1. Na administração de atualizações, cadastrar a URL de Pages acima e canal
   `stable`, mantendo validação de TLS e assinatura ativa.
2. Testar `update-check`, os tipos de conteúdo do índice/assinatura e o download
   com redirecionamentos reais do GitHub.
3. Caso exista allowlist de hosts, incluir somente os hosts observados e
   verificados no download; não desabilitar a validação para contornar erros.
4. Validar RC9 → 1.1.0 em homologação e documentar aplicação/recuperação.

Na instalação atual, já em 1.1.0, é esperado que a mesma versão **não seja
oferecida**. Não forçar downgrade nem mudar a versão do banco para testar.
Consulta/download não aplicam a atualização automaticamente.

## Próximas versões

1. Desenvolver e revisar alterações no repositório de código; não publicar dados
   operacionais ou credenciais junto com patches.
2. Atualizar versão, canal, build e notas; passar testes e conferir migrations.
3. Homologar o caminho de atualização, backup e recuperação. O pacote 1.1.0 foi
   preparado para RC9 no schema 31; isso não comprova outros saltos de versão.
4. Criar nova tag sem mover `v1.1.0`.
5. Assinar na estação do proprietário com a chave oficial.
6. Preservar releases anteriores no índice e suas URLs; não recriar um índice
   de produção só com a versão nova sem avaliar clientes mais antigos.
7. Publicar os artefatos antes de publicar o índice que os anuncia.
8. Conferir assinatura, hashes, MIME e redirecionamentos via atualizador real.

## O que depende do retorno do proprietário

- Executar a assinatura oficial no Windows, onde está a chave privada.
- Criar/configurar o repositório de updates e definir sua visibilidade.
- Disponibilizar os artefatos assinados e o acesso necessário para publicação.

O código e sua documentação podem ser mantidos pelo acesso SSH já configurado.
Criar repositórios, releases pela API e configurar Pages exige acesso adicional;
a deploy key do código não concede essas capacidades.

## Referências

- [Deploy keys do GitHub](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)
- [Fingerprints de host do GitHub](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)
- [Criar GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)
- [Publicar Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)
- [Validação da instalação 1.1.0](RELEASE_1.1.0.md)
