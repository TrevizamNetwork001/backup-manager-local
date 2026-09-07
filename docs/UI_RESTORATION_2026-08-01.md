# Preservação do layout moderno — 01/08/2026

Este documento registra a restauração visual realizada em 01/08/2026 e é a
referência obrigatória antes de alterar ou publicar arquivos de interface.

## Motivo

Uma cópia integral de `static/app.css` substituiu estilos que existiam somente
na instalação ativa. A estrutura HTML moderna continuou presente em algumas
páginas, mas sem seus seletores específicos elas voltaram visualmente ao padrão
antigo. Também havia rotas duplicadas com implementações diferentes, como
`/audit` e `/reports/audit`.

Não copie arquivos inteiros da árvore de desenvolvimento sobre a instalação
ativa sem comparar os dois lados. Preserve mudanças locais e publique somente
os arquivos validados.

## Referências visuais protegidas

As capturas originais estão na raiz da instalação ativa:

| Arquivo | Tela | SHA-256 |
| --- | --- | --- |
| `/opt/backup-manager-local/inicial.png` | Dashboard principal | `65cfaff4b925efc171f9cd88f878dc873274902a442e07b220abe7ba330deec7` |
| `/opt/backup-manager-local/comoesta.png` | Backups | `eea9e17d20dc44e0b867b82ddcfbf0350a8ee1ae45e9a77439b16e1a092c9716` |
| `/opt/backup-manager-local/iii.jpeg` | Central de segurança | `ba738f3ef43c95485f3510c33d110082179653a0ee7a5d33b063569956f7b58e` |

Essas imagens não devem ser removidas nem substituídas. Antes de refazer uma
dessas páginas, compare a implementação com a captura correspondente.

## Páginas e responsáveis

| Página | Implementação principal | Seletores importantes |
| --- | --- | --- |
| `/` | `backup_manager/dashboard_views.py`, `templates/dashboard.html` | `dashboard-reference-*`, `dashboard-security` |
| `/backups` | `backup_manager/backups_views.py` | `backup-reference-*`, `backup-report-*` |
| `/jobs` | `backup_manager/jobs_views.py`, `backup_manager/app.py` | `jobs-modern-*` |
| `/settings` e abas | `backup_manager/settings_views.py`, `backup_manager/settings_center.py`, `static/settings.css` | `settings-*` |
| `/reports/backups` | `backup_manager/app.py` | `report-reference-page`, `report-backups-reference` |
| `/reports/equipment` | `backup_manager/app.py` | `report-reference-page`, `report-equipment-reference` |
| `/reports/storage` | `backup_manager/app.py` | `report-reference-page`, `report-storage-reference` |
| `/reports/ftp` | `backup_manager/app.py` | `report-reference-page`, `report-ftp-reference` |
| `/reports/telegram` | `backup_manager/app.py` | `report-reference-page`, `report-telegram-reference` |
| `/reports/audit` | `backup_manager/app.py`, `backup_manager/report_views.py` | `report-reference-page`, `report-audit-*` |
| `/audit#security-report` | `backup_manager/app.py` | `security-center-*`, `security-kpis`, `security-rankings`, `security-auth-events` |

Os estilos compartilhados ficam em `static/app.css`. O layout da Central de
Configurações também depende de `static/settings.css`.

## Central de segurança

A página `/audit#security-report` deve seguir `iii.jpeg` e conter:

- cabeçalho “Central de segurança” e estado atual;
- falhas em 10 minutos e nas últimas 24 horas;
- quantidade de IPs distintos e acessos bloqueados;
- ranking de IPs e ranking de contas;
- eventos recentes de autenticação, com sucesso em verde e falha em amarelo;
- país, bandeira SVG, ASN e nome da operadora;
- ícone próprio para endereços de rede local.

País e ASN são resolvidos localmente por `ip_geo_summary()` em
`backup_manager/app.py`, usando o módulo `maxminddb`. Não há chamada para API
externa.

Arquivos necessários no servidor:

- `/usr/share/GeoIP/GeoLite2-Country.mmdb`
- `/usr/share/GeoIP/GeoLite2-ASN.mmdb`
- `/opt/backup-manager-local/static/assets/flags/*.svg`

As bandeiras usam o código ISO em minúsculas, por exemplo `br.svg` e `gb.svg`.
O arquivo `xx.svg` é apenas fallback de país desconhecido; redes privadas usam
o ícone interno de rede. A pasta de bandeiras deve ser preservada em deploys.

## Checklist obrigatório de publicação

1. Compare cada arquivo que será publicado com a instalação ativa usando
   `diff -u` ou `cmp`; não presuma que as árvores são idênticas.
2. Não substitua `static/app.css`, `static/settings.css`, `app.py`, templates ou
   módulos de views se houver diferenças locais não compreendidas.
3. Execute:

   ```bash
   python3 -m py_compile backup_manager/app.py
   python3 -m backup_manager.presentation_check
   ```

4. Confirme a existência das três imagens de referência, das duas bases MMDB e
   da pasta `static/assets/flags`.
5. Publique somente os arquivos alterados e reinicie
   `backup-manager-local.service`.
6. Confirme `systemctl is-active backup-manager-local.service`.
7. Valide manualmente, no mínimo: `/`, `/backups`, `/jobs`, `/settings`, todas
   as abas de `/reports` e `/audit#security-report`.
8. Faça atualização forçada no navegador (`Ctrl+F5`) antes de concluir que um
   estilo foi perdido.

## Verificação rápida dos arquivos de referência

```bash
sha256sum \
  /opt/backup-manager-local/inicial.png \
  /opt/backup-manager-local/comoesta.png \
  /opt/backup-manager-local/iii.jpeg

test -f /usr/share/GeoIP/GeoLite2-Country.mmdb
test -f /usr/share/GeoIP/GeoLite2-ASN.mmdb
test -f /opt/backup-manager-local/static/assets/flags/br.svg
test -f /opt/backup-manager-local/static/assets/flags/gb.svg
```

## Regra de recuperação

Se uma tela voltar ao padrão antigo, não tente modernizá-la do zero. Primeiro:

1. confira se a estrutura HTML moderna ainda existe;
2. procure os seletores listados acima em `static/app.css`;
3. compare com a captura protegida;
4. restaure somente o bloco ausente;
5. valide e publique de forma incremental.

