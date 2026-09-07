# Hardening

## Helper de atualização

O helper aceita somente ações allowlisted, não usa shell e confina pacote, staging e backup. A unit não tem timer, restringe rede a `AF_UNIX` e limita caminhos graváveis. Execute `systemd-analyze verify deploy/systemd/backup-manager-update.service` em homologação.

As práticas gerais estão em [SECURITY.md](SECURITY.md). Para FTP: anonymous e FXP desativados, chroot obrigatório, `backupftp` sem shell, PureDB fora do Git e nenhum sudo genérico. Somente o helper com ações allowlisted deve ser autorizado.

FTP simples não tem confidencialidade. Restrinja portas/origens, prefira FTPS, use credencial exclusiva e faça rotação. `ftp-incoming` não é publicado pelo Nginx e rejeitados não são acessíveis a perfis limitados.
