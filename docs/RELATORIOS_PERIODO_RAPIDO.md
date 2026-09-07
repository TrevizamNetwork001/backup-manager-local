# Relatórios: aplicação de período rápido

Data: 20/07/2026

## Alteração

O seletor `Período rápido` da visão geral de relatórios passou a ter uma ação própria para aplicar o intervalo escolhido.

- Adicionado o botão `Aplicar período` dentro do painel de datas.
- Mantidos os presets de 1, 7, 10, 15, 20, 25 e 30 dias.
- A seleção do preset preenche as datas inicial e final antes do envio.
- O preset escolhido permanece selecionado depois da atualização da página.
- Datas preenchidas manualmente continuam mudando o seletor para `Selecionar datas`.
- Os filtros de equipamento e status são preservados e enviados no mesmo formulário.
- O botão geral `Aplicar` continua disponível para aplicar todos os filtros.

## Validação

- Suíte `tests.test_reports`: 9 testes aprovados.
- Teste específico confirma a presença do botão e a preservação do preset.
- `py_compile` aprovado.
- `git diff --check` aprovado.
- Serviço `backup-manager-local.service` reiniciado e confirmado como ativo.

Nenhuma credencial, banco de produção, infraestrutura ou dado de backup foi alterado.
