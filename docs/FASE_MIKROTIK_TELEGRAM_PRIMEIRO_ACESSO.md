# Fechamento da fase: MikroTik, Telegram e primeiro acesso

Data: 20/07/2026

## Escopo concluído

### MikroTik FTP Push

- O diagnóstico exibe somente o botão de configuração antes de abrir o fluxo guiado.
- O modal foi reorganizado em três etapas: conta FTP, backup e destino.
- O botão `Aplicar configuração` permanece disponível na etapa final.
- Contas FTP existentes são apresentadas sem diretório interno, UUID ou termos do importador legado.
- Credenciais antigas não recuperáveis exigem rotação antes da geração do script.
- Integrações órfãs, vinculadas a contas removidas ou inativas, são desativadas sem apagar histórico e deixam de bloquear uma nova integração.
- A rotação de senha abre o script atualizado no modal, sem expor a credencial em páginas intermediárias.
- O estado visual diferencia conta FTP pronta de FTP Push efetivamente configurado.
- O cartão `Backups recebidos` é atualizado ao concluir a operação, sem depender de recarga manual da página.

### Telegram operacional

- O aviso de backup SSH concluído resolve internamente o backup e mostra o nome do equipamento.
- Backups SSH e FTP identificados usam o rótulo amigável `Backup de configuração` em vez do nome técnico longo.
- O arquivo físico, seu nome original, UUID e demais dados técnicos permanecem preservados no banco e na auditoria.
- Backups FTP sem identificação continuam mostrando o nome recebido, pois ele é necessário para investigação.
- Eventos de sucesso de backup usam o ícone `✅`.
- O fluxo foi testado somente com transportes falsos; os testes não enviam mensagens reais.

Exemplo final:

```text
✅ Backup SSH concluído

🖥️ Equipamento: CE-BASE-CONECTA
📄 Arquivo: Backup de configuração
🕒 Horário: 20/07/2026 12:09
```

### Primeiro acesso e troca obrigatória de senha

- A rota `/change-password` foi redesenhada a partir da referência visual fornecida.
- O layout é responsivo para monitor, notebook, tablet e celular, considerando largura e altura disponíveis.
- A tela usa SVGs próprios, tipografia e paleta compatíveis com o restante da interface.
- A força da senha e os requisitos são atualizados em tempo real.
- A validação no servidor exige no mínimo dez caracteres, uma letra maiúscula, um número e um caractere especial.
- Os campos possuem preenchimento automático apropriado e controle para mostrar ou ocultar a senha.
- A opção de sair preserva a exigência de troca no próximo login.

## Preservação e segurança

- Nenhum token, senha, `chat_id`, credencial SSH ou credencial FTP foi alterado por esta fase.
- Nenhum histórico de integração, upload, backup ou auditoria foi apagado.
- Nomes técnicos e UUIDs continuam disponíveis internamente, sem exposição nas mensagens operacionais.
- Nginx, firewall, tópico do Telegram e infraestrutura não foram modificados.
- As imagens `trocasenha.png` e `tela01.png` foram usadas apenas como referência local e não fazem parte do commit.

## Validação

- Testes de interface e fluxo de primeiro acesso.
- Testes de notificações operacionais e formatação visual.
- Testes de execução e configuração MikroTik FTP Push.
- Testes de configurações, relatórios e apresentação.
- 78 testes dos módulos relacionados executados com sucesso em conjunto.
- 6 testes focados de `test_app` executados com sucesso em processo isolado.
- A execução de todos esses módulos no mesmo processo apresentou contaminação de estado entre módulos (sessão e backups residuais); não houve falha ao separar os processos.
- `py_compile` e `compileall`.
- `git diff --check`.
- Serviço `backup-manager-local.service` reiniciado e confirmado como ativo após as alterações visuais.
