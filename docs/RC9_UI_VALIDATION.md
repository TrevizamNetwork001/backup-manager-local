# Homologação visual e operacional — v1.1.0-rc9

Use este roteiro antes de promover a RC9 para `v1.1.0`. Não é necessário
alterar configurações que já estejam corretas.

## Interface

- [ ] Login, logout e troca de senha funcionam.
- [ ] Dashboard abre sem alerta técnico e apresenta os totais esperados.
- [ ] Menu e telas permanecem utilizáveis no computador e no celular.
- [ ] Equipamentos podem ser abertos, editados e filtrados por grupo.
- [ ] Backups exibem detalhes, tamanho, origem e opção de download.
- [ ] Centro de Operações, relatórios e auditoria abrem normalmente.
- [ ] Central de Configurações preserva os valores já cadastrados.

## Fluxos reais

- [ ] Um backup SSH manual conclui e o arquivo pode ser baixado.
- [ ] Um envio FTP é associado ao equipamento correto.
- [ ] Um backup de OLT conclui pelo fluxo configurado no equipamento.
- [ ] A mensagem de teste do Telegram chega ao destino correto.
- [ ] A cópia Telegram de um backup chega ao chat, canal ou tópico esperado.
- [ ] A cópia rclone chega em `<nome-da-instalacao>/<hostname>/<DD-MM-AAAA>`.
- [ ] Uma falha proposital apresenta mensagem compreensível e permite tentar novamente.

## Recuperação

- [ ] Um backup baixado abre e contém configuração válida.
- [ ] A lixeira permite restaurar um item de teste sem alterar outros backups.
- [ ] A retenção simulada informa exatamente o que seria removido.

Registre qualquer divergência com tela, equipamento, horário aproximado e a
ação executada. Esses dados permitem localizar a ocorrência na auditoria sem
expor senhas ou tokens.
