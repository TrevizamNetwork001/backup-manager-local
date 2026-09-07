from __future__ import annotations

from .presentation import escape_html as e


def equipment_page_content(*, user, error: str, equipment, vendors, groups, pops, environments, dependency_map, equipment_form, message, dependency_summary, can_operate, is_mikrotik_equipment, new_mode=False, driver_options=None) -> str:
    equipment = list(equipment)

    def options(items) -> str:
        return '<option value="">Todos</option>' + "".join(
            f'<option value="{item["id"]}">{e(item["name"])}</option>' for item in items
        )

    def method_label(driver: str) -> str:
        value = (driver or "").lower()
        if "ftp" in value:
            return "FTP"
        if "telnet" in value:
            return "Telnet"
        return "SSH"

    def icon(name: str) -> str:
        paths = {
            "edit": '<path d="M4 16v4h4L19 9l-4-4L4 16Zm9-9 4 4"/>',
            "view": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
            "run": '<circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4Z"/>',
            "trash": '<path d="M4 7h16M9 7V4h6v3m3 0-1 14H7L6 7m4 4v6m4-6v6"/>',
            "trash-filled": '<path fill="currentColor" stroke="none" d="M9 3.5h6l.8 2H20a1 1 0 1 1 0 2h-.8l-.9 12a2 2 0 0 1-2 1.8H7.7a2 2 0 0 1-2-1.8l-.9-12H4a1 1 0 1 1 0-2h4.2l.8-2Zm1.2 5.7a.8.8 0 0 0-.8.8v7a.8.8 0 0 0 1.6 0v-7a.8.8 0 0 0-.8-.8Zm3.6 0a.8.8 0 0 0-.8.8v7a.8.8 0 0 0 1.6 0v-7a.8.8 0 0 0-.8-.8Z"/>',
            "pause": '<path d="M9 7v10M15 7v10"/>',
            "archive": '<path d="M4 8h16v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8Z"/><path d="M3 4h18v4H3zM9 12h6"/>',
            "warning": '<path d="M10.25 3.8 1.9 18.3A1.8 1.8 0 0 0 3.46 21h17.08a1.8 1.8 0 0 0 1.56-2.7L13.75 3.8a2.02 2.02 0 0 0-3.5 0Z"/><path d="M12 9v5m0 3h.01"/>',
        }
        return f'<svg aria-hidden="true" viewBox="0 0 24 24">{paths[name]}</svg>'

    summary_icons = {
        "total": '<svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="13" rx="1.5"/><path d="M8 21h8M12 17v4"/></svg>',
        "online": '<svg aria-hidden="true" viewBox="0 0 24 24"><circle class="summary-icon-shape" cx="12" cy="12" r="10"/><path class="summary-icon-mark" d="m7.5 12 3 3 6.5-7"/></svg>',
        "offline": '<svg aria-hidden="true" viewBox="0 0 24 24"><circle class="summary-icon-shape" cx="12" cy="12" r="10"/><path class="summary-icon-mark" d="m8.5 8.5 7 7m0-7-7 7"/></svg>',
        "attention": '<svg aria-hidden="true" viewBox="0 0 24 24"><path class="summary-icon-shape" d="M10.25 3.8 1.9 18.3A1.8 1.8 0 0 0 3.46 21h17.08a1.8 1.8 0 0 0 1.56-2.7L13.75 3.8a2.02 2.02 0 0 0-3.5 0Z"/><path class="summary-icon-mark" d="M12 9v5m0 3h.01"/></svg>',
    }

    def equipment_actions(item) -> str:
        manage = (
            f'<a class="button small" href="/equipment/{item["id"]}#ftp-push">Gerenciar backups</a>'
            if is_mikrotik_equipment(item, item["vendor"]) else ""
        )
        if not can_operate(user):
            return f'<div class="actions">{manage}<span class="muted">Leitura</span></div>'
        dependencies = dependency_map[item["id"]]
        accounts = dependencies["accounts"]
        integrations = dependencies["integrations"]
        active_integrations = dependencies["active_integrations"]
        blockers = dependency_summary(dependencies)
        account_rows = "".join(
            f'''<li>Conta FTP #{account['id']}: <strong>{'excluída' if account['deleted_at'] else ('ativa' if account['is_active'] else 'inativa')}</strong>,
              {'com histórico' if account['has_history'] else 'sem histórico'},
              {'vinculada ao FTP Push' if account['used_by_push'] else 'usada pelo importador FTP antigo'}.</li>'''
            for account in accounts
        )
        integration_rows = "".join(
            f'''<li>Integração FTP Push #{integration['id']}: <strong>{'ativa' if integration['is_active'] else 'inativa'}</strong>
              {' · vínculo órfão (a conta FTP foi excluída)' if integration['account_deleted'] else ''}.</li>'''
            for integration in integrations
        )
        ftp_note = f'<ul class="dependency-summary">{account_rows}{integration_rows}</ul>' if account_rows or integration_rows else ""
        push_note = (
            f'''<div class="notice warning"><strong>Bloqueio:</strong> existe integração FTP Push ativa.
          Desative-a antes de remover este equipamento.</div><div class="actions block-actions">{''.join(
                f'<form method="post" action="/mikrotik-ftp/{integration["id"]}/deactivate"><button class="danger" type="submit">Desativar integração FTP Push</button></form>'
                for integration in active_integrations)}</div>'''
            if active_integrations else ""
        )
        account_note = (
            '''<div class="notice warning"><strong>Bloqueio:</strong> existe conta FTP ativa.
          Desative ou exclua a conta antes de arquivar o equipamento.</div>'''
            if dependencies["active_accounts"] else ""
        )
        archive_control = (
            f'''<form id="equipment-archive-{item['id']}" class="equipment-archive-form" method="post" action="/equipment/{item['id']}/archive">
          <fieldset><legend>Arquivos ao arquivar</legend><label><input type="radio" name="file_action" value="preserve" checked> Preservar backups</label>
          <label><input type="radio" name="file_action" value="trash"> Mover backups para a lixeira</label></fieldset>
          <label>Digite <strong>{e(item['hostname'])}</strong> para confirmar<input name="confirm_name" autocomplete="off" required></label>
          <button type="submit">Arquivar equipamento</button></form>'''
            if not active_integrations and not dependencies["active_accounts"] else
            '<button class="danger" type="button" disabled aria-disabled="true">Arquivamento bloqueado</button>'
        )
        purge_control = f'''<form class="equipment-purge-form" method="post" action="/equipment/{item['id']}/purge">
          <div class="notice error equipment-purge-notice"><span>{icon('warning')}</span><div><strong>Exclusão definitiva:</strong> todos os itens do relatório acima e seus arquivos serão apagados sem possibilidade de restauração.</div></div>
          <label>Digite o hostname<input name="confirm_name" placeholder="{e(item['hostname'])}" autocomplete="off" required></label>
          <label>Digite <strong>EXCLUIR TUDO</strong><input name="confirmation" placeholder="EXCLUIR TUDO" autocomplete="off" required></label>
          <button class="danger" type="submit">Remover equipamento e tudo atrelado</button></form>''' if user["can_admin"] else ""
        return f'''<div class="actions equipment-row-actions">{manage}<a class="icon-action" title="Editar" aria-label="Editar {e(item['hostname'])}" href="/equipment/{item["id"]}/edit">{icon('edit')}</a>
          <a class="icon-action" title="Visualizar" aria-label="Visualizar {e(item['hostname'])}" href="/equipment/{item["id"]}">{icon('view')}</a>
          <a class="icon-action" title="Executar backup" aria-label="Executar backup de {e(item['hostname'])}" href="/equipment/{item["id"]}#backups">{icon('run')}</a>
          <button class="icon-action danger" title="Remover" aria-label="Remover {e(item['hostname'])}" type="button" data-dialog-open="equipment-delete-{item['id']}" onclick="document.getElementById('equipment-delete-{item['id']}').showModal()">{icon('trash')}</button></div>
          <dialog id="equipment-delete-{item['id']}" class="equipment-delete-dialog"><form method="dialog"><button class="dialog-close" aria-label="Fechar">×</button></form>
            <header class="equipment-delete-head"><span class="equipment-delete-icon">{icon('trash-filled')}</span><div><span class="eyebrow">Gerenciar ciclo de vida</span><h2>Remover equipamento</h2><strong class="equipment-delete-name">{e(item['hostname'])}</strong></div></header>
            <section class="equipment-delete-dependencies"><span class="equipment-delete-info">i</span><div><h3>Itens vinculados</h3><p>Confira o que será preservado ou removido em cada opção.</p>{blockers}{ftp_note}</div></section>
            {push_note}{account_note}
            <div class="equipment-delete-options">
              <section class="equipment-delete-disable"><span class="equipment-option-icon">{icon('pause')}</span><div><h3>Desativar</h3><p>Interrompe novas operações e mantém cadastro, acessos, backups e histórico.</p></div><form method="post" action="/equipment/{item['id']}/deactivate"><button class="secondary" type="submit">Desativar equipamento</button></form></section>
              <section class="equipment-delete-archive"><span class="equipment-option-icon">{icon('archive')}</span><div class="equipment-option-copy"><h3>Arquivar</h3><p>Retira o equipamento da operação diária e permite preservar os arquivos ou enviá-los à lixeira.</p></div>{archive_control}</section>
            </div>
            {f'<section class="equipment-delete-danger"><div><span>{icon("warning")}</span><div><h3>Excluir definitivamente</h3><p>Apaga cadastro, credenciais, integrações, agendamentos, históricos, backups e arquivos físicos. Esta ação não pode ser desfeita.</p></div></div>{purge_control}</section>' if user["can_admin"] else ''}
            <footer class="equipment-delete-footer"><div class="actions">{f'<a class="button secondary" href="/equipment/{item["id"]}#ftp-push">Gerenciar backup FTP</a>' if accounts or integrations else ''}</div><div class="actions"><form method="dialog"><button type="submit" class="secondary">Cancelar</button></form>{f'<button type="submit" form="equipment-archive-{item["id"]}">Arquivar equipamento</button>' if not active_integrations and not dependencies["active_accounts"] else ''}</div></footer>
          </dialog>'''

    rows = "".join(
        f"""
        <tr data-equipment-row data-environment="{item['environment_id'] or ''}" data-group="{item['group_id'] or ''}" data-vendor="{item['vendor_id'] or ''}" data-pop="{item['pop_id'] or ''}" data-search="{e(' '.join(str(item[key] or '') for key in ('name', 'hostname', 'ip_address', 'vendor', 'group_name', 'pop', 'environment')).lower())}">
          <td><a class="equipment-name" href="/equipment/{item['id']}">{e(item['name'] or item['hostname'])}</a><small>{e(item['hostname'])}</small></td>
          <td>{e(item['environment'] or '—')}</td><td>{e(item['group_name'] or '—')}</td><td>{e(item['vendor'] or '—')}</td>
          <td>{e(item['pop'] or '—')}</td><td>{e(item['ip_address'])}</td><td>{method_label(item['ssh_backup_driver'])}</td>
          <td><span class="badge {'equipment-online' if item['is_active'] else 'equipment-offline'}">{'Online' if item['is_active'] else 'Offline'}</span></td>
          <td>{equipment_actions(item)}</td>
        </tr>
        """
        for item in equipment
    )
    create_panel = ""
    create_action = ""
    if can_operate(user):
        create_action = '<a class="button equipment-create-button" href="/equipment?new=1">Novo equipamento</a>'
        auto_open = ' data-dialog-auto-open="true"' if error else ""
        create_panel = f'''<dialog id="equipment-create" class="deployment-modal"{auto_open}>
          <form method="dialog"><button class="dialog-close" aria-label="Fechar">×</button></form>
          {equipment_form("/equipment", vendors, groups, pops, environments)}
          <form method="dialog"><button type="submit" class="secondary">Cancelar</button></form>
        </dialog>'''
    if new_mode and can_operate(user):
        def select_options(items, placeholder: str) -> str:
            return f'<option value="">{placeholder}</option>' + "".join(
                f'<option value="{item["id"]}">{e(item["name"])}</option>' for item in items
            )

        return f"""
        <div class="equipment-wizard-page" data-equipment-wizard>
          <nav class="breadcrumbs ftp-wizard-breadcrumbs"><a href="/">Início</a><span>/</span><a href="/equipment">Equipamentos</a><span>/</span><strong>Novo equipamento</strong></nav>
          <header class="ftp-wizard-head"><h1>Novo equipamento</h1><p>Cadastre um novo equipamento para iniciar o gerenciamento de backups.</p></header>
          {message("error", error)}
          <ol class="ftp-wizard-steps"><li class="is-active" data-equipment-step-indicator="1"><span>1</span><div><strong>Informações gerais</strong><small>Dados básicos do equipamento</small></div></li><li data-equipment-step-indicator="2"><span>2</span><div><strong>Integração e método</strong><small>Como será realizado o backup</small></div></li><li data-equipment-step-indicator="3"><span>3</span><div><strong>Credenciais</strong><small>Acesso ao equipamento</small></div></li><li data-equipment-step-indicator="4"><span>4</span><div><strong>Revisão e confirmação</strong><small>Confirme os dados informados</small></div></li></ol>
          <form method="post" action="/equipment" class="equipment-wizard-form" novalidate>
            <div class="equipment-wizard-main">
              <section class="panel ftp-wizard-panel" data-equipment-step="1"><h2>▣ &nbsp; Informações gerais</h2><div class="equipment-form-grid">
                <label>Nome do equipamento <em>*</em><input name="hostname" placeholder="Ex.: OLT Huawei 01" required><small>Nome amigável para identificação</small></label>
                <label>Hostname / IP <em>*</em><input name="ip_address" placeholder="10.10.10.11" inputmode="decimal" required><small>IP ou hostname para conexão</small></label>
                <label>Ambiente<select name="environment_id">{select_options(environments, 'Selecione o ambiente')}</select><small>Ambiente operacional</small></label>
                <label>Fabricante / Vendor<select name="vendor_id">{select_options(vendors, 'Selecione o fabricante')}</select><small>Fabricante do equipamento</small></label>
                <label>Grupo / Categoria<select name="group_id">{select_options(groups, 'Selecione o grupo')}</select><small>Grupo ou categoria do equipamento</small></label>
                <label>Site / Localidade<select name="pop_id">{select_options(pops, 'Selecione a localidade')}</select><small>Local onde o equipamento está instalado</small></label>
                <label class="ftp-wide">Descrição<textarea name="notes" rows="3" placeholder="Informações adicionais sobre o equipamento"></textarea><small>Campo opcional</small></label>
              </div></section>
              <section class="panel ftp-wizard-panel" data-equipment-step="2" hidden><h2>Integração e método</h2><div class="equipment-form-grid">
                <label class="ftp-wide">Tipo de equipamento e acesso <em>*</em><select name="ssh_backup_driver" data-connection-method required>{driver_options('') if driver_options else ''}</select><small>Define o driver usado para acessar e administrar o equipamento.</small></label>
                <fieldset class="ftp-wide mikrotik-backup-choice" data-mikrotik-backup-mode hidden><legend>Escolha o método do backup <em>*</em></legend>
                  <label><input type="radio" name="mikrotik_backup_mode" value="ftp" checked><span><strong>Backup por FTP Push</strong><small>O MikroTik executa o agendamento e envia os arquivos ao servidor FTP.</small></span></label>
                  <label><input type="radio" name="mikrotik_backup_mode" value="ssh"><span><strong>Backup via SSH</strong><small>O Backup Manager executa e agenda o backup acessando o MikroTik por SSH.</small></span></label>
                  <p>Escolha apenas um método para evitar backups e agendamentos duplicados.</p>
                </fieldset>
                <label>Porta de acesso<input name="ssh_port" data-access-port value="22" inputmode="numeric"><small>Porta padrão de conexão</small></label>
                <label>Status<input value="Ativo" disabled><small>O equipamento será habilitado após o cadastro</small></label>
              </div><div class="ftp-how-it-works"><h3>ⓘ &nbsp; Acesso e método</h3><p>Primeiro escolha o driver do equipamento. Para MikroTik, escolha também se o backup será realizado por FTP Push ou via SSH.</p></div></section>
              <section class="panel ftp-wizard-panel" data-equipment-step="3" hidden><h2>Credenciais de acesso</h2><div class="equipment-form-grid">
                <label>Usuário SSH <em>*</em><input name="ssh_username" autocomplete="username" placeholder="Ex.: backup" required><small>Usuário utilizado para acessar o equipamento</small></label>
                <label>Nome da credencial<input name="credential_name" value="SSH principal" placeholder="Ex.: SSH principal"><small>Identificação interna da credencial</small></label>
                <label>Senha SSH <em>*</em><input name="ssh_password" type="password" autocomplete="new-password" minlength="5" maxlength="128" required><small data-ssh-password-strength>Mínimo de 5 caracteres; recomendamos 10 ou mais.</small></label>
                <label>Confirmar senha <em>*</em><input name="ssh_confirm_password" type="password" autocomplete="new-password" minlength="5" maxlength="128" required><small>Digite novamente a senha SSH.</small></label>
              </div><div class="ftp-security-note success"><span>🔐</span><div><strong>Credenciais protegidas</strong><p>Usuário e senha serão criptografados e vinculados ao equipamento durante o cadastro.</p></div></div></section>
              <section class="panel ftp-wizard-panel" data-equipment-step="4" hidden><h2>Revisão e confirmação</h2><p>Confira os dados antes de cadastrar o equipamento.</p><dl class="ftp-final-review" data-equipment-final-review></dl><div class="ftp-security-note success"><span>✓</span><div><strong>Pronto para concluir</strong><p>Após o cadastro você poderá configurar credenciais e testar a conexão.</p></div></div></section>
              <div class="ftp-wizard-actions"><a class="button secondary" href="/equipment">Cancelar</a><div><button type="button" class="secondary" data-equipment-previous hidden>← Voltar</button><button type="button" data-equipment-next>Próximo passo →</button><button type="submit" data-equipment-submit hidden>Cadastrar equipamento</button></div></div>
            </div>
            <aside class="ftp-wizard-aside"><section class="panel"><h2>Resumo do cadastro</h2><dl><dt>Nome</dt><dd data-equipment-summary="name">-</dd><dt>IP / Host</dt><dd data-equipment-summary="ip">-</dd><dt>Ambiente</dt><dd data-equipment-summary="environment">-</dd><dt>Fabricante</dt><dd data-equipment-summary="vendor">-</dd><dt>Grupo</dt><dd data-equipment-summary="group">-</dd><dt>Método</dt><dd data-equipment-summary="method">-</dd><dt>Status</dt><dd><span class="badge equipment-online">Ativo</span></dd></dl></section><section class="ftp-security-note"><span>♙</span><div><strong>Seus dados estão seguros</strong><p>As credenciais informadas são criptografadas e armazenadas com segurança.</p></div></section></aside>
          </form>
        </div>"""
    total = len(equipment)
    online = sum(bool(item["is_active"]) for item in equipment)
    offline = total - online
    attention = sum(bool(dependency_map[item["id"]]["active_integrations"]) for item in equipment)
    return f"""
    <div class="equipment-page">
    <header class="page-head equipment-head"><div><h1>Equipamentos</h1><p>Gerencie os equipamentos da instalação</p></div><div class="actions">{create_action}</div></header>
    {message("error", error)}
    {create_panel}
    <section class="panel equipment-filters" aria-label="Filtros de equipamentos">
      <label>Ambiente<select data-equipment-filter="environment">{options(environments)}</select></label>
      <label>Grupo<select data-equipment-filter="group">{options(groups)}</select></label>
      <label>Fabricante<select data-equipment-filter="vendor">{options(vendors)}</select></label>
      <label>POP<select data-equipment-filter="pop">{options(pops)}</select></label>
      <label class="equipment-search"><span class="sr-only">Buscar equipamento</span><input type="search" data-equipment-search placeholder="⌕  Buscar equipamento..."></label>
    </section>
    <section class="panel equipment-summary" aria-label="Resumo dos equipamentos">
      <div class="summary-item summary-total"><span class="summary-icon">{summary_icons['total']}</span><strong>{total}<small>equipamentos</small></strong></div>
      <div class="summary-item summary-online"><span class="summary-icon">{summary_icons['online']}</span><strong>{online}<small>online</small></strong></div>
      <div class="summary-item summary-offline"><span class="summary-icon">{summary_icons['offline']}</span><strong>{offline}<small>offline</small></strong></div>
      <div class="summary-item summary-attention"><span class="summary-icon">{summary_icons['attention']}</span><strong>{attention}<small>com atenção</small></strong></div>
    </section>
    <section class="panel table-panel equipment-table"><table><thead><tr><th>Nome</th><th>Ambiente</th><th>Grupo</th><th>Fabricante</th><th>POP / Localidade</th><th>IP / Host</th><th>Método</th><th>Status</th><th>Ações</th></tr></thead><tbody>{rows}<tr class="equipment-empty" hidden><td colspan="9">Nenhum equipamento encontrado.</td></tr></tbody></table><footer><span>Mostrando <strong data-equipment-visible>{total}</strong> de {total} registros</span></footer></section>
    </div>
    """
