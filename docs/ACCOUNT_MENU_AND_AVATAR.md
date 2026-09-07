# Menu da conta, troca de senha e avatar

Este documento descreve o menu da conta exibido no avatar do cabeçalho global
do Backup Manager Local. A funcionalidade foi incorporada em 02/08/2026 ao
componente compartilhado; portanto, fica disponível em todas as páginas
autenticadas que usam o layout principal.

## Experiência do usuário

Ao clicar no avatar, no canto superior direito, o sistema abre um painel com:

- identificação compacta do usuário conectado;
- ação **Alterar senha**;
- ação expansível **Trocar ícone do avatar**.

O menu fecha ao clicar fora dele ou ao pressionar `Esc`. A tecla devolve o foco
ao botão do avatar. A implementação usa os elementos HTML nativos `details` e
`summary`, preservando navegação por teclado sem criar um modal.

O painel é posicionado sobre o conteúdo e não participa do cálculo da altura do
cabeçalho. O topo continua com 48 px, sem deslocar o conteúdo principal. Em
celulares, o painel usa posicionamento fixo abaixo do cabeçalho e limita a
largura ao espaço disponível na tela.

## Alteração de senha

A opção do menu encaminha para `GET /change-password`. O mesmo fluxo atende
dois contextos:

- no primeiro acesso, informa que a senha provisória deve ser substituída e
  mantém a saída da sessão como alternativa;
- para uma conta já configurada, apresenta **Segurança da conta**, permite
  cancelar e voltar ao Dashboard e, após salvar, retorna ao Dashboard.

A alteração exige a senha atual e uma nova senha com pelo menos 10 caracteres,
uma letra maiúscula, um número e um caractere especial. A confirmação deve ser
idêntica. O hash continua sendo produzido pelo mecanismo existente em
`backup_manager.security`; a senha em texto puro não é persistida nem incluída
na auditoria.

O evento de auditoria registrado é `password_changed`. Quando a instalação
ainda não concluiu a configuração inicial, o fluxo continua em `/setup` após a
troca obrigatória.

## Ícones disponíveis

O usuário pode escolher uma das opções abaixo:

| Valor persistido | Apresentação |
| --- | --- |
| `initial` | Inicial do nome ou usuário |
| `user` | Usuário |
| `shield` | Escudo com confirmação |
| `server` | Servidor |
| `network` | Rede |
| `settings` | Ajustes |

Os desenhos são SVGs incorporados e seguem a cor do componente. Não há upload
de imagem, leitura de arquivo externo ou HTML fornecido pelo usuário. A lista é
fechada no servidor; qualquer valor fora dela é recusado com HTTP 400.

Após salvar, a escolha aparece no avatar do cabeçalho e no resumo do usuário na
barra lateral.

## Persistência e compatibilidade

A escolha é armazenada na tabela genérica `settings`, sob a chave:

```text
profile_avatar_icon_<user_id>
```

Exemplo para o usuário de identificador 1:

```text
profile_avatar_icon_1 = shield
```

Esse desenho mantém a preferência separada por usuário e não exige migração do
banco. Usuários sem chave persistida recebem `initial`. Um valor antigo ou
desconhecido também é renderizado como inicial, evitando quebra do layout.

## Requisição e segurança

A atualização usa `POST /profile/avatar` com os campos:

```text
csrf_token=<token da sessão>
avatar_icon=<opção permitida>
```

Regras aplicadas pelo servidor:

- sessão autenticada obrigatória;
- validação CSRF vinculada ao cookie da sessão;
- lista fechada de valores aceitos;
- persistência somente para o identificador do usuário da sessão;
- auditoria `profile.avatar_updated`, contendo somente o nome do ícone;
- redirecionamento seguro para `/` quando o referenciador não é um caminho
  interno relativo.

O token CSRF é preparado no início da requisição WSGI e transportado ao
componente global por contexto de requisição. Ele não é persistido na
preferência do avatar.

## Componentes e responsabilidades

- `backup_manager/app.py`: catálogo de ícones, renderização segura, preferência
  por usuário, CSRF, atualização, auditoria e adaptação do fluxo de senha.
- `templates/components/topbar.html`: botão do avatar, painel da conta, ação de
  senha e formulário de seleção.
- `templates/components/sidebar.html`: reutilização do avatar selecionado.
- `static/app.css`: painel, estados de foco/seleção e comportamento responsivo.
- `static/app.js`: fechamento por clique externo e tecla `Esc`.
- `tests/test_presentation.py`: contrato estrutural do cabeçalho compartilhado.
- `tests/test_settings.py`: atualização persistente e renderização do avatar.

## Validação realizada em 02/08/2026

Foram aprovados:

```bash
python3 -m py_compile backup_manager/app.py
python3 -m backup_manager.presentation_check
python3 -m unittest tests.test_presentation.PresentationArchitectureTests.test_shared_topbar_keeps_user_area_in_flow_across_viewports
python3 -m unittest tests.test_settings.FinalSettingsTest.test_user_can_change_avatar_icon_from_account_menu
python3 -m unittest tests.test_app.BackupManagerAppTest.test_login_requires_password_change_then_setup_and_crud tests.test_app.BackupManagerAppTest.test_first_access_password_page_matches_reference_and_enforces_strength
git diff --check
```

Os testes de módulos que definem bancos temporários globais devem ser executados
em processos separados, como acima, para evitar colisão de ambiente entre
fixtures. Na execução ampliada de `tests.test_settings`, permanece uma falha
anterior: o teste procura o texto **Backup da Configuração**, aba removida do
layout em 01/08/2026. Essa expectativa desatualizada não pertence ao menu da
conta.

Depois da alteração, `backup-manager-local.service` foi reiniciado e confirmado
como `active`.

