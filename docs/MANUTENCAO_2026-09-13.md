# Manutenção operacional — 13/09/2026

## Correções

- Os links de Estado geral, Alertas ativos e Precisa de atenção encaminham
  alertas do Telegram para a fila; falhas recentes encaminham para o histórico.
- Ver todos os eventos de Atividade recente abre `/reports/audit`. A rota
  `/audit` permanece como central de segurança, IPs, ASN e autenticações.
- A importação FTP normaliza permissões para leitura pelo worker Telegram.
  A área temporária mantém escrita pelo grupo do serviço.
- Um erro de leitura em um backup é registrado no item sem derrubar o lote.
- O título de falha externa deixou de afirmar que o envio foi simulado.
- As legendas do Telegram usam o fuso configurado e o rótulo Enviado em.

## Recuperação

O diagnóstico e a recuperação do Google Drive estão em
[Incidente Google Drive](INCIDENTE_GOOGLE_DRIVE_2026-09-13.md).
A fila de 80 documentos do Telegram foi retomada após normalizar permissões e
validar integridade. Os detalhes e o registro reversível de permissões estão em
[Cópia no Telegram](TELEGRAM_BACKUP.md).

A única falha antiga de cloud encontrada fora do incidente atual pertence a
um destino excluído em agosto. Esse registro histórico não foi apagado nem
reenviado para outro destino.

## Testes

Há regressões para os links dos alertas, modo e grupo de arquivos FTP,
erro de leitura sem encerramento do worker e conversão de fuso nas legendas.
O teste isolado da página de login agora prepara o schema necessário.
O teste do preparador de release usa uma impressão digital do código de teste;
a impressão digital de produção da release original 1.1.0 continua imutável,
e o caso que rejeita código divergente permanece testado. Nenhum pacote novo
foi publicado como se fosse a release original.

## Google OAuth

Confirmar com o proprietário o status de publicação do app Google. Modo de
teste para aplicativo externo com escopo Drive limita refresh tokens a sete
dias; isso exige configuração na conta Google Cloud, seguida de nova autorização.
O acesso administrativo à conta Google não está disponível nesta manutenção.
