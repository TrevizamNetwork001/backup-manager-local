# Desenvolvimento da interface

## Arquitetura encontrada

A aplicação web não usa Flask, Django, Gunicorn, Uvicorn ou Waitress. Ela é uma
aplicação WSGI feita com a biblioteca padrão do Python e servida por
`wsgiref.simple_server`. Em produção, o Nginx encaminha as requisições para
`127.0.0.1:8080` e o systemd inicia `/usr/bin/python3
/opt/backup-manager-local/run.py` pelo serviço `backup-manager-local.service`.

Os templates ficam em `templates/`, o CSS e o JavaScript em `static/`. Login e
dashboard usam templates carregados em runtime; `base.html`, `sidebar.html` e
`topbar.html` concentram a estrutura compartilhada.

O Dashboard usa a variante visual `sidebar-main-reference`, inspirada na
referência `SAIDEBAR.png`: menu escuro com 250 px, item ativo verde e cartão de
estado operacional no rodapé. A classe é aplicada apenas ao Centro de Operações;
as demais páginas preservam o menu compacto compartilhado. A imagem de referência
não faz parte dos assets publicados pela aplicação.

A marca dessa variante usa a pilha tipográfica `Roboto`, `Segoe UI` e `Arial`,
com peso e proporções ajustados para a referência. O escudo da marca e os ícones
do menu são SVGs de contorno incorporados ao template, sem fonte de ícones,
biblioteca JavaScript ou acesso a serviços externos.
O Dashboard também oculta no rodapé da sidebar o cartão redundante com avatar,
usuário e nome do produto; a ação de sair permanece disponível.
O escudo oficial reutilizável está em
`static/assets/backup-manager-logo.svg`; o nome e o qualificativo `Local`
continuam como texto HTML para acessibilidade e adaptação ao espaço disponível.
No Centro de Operações esse SVG não é substituído pela personalização global de
branding; a logo configurável continua sendo respeitada nas demais páginas.

## Executar em desenvolvimento

Pare ou use uma porta diferente da instância que já esteja ocupando a porta
8080. Depois execute como o mesmo usuário que possui acesso ao banco de teste:

```bash
cd /opt/backup-manager-local
BACKUP_MANAGER_PORT=8081 ./scripts/run-dev.sh
```

O runner define `BACKUP_MANAGER_ENV=development` e observa `run.py`, módulos
Python e templates. Mudanças Python reiniciam somente o processo iniciado por
esse comando. O serviço systemd de produção não é observado nem alterado.

- CSS: salve e atualize o navegador; a versão da URL muda automaticamente.
- JavaScript: salve e atualize o navegador; a versão da URL muda automaticamente.
- Template: salve e atualize o navegador; o arquivo é lido novamente no request.
- Python em desenvolvimento: salve; o runner reinicia o servidor automaticamente.

## Produção

O serviço real continua sem autoreload:

```bash
sudo systemctl restart backup-manager-local.service
```

Mudanças somente em templates, CSS ou JavaScript não exigem restart da aplicação.
Mudanças Python exigem o restart controlado acima ou o fluxo completo de
`sudo bash scripts/deploy-update.sh`. Não existe reload gracioso configurado no
servidor `wsgiref`; portanto não foi documentado um comando de reload inexistente.

O HTML dinâmico usa `Cache-Control: no-store`. Assets com a versão correta na
query string usam cache imutável; acessos sem versão usam `no-cache`. O Nginx do
repositório atua apenas como proxy e não sobrescreve esses cabeçalhos.

## Publicar alterações visuais

Depois de uma tarefa visual que também altere Python, ou quando for desejável
validar e publicar todo o conjunto de uma vez, execute:

```bash
cd /opt/backup-manager-local
sudo ./scripts/deploy-ui.sh
```

O script interrompe no primeiro erro. Antes de reiniciar, ele valida sintaxe
Python, templates, CSS, JavaScript, shell, `git diff --check`, a suíte completa e
a ausência de autoreload/debug na unit real de produção. Como o `wsgiref` não
possui reload gracioso, somente então executa um restart controlado de
`backup-manager-local.service`, confirma `active` e testa `/login` e as URLs
versionadas dos assets.

O script não faz commit, `git pull`, migrations, descarte de alterações ou
remoção de arquivos.

Fluxo resumido:

- somente CSS/JavaScript: salvar e usar F5; Ctrl+F5 apenas se o navegador mantiver
  uma aba antiga aberta por muito tempo;
- somente template: salvar e usar F5, pois os templates são lidos em runtime;
- Python em desenvolvimento: `./scripts/run-dev.sh` faz autoreload local;
- Python em produção ou publicação completa: `sudo ./scripts/deploy-ui.sh`.

Verificação e diagnóstico:

```bash
systemctl status backup-manager-local.service --no-pager
journalctl -u backup-manager-local.service -n 100 --no-pager
```

## Validação de recursos Python

Além da suíte comum, execute periodicamente com `ResourceWarning` visível para
detectar arquivos, sockets e conexões SQLite sem fechamento determinístico:

```bash
python3 -W always::ResourceWarning -m unittest discover -s tests
```

Os fixtures SQLite devem preservar as duas responsabilidades: commit/rollback
do contexto da conexão e `close()` ao final. Para conexões SQLite nativas, use
um context manager que execute ambos; o `with sqlite3.connect(...)` isolado não
fecha a conexão automaticamente.
