from __future__ import annotations

from html import escape

from .ssh import SSH_DRIVER_LABELS, driver_group, is_ftp_push_olt_driver
from .equipment_compatibility import vendor_key


def e(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def equipment_detail_content(
    *,
    user,
    item,
    rows,
    jobs,
    credentials,
    last_ssh_backup,
    last_ssh_run,
    config,
    timezone_name: str,
    artifacts_v2: bool,
    default_ftp_host: str,
    default_ftp_port: str,
    mikrotik_snapshot: dict[str, object],
    csrf_token: str,
    olt_operation,
    olt_artifacts,
    equipment_id: int,
    onboarding: str,
    error: str,
    success: str,
    info: str,
    local_dt,
    hidden,
    can_operate,
    message,
    backup_table,
    credential_rows,
    ssh_status_panel,
    ssh_run_panel,
    job_table,
    generate_mikrotik_removal_script,
    ftp_operation_payload,
    job_form,
    is_mikrotik_equipment,
    mask_username,
    mikrotik_equipment_panel,
    format_size,
) -> str:
    ftp_account = mikrotik_snapshot["ftp_account"]
    ftp_count = mikrotik_snapshot["ftp_count"]
    mikrotik_ftp = mikrotik_snapshot["mikrotik_ftp"]
    mikrotik_last_test = mikrotik_snapshot["mikrotik_last_test"]
    mikrotik_last_upload = mikrotik_snapshot["mikrotik_last_upload"]
    mikrotik_operation = mikrotik_snapshot["mikrotik_operation"]
    mikrotik_artifacts = mikrotik_snapshot["mikrotik_artifacts"]
    mikrotik_install_event = mikrotik_snapshot["mikrotik_install_event"]
    ftp_push_olt = is_ftp_push_olt_driver(item["ssh_backup_driver"] or "")
    mikrotik_inconsistent = ((vendor_key(item["vendor"]) == "mikrotik") !=
                             ((item["ssh_backup_driver"] or "") == "mikrotik_routeros"))
    compatibility_notice = ('''<div class="notice warning"><strong>Cadastro inconsistente.</strong>
      <p>Fabricante e driver MikroTik não correspondem. As automações RouterOS estão bloqueadas até a revisão do cadastro.</p></div>'''
                            if mikrotik_inconsistent else "")
    onboarding_ready = any(credential["is_active"] and credential["last_test_status"] == "success" for credential in credentials)
    quick_actions = f'<a class="button secondary" href="#historico" data-equipment-tab-link="historico">Ver histórico</a>'
    if can_operate(user):
        run_now = "" if ftp_push_olt else f'''<form method="post" action="/equipment/{equipment_id}/jobs/run-now">{hidden('method', 'ssh')}<button type="submit">Executar backup agora</button></form>'''
        quick_actions = f"""
          <a class="button secondary" href="/equipment/{equipment_id}/edit">Editar equipamento</a>
          {run_now}
          <form method="post" action="/equipment/{equipment_id}/ssh-test"><button class="secondary" type="submit">Testar conexão</button></form>
          <a class="button secondary" href="#backup" data-equipment-tab-link="backup">Configurar backup</a>
        """
    jobs_controls = ""
    jobs_create_action = ""
    if can_operate(user):
        jobs_create_action = '<button type="button" data-dialog-open="equipment-job-create">Criar agendamento</button>'
        jobs_controls = f"""
        <dialog id="equipment-job-create" class="equipment-job-dialog"{' data-dialog-auto-open' if onboarding == 'ssh' and onboarding_ready else ''}><form method="dialog"><button class="dialog-close" aria-label="Fechar">×</button></form><div id="criar-job">{'<div class="notice success onboarding-ssh-confirmed"><strong>SSH validado com sucesso.</strong><p>Agora configure o agendamento. Ao salvar, o Backup Manager executará o primeiro backup SSH e mostrará o resultado.</p></div>' if onboarding == 'ssh' and onboarding_ready else ''}{job_form(equipment_id, timezone_name=timezone_name, olt_mode=ftp_push_olt, onboarding=onboarding == 'ssh')}</div></dialog>
        """
    credential_form = ""
    credential_add_action = ""
    credential_manage_actions = ""
    if can_operate(user):
        editable_credential = next((credential for credential in credentials if credential["is_active"]), credentials[0] if credentials else None)
        editing = editable_credential is not None
        dialog_id = "equipment-credential-edit" if editing else "equipment-credential-create"
        credential_add_action = f'<button type="button" data-dialog-open="{dialog_id}">{"Editar credencial" if editing else "Adicionar credencial"}</button>'
        credential_manage_actions = credential_add_action
        if editing:
            active_action = "Desativar" if editable_credential["is_active"] else "Ativar"
            credential_manage_actions += f'''<form method="post" action="/credentials/{editable_credential['id']}/toggle"><button class="secondary" type="submit">{active_action}</button></form><form method="post" action="/credentials/{editable_credential['id']}/delete" onsubmit="return confirm('Excluir credencial?')"><button class="danger" type="submit">Excluir</button></form>'''
        form_action = f'/credentials/{editable_credential["id"]}/edit' if editing else f'/equipment/{equipment_id}/credentials'
        credential_form = f"""
        <dialog id="{dialog_id}" class="equipment-credential-dialog"><form method="dialog"><button class="dialog-close" aria-label="Fechar">×</button></form><form method="post" action="{form_action}" class="equipment-credential-form">
          <header><div><h2>{'Editar credencial de acesso' if editing else 'Adicionar credencial de acesso'}</h2><p>{'Atualize os dados usados na conexão com o equipamento.' if editing else 'Cadastre os dados usados para conexão segura com o equipamento.'}</p></div><span class="badge status-{'success' if editing else 'warning'}">{'Credencial ativa' if editing else 'Nova credencial'}</span></header>
          <div class="equipment-credential-fields"><label>Nome da credencial<input name="name" value="{e(editable_credential['name']) if editing else ''}" placeholder="Ex.: Backup Manager" required></label>
          <label>Usuário de acesso<input name="username" value="{e(editable_credential['username']) if editing else ''}" autocomplete="off" placeholder="Usuário SSH" required></label>
          <label>Senha de acesso<input name="password" type="password" autocomplete="new-password" placeholder="{'Deixe vazio para manter a senha atual' if editing else 'Senha do equipamento'}" {'required' if not editing else ''}></label>
          <label>Porta de acesso<input name="port" value="{e(item['ssh_port'] or 22)}" inputmode="numeric"></label>
          <label>Status<select name="is_active"><option value="1" {'selected' if not editing or editable_credential['is_active'] else ''}>Ativa</option><option value="0" {'selected' if editing and not editable_credential['is_active'] else ''}>Inativa</option></select></label>
          <label class="equipment-credential-notes">Observações<textarea name="notes" rows="2" placeholder="Informações opcionais sobre esta credencial">{e(editable_credential['notes']) if editing else ''}</textarea></label></div>
          <footer><span>A senha é armazenada de forma protegida e nunca será exibida.</span><button type="submit">Salvar credencial</button></footer>
        </form></dialog>
        """
    display_name = item["name"] or item["hostname"]
    if ftp_account:
        ftp_panel = f"""<article id="recebimento-ftp" class="panel"><h2>Recebimento FTP</h2><dl class="details"><dt>Conta</dt><dd>{e(ftp_account['name'])}</dd>
          <dt>Usuário</dt><dd>{e(ftp_account['username'])}</dd><dt>Porta</dt><dd>{ftp_account['control_port']}</dd><dt>Modo</dt><dd>Passivo</dd>
          <dt>Permissão</dt><dd>{e(ftp_account['permission_mode'])}</dd><dt>IP permitido</dt><dd>{e(ftp_account['allowed_source_ip'] or ftp_account['allowed_source_cidr'] or 'Firewall')}</dd>
          <dt>Último upload</dt><dd>{e(local_dt(ftp_account['last_upload_at']))}</dd><dt>Backups FTP</dt><dd>{ftp_count}</dd><dt>Status</dt><dd>{'Ativa' if ftp_account['is_active'] else 'Inativa'} / {e(ftp_account['sync_status'])}</dd></dl>
          <a class="button secondary" href="/ftp/{ftp_account['id']}">Ver conta e uploads</a></article>"""
    else:
        if is_mikrotik_equipment(item, item["vendor"]):
            ftp_action = ('<button type="button" data-dialog-open="mikrotik-ftp-config">Configurar FTP Push</button>'
                          if can_operate(user) else "Nenhuma conta configurada.")
        else:
            ftp_action = f'<a class="button" href="/ftp?new=1&amp;equipment_id={equipment_id}">Criar conta FTP</a>' if user["can_admin"] else "Nenhuma conta configurada."
        ftp_panel = f'<article id="recebimento-ftp" class="panel"><h2>Recebimento FTP</h2><p>Nenhuma conta FTP vinculada.</p>{ftp_action}</article>'
    mikrotik_ftp_panel = ""
    if is_mikrotik_equipment(item, item["vendor"]):
        if mikrotik_ftp:
            mikrotik_ftp_panel = mikrotik_equipment_panel(
                item=item,
                mikrotik_ftp=mikrotik_ftp,
                mikrotik_last_test=mikrotik_last_test,
                mikrotik_last_upload=mikrotik_last_upload,
                mikrotik_operation=mikrotik_operation,
                mikrotik_artifacts=mikrotik_artifacts,
                mikrotik_install_event=mikrotik_install_event,
                artifacts_v2=artifacts_v2,
                ftp_account=ftp_account,
                ftp_count=ftp_count,
                local_dt=local_dt,
                can_operate=can_operate(user),
                mask_username=mask_username,
                generate_mikrotik_removal_script=generate_mikrotik_removal_script,
                ftp_operation_payload=ftp_operation_payload,
                job_form=job_form,
                equipment_id=equipment_id,
                onboarding=onboarding,
            )
        else:
            configure_action = '<button type="button" data-dialog-open="mikrotik-ftp-config">Configurar FTP Push</button>' if can_operate(user) else '<span class="muted">Somente administradores e operadores podem configurar.</span>'
            existing_account_notice = ""
            credential_choices = '<label><input type="radio" name="credential_action" value="isolated" checked> Criar nova credencial isolada</label>'
            if ftp_account and ftp_account["is_active"]:
                has_history = bool(ftp_count or ftp_account["last_upload_at"])
                password_recoverable = str(ftp_account["password_hash_or_secret_reference"] or "").startswith("fernet:")
                existing_account_notice = f'''<div class="mikrotik-account-ready"><span aria-hidden="true">✓</span><div><strong>Conta FTP pronta para uso</strong>
                  <p>O equipamento já possui uma conta ativa e ela pode ser vinculada ao FTP Push.</p>
                  <dl><dt>Usuário</dt><dd>{e(mask_username(ftp_account['username']))}</dd><dt>Último recebimento</dt><dd>{e(local_dt(ftp_account['last_upload_at']) or 'Ainda não utilizado')}</dd></dl></div></div>'''
                credential_choices = f'''<fieldset class="mikrotik-credential-choices"><legend>Como deseja continuar?</legend>
                  <label class="choice-card"><input type="radio" name="credential_action" value="reuse" {'checked' if password_recoverable else 'disabled'}><span><strong>Usar conta existente</strong><small>{'Mantém a credencial atual e preserva todo o histórico.' if password_recoverable else 'A senha atual não pode ser recuperada para gerar o script.'}</small></span></label>
                  <label class="choice-card"><input type="radio" name="credential_action" value="rotate" {'checked' if not password_recoverable else ''}><span><strong>Gerar uma nova senha</strong><small>Atualiza a senha desta conta antes de criar o script.</small></span></label></fieldset>'''
            elif ftp_account:
                existing_account_notice = f'''<div class="notice info"><strong>Existe uma conta FTP inativa; ela não bloqueia uma nova credencial.</strong>
                  <p>O usuário anterior era {e(mask_username(ftp_account['username']))}. O histórico permanece preservado.</p></div>'''
            configure_dialog = f'''<dialog id="mikrotik-ftp-config" class="deployment-modal mikrotik-config-wizard" data-mikrotik-config-wizard><form method="dialog"><button class="dialog-close" aria-label="Fechar">×</button></form>
              <header class="mikrotik-config-head"><span class="mikrotik-config-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 5h16v11H4zM8 20h8M12 16v4"/><path d="M8 9h.01M11 9h5M8 12h8"/></svg></span><div><span class="eyebrow">Backup automático</span><h2>Configurar FTP Push</h2><p>Prepare o recebimento e a rotina de backup do MikroTik.</p></div></header>
              {'<div class="notice success mikrotik-config-notice"><strong>SSH validado com sucesso.</strong><p>Continue com a configuração do FTP Push.</p></div>' if onboarding == 'ftp' and onboarding_ready else ''}
              <div class="mikrotik-config-stepper" aria-label="Etapas da configuração"><span data-config-step-marker="1" class="is-current"><b>1</b>Conta FTP</span><i></i><span data-config-step-marker="2"><b>2</b>Backup</span><i></i><span data-config-step-marker="3"><b>3</b>Destino</span></div>
              <form method="post" action="/equipment/{equipment_id}/mikrotik-ftp" data-mikrotik-config-form class="mikrotik-config-form">
                <input type="hidden" name="onboarding" value="ftp">
                <section data-config-step="1">{existing_account_notice}{credential_choices}</section>
                <section data-config-step="2" hidden><div class="wizard-field-grid">
                  <label>Versão RouterOS<select name="routeros_version"><option value="7">RouterOS v7</option><option value="6">RouterOS v6</option></select></label>
                  <label>Formato<select name="backup_format"><option value="both">.backup e .rsc</option><option value="backup">.backup</option><option value="rsc">.rsc</option></select></label>
                  <label>Modo<select name="schedule_mode" data-mikrotik-schedule><option value="scheduled">Diário</option><option value="manual">Manual</option></select></label>
                  <label data-mikrotik-schedule-time>Horário<input type="time" name="schedule_time" value="02:00" required></label>
                </div><div class="notice info">RouterOS 6 requer versão 6.43 ou superior. A instalação SSH confirmará a versão real antes de enviar arquivos.</div></section>
                <section data-config-step="3" hidden><div class="wizard-field-grid">
                  <label>Host FTP<input name="ftp_host" value="{e(default_ftp_host)}" required></label><label>Porta FTP<input name="ftp_port" value="{e(default_ftp_port)}" inputmode="numeric" required></label>
                  <label>Diretório remoto<input name="ftp_directory" value="/" required></label><label>Retenção em dias (opcional)<input name="retention_days" inputmode="numeric"></label>
                </div><div class="notice warning">FTP não possui criptografia. Use rede privada, VPN ou ambiente controlado.</div></section>
                <footer class="wizard-actions"><button type="button" class="secondary" data-config-back hidden>Voltar</button><button type="button" data-config-next>Continuar</button><button type="submit" data-config-submit hidden>Aplicar configuração</button></footer>
              </form></dialog>''' if can_operate(user) else ""
            setup_card = f'''<div id="ftp-push" class="actions mikrotik-setup-launcher">{configure_action}</div>'''
            mikrotik_ftp_panel = setup_card + configure_dialog
    if mikrotik_ftp:
        ftp_healthy = bool(mikrotik_ftp["is_active"] and mikrotik_ftp["account_is_active"] and
                           not mikrotik_ftp["account_deleted_at"] and
                           ((mikrotik_last_test and mikrotik_last_test["status"] == "validated") or mikrotik_ftp["last_success_at"]))
        backup_state = "Funcionando" if ftp_healthy else "Com problema"
        backup_state_class = "success" if ftp_healthy else "error"
        schedule_label = (f"Todos os dias às {e(mikrotik_ftp['schedule_time'])}"
                          if mikrotik_ftp["schedule_mode"] == "scheduled" else "Execução manual")
        format_label = {"backup": ".backup", "rsc": ".rsc", "both": ".backup e .rsc"}.get(
            mikrotik_ftp["backup_format"], mikrotik_ftp["backup_format"])
        problem_note = "" if ftp_healthy else '<p class="notice warning">A configuração FTP precisa de atenção. Consulte o diagnóstico antes de tentar novamente.</p>'
        backup_actions = (f'''<form method="post" data-mikrotik-operation="ftp" action="/mikrotik-ftp/{mikrotik_ftp['id']}/test">
              <button type="submit">Testar agora</button></form>
            <a class="button secondary" href="#diagnostico" data-equipment-tab-link="diagnostico">Ver diagnóstico</a>
            <form method="post" action="/mikrotik-ftp/{mikrotik_ftp['id']}/deactivate"><button class="danger" type="submit">Desativar</button></form>'''
            if mikrotik_ftp["is_active"] else
            '<a class="button secondary" href="#diagnostico" data-equipment-tab-link="diagnostico">Ver diagnóstico</a>')
        backup_overview_panel = f'''<article class="panel backup-overview-card"><div class="panel-heading"><div>
          <span class="eyebrow">Backup automático</span><h2>Envio FTP</h2></div><span class="badge status-{backup_state_class}">{backup_state}</span></div>
          <dl class="details"><dt>Método</dt><dd>Envio FTP</dd><dt>Formato</dt><dd>{e(format_label)}</dd>
          <dt>Horário</dt><dd>{schedule_label}</dd><dt>Último backup</dt><dd>{e(local_dt(mikrotik_ftp['last_success_at']) or 'Ainda não recebido')}</dd></dl>
          {problem_note}<div class="actions">{backup_actions}</div></article>'''
    elif is_mikrotik_equipment(item, item["vendor"]):
        backup_overview_panel = '''<article class="panel backup-overview-card"><div class="panel-heading"><div>
          <span class="eyebrow">Backup automático</span><h2>Não configurado</h2></div><span class="badge">Não configurado</span></div>
          <p>Escolha o método, o horário e o formato. Os detalhes técnicos serão preparados pelo sistema.</p>
          <button type="button" data-dialog-open="mikrotik-ftp-config">Configurar backup</button></article>'''
    elif ftp_push_olt:
        account_ready = bool(ftp_account and ftp_account["is_active"])
        zte_install = (f'''<form method="post" action="/equipment/{equipment_id}/olt-script" target="_blank">
          {hidden('csrf_token', csrf_token)}{hidden('action', 'install')}<button class="secondary" type="submit">Gerar instalação automática</button></form>'''
          if item["ssh_backup_driver"] == "zte_olt_ssh_ftp" and account_ready and can_operate(user) else "")
        manual_action = (f'''<form method="post" action="/equipment/{equipment_id}/olt-script" target="_blank">
          {hidden('csrf_token', csrf_token)}{hidden('action', 'manual')}<button type="submit">Gerar roteiro manual</button></form>'''
          if account_ready and can_operate(user) else "")
        execute_action = (f'''<form method="post" action="/equipment/{equipment_id}/olt-execute">
          {hidden('csrf_token', csrf_token)}{hidden('action', 'manual')}<button type="submit">Executar agora</button></form>'''
          if account_ready and credentials and can_operate(user) else "")
        operation_status = e(olt_operation["status"] if olt_operation else "ainda não executado")
        artifact_status = ("; ".join(f"{e(row['filename'])}: {e(row['state'])}" for row in olt_artifacts)
                           if olt_artifacts else "Nenhum arquivo aguardado")
        backup_overview_panel = f'''<article class="panel backup-overview-card"><div class="panel-heading"><div>
          <span class="eyebrow">Backup da OLT</span><h2>Envio para o servidor FTP</h2></div>
          <span class="badge status-{'success' if account_ready else 'warning'}">{'Conta FTP vinculada' if account_ready else 'Conta FTP necessária'}</span></div>
          <dl class="details"><dt>Método</dt><dd>{e(SSH_DRIVER_LABELS.get(item['ssh_backup_driver'], 'OLT via FTP'))}</dd>
          <dt>Transporte</dt><dd>Acesso remoto ao equipamento + recebimento FTP</dd>
          <dt>Última operação</dt><dd>{operation_status}</dd><dt>Arquivos</dt><dd>{artifact_status}</dd></dl>
          <p class="notice warning">FTP não utiliza criptografia. Use somente em rede privada, VPN ou ambiente controlado.</p>
          <p>O backup SSH genérico está desativado para este método, evitando comandos incompatíveis com a OLT.</p>
          <div class="actions">{execute_action}{manual_action}{zte_install}<a class="button secondary" href="#diagnostico" data-equipment-tab-link="diagnostico">Configurar acesso e FTP</a></div></article>'''
    else:
        ssh_configured = bool(credentials)
        backup_overview_panel = f'''<article class="panel backup-overview-card"><div class="panel-heading"><div>
          <span class="eyebrow">Backup</span><h2>Via SSH</h2></div><span class="badge status-{'success' if ssh_configured else 'warning'}">{'Configurado' if ssh_configured else 'Não configurado'}</span></div>
          <dl class="details"><dt>Método</dt><dd>SSH</dd><dt>Último backup</dt><dd>{e(local_dt(last_ssh_backup['received_at']) if last_ssh_backup else 'Ainda não executado')}</dd></dl>
          <div class="actions">{'' if not (can_operate(user) and ssh_configured) else f'<form method="post" action="/equipment/{equipment_id}/jobs/run-now">{hidden("method", "ssh")}<button type="submit">Executar backup agora</button></form>'}
          <a class="button secondary" href="#diagnostico" data-equipment-tab-link="diagnostico">Ver diagnóstico</a></div></article>'''

    def icon(path: str) -> str:
        return f'<svg class="equipment-ui-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="{path}"/></svg>'

    last_backup = rows[0] if rows else None
    active_credential = next((credential for credential in credentials if credential["is_active"]), None)
    backup_ok = bool(last_backup and last_backup["backup_status"] == "available")
    ftp_account_ready = bool(ftp_account and ftp_account["is_active"])
    ftp_push_ready = bool(mikrotik_ftp)
    ftp_configured = ftp_push_ready if is_mikrotik_equipment(item, item["vendor"]) else ftp_account_ready
    if ftp_push_ready:
        ftp_health_title, ftp_health_detail = "Configurado", "FTP Push vinculado ao equipamento"
    elif ftp_account_ready:
        ftp_health_title, ftp_health_detail = "Conta pronta", "FTP Push ainda não configurado"
    else:
        ftp_health_title, ftp_health_detail = "Atenção", "Conta FTP ainda não configurada"
    scheduled_job = next((job for job in jobs if job["status"] == "active" and job["schedule_enabled"] and job["schedule_type"] != "none"), None)
    method_name = SSH_DRIVER_LABELS.get(item["ssh_backup_driver"] or "", "SSH")
    is_vsol_telnet = item["ssh_backup_driver"] == "vsol_olt_telnet_cli"
    access_protocol = "Telnet" if is_vsol_telnet else "SSH"
    display_protocol = "FTP" if ftp_push_olt else access_protocol
    credential_name = e(active_credential["name"] if active_credential else "Nenhuma credencial ativa")
    credential_user = e(active_credential["username"] if active_credential else "-")
    credential_created_at = e(local_dt(active_credential["created_at"]) if active_credential and active_credential["created_at"] else "Data não informada")
    day_names = {"0": "Seg", "1": "Ter", "2": "Qua", "3": "Qui", "4": "Sex", "5": "Sáb", "6": "Dom"}
    method_labels = {"ssh": "Backup via SSH", "dry_run": "Simulação de teste", "manual": "Execução manual", "ftp": "Recebimento via FTP"}
    def equipment_schedule_label(job) -> str:
        if not job["schedule_enabled"] or job["schedule_type"] == "none": return "Execução manual"
        if job["schedule_type"] == "daily": return f"Diário às {job['schedule_time']}"
        if job["schedule_type"] == "weekly":
            days = ", ".join(day_names.get(value.strip(), value.strip()) for value in (job["schedule_days"] or "").split(",") if value.strip())
            return f"Semanal · {days or 'dias não definidos'} · {job['schedule_time']}"
        return str(job["schedule_type"]).replace("_", " ").title()
    equipment_job_rows = "".join(
        f'''<tr><td><strong>{e(method_labels.get(job['method'], str(job['method']).upper()))}</strong></td><td>{e(equipment_schedule_label(job))}</td><td><span class="badge status-{'success' if job['status'] == 'active' else 'warning'}">{'Ativo' if job['status'] == 'active' else 'Pausado' if job['status'] == 'paused' else e(job['status']).title()}</span></td><td>{e(local_dt(job['last_run_at']) if job['last_run_at'] else 'Ainda não executado')}</td><td>{e(local_dt(job['next_run_at']) if job['next_run_at'] and job['schedule_enabled'] and job['schedule_type'] != 'none' else 'Somente ao executar')}</td><td><details class="equipment-job-actions"><summary>Gerenciar</summary><div><a href="/jobs/{e(job['uuid'])}/runs">Ver execuções</a>{f'<form method="post" action="/jobs/{e(job["uuid"])}/run"><button type="submit">Executar agora</button></form>' if can_operate(user) else ''}{f'<form method="post" action="/jobs/{e(job["uuid"])}/pause"><button type="submit">Pausar agendamento</button></form>' if can_operate(user) and job['status'] == 'active' and job['schedule_enabled'] and job['schedule_type'] != 'none' else ''}{f'<form method="post" action="/jobs/{e(job["uuid"])}/resume"><button type="submit">Retomar agendamento</button></form>' if can_operate(user) and job['status'] == 'paused' and job['schedule_enabled'] and job['schedule_type'] != 'none' else ''}{f'<a href="/jobs/{e(job["uuid"])}/edit">Editar</a><form method="post" action="/jobs/{e(job["uuid"])}/delete" onsubmit="return confirm(\'Excluir este agendamento?\')"><button class="danger" type="submit">Excluir</button></form>' if can_operate(user) else ''}</div></details></td></tr>'''
        for job in jobs
    ) or '<tr><td colspan="6" class="muted">Nenhum agendamento configurado para este equipamento.</td></tr>'
    equipment_jobs_table = f'''<table class="equipment-jobs-modern"><thead><tr><th>Método</th><th>Agendamento</th><th>Status</th><th>Última execução</th><th>Próxima execução</th><th>Ações</th></tr></thead><tbody>{equipment_job_rows}</tbody></table>'''
    connection_state = "Saudável" if active_credential and item["is_active"] else "Requer atenção"
    connection_state_class = "success" if active_credential and item["is_active"] else "warning"
    diagnostic_status = active_credential["last_test_status"] if active_credential else ""
    diagnostic_at = local_dt(active_credential["last_test_at"]) if active_credential and active_credential["last_test_at"] else ""
    diagnostic_error = e(active_credential["last_test_error"] if active_credential and active_credential["last_test_error"] else "")
    diagnostic_ok = diagnostic_status == "success"
    if diagnostic_status:
        diagnostic_title = "Conexão funcionando normalmente" if diagnostic_ok else "Falha no teste de conexão"
        diagnostic_description = "O equipamento respondeu e a autenticação foi concluída com sucesso." if diagnostic_ok else (diagnostic_error or "O teste não pôde ser concluído.")
        diagnostic_badge = "Sucesso" if diagnostic_ok else "Falha"
        diagnostic_class = "success" if diagnostic_ok else "error"
    else:
        diagnostic_title = "Nenhum diagnóstico executado"
        diagnostic_description = "Execute o diagnóstico completo para verificar conexão e autenticação."
        diagnostic_badge = "Não testado"
        diagnostic_class = "warning"
    if ftp_account:
        ftp_config_button = f'<a class="button" href="/ftp/{ftp_account["id"]}">Gerenciar FTP</a>'
        ftp_missing_dialog = ""
    elif user["can_admin"]:
        ftp_config_button = '<button type="button" data-dialog-open="equipment-ftp-missing">Configurar FTP</button>'
        ftp_missing_dialog = f'''<dialog id="equipment-ftp-missing" class="equipment-confirm-dialog"><form method="dialog"><button class="dialog-close" aria-label="Fechar">×</button></form><div class="equipment-confirm-icon">{icon('M7 18H6a4 4 0 0 1-.6-8A6.5 6.5 0 0 1 18 9a4.5 4.5 0 0 1 0 9h-1M12 18V9m0 0-3 3m3-3 3 3')}</div><h2>FTP não configurado</h2><p>Este equipamento ainda não possui uma conta FTP. Deseja cadastrar uma agora?</p><div class="actions"><form method="dialog"><button class="secondary" type="submit">Cancelar</button></form><a class="button" href="/ftp?new=1&amp;equipment_id={equipment_id}">Cadastrar FTP agora</a></div></dialog>'''
    else:
        ftp_config_button = '<span class="muted">Somente administradores podem configurar FTP.</span>'
        ftp_missing_dialog = ""
    recent_backups = "".join(
        f'''<tr><td>{e(local_dt(row['received_at']))}</td><td>{e(row['original_filename'])}</td>
        <td>{e(row['source_method'].upper())}</td><td><span class="badge status-{'success' if row['backup_status'] == 'available' else 'warning'}">{'Sucesso' if row['backup_status'] == 'available' else e(row['backup_status'])}</span></td>
        <td><a class="equipment-download" aria-label="Baixar {e(row['original_filename'])}" href="/backups/{e(row['uuid'])}/download">{icon('M12 3v12m0 0 4-4m-4 4-4-4M5 19h14')}</a></td></tr>'''
        for row in rows[:3]
    ) or '<tr><td colspan="5" class="muted">Nenhum backup disponível.</td></tr>'
    history_period = "Todos os períodos"
    if rows:
        oldest_date = local_dt(rows[-1]["received_at"]).split(" ", 1)[0]
        newest_date = local_dt(rows[0]["received_at"]).split(" ", 1)[0]
        history_period = oldest_date if oldest_date == newest_date else f"{oldest_date} - {newest_date}"
    history_rows = "".join(
        f'''<tr><td>{e(local_dt(row['received_at']))}</td><td><a class="equipment-history-file" href="/backups/{e(row['uuid'])}">{e(row['original_filename'])}</a></td><td>{e(row['source_method'].upper())}</td><td><span class="badge status-{'success' if row['backup_status'] == 'available' else 'warning'}">{'Sucesso' if row['backup_status'] == 'available' else e(row['backup_status']).title()}</span></td><td>{format_size(row['file_size'])}</td><td><div class="equipment-history-actions"><a href="/backups/{e(row['uuid'])}" aria-label="Ver detalhes" title="Ver detalhes">{icon('M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Zm9.5 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z')}</a>{f'<a href="/backups/{e(row["uuid"])}/download" aria-label="Baixar" title="Baixar">{icon("M12 3v12m0 0 4-4m-4 4-4-4M5 19h14")}</a>' if row['backup_status'] == 'available' else ''}{f'<form method="post" action="/backups/{e(row["uuid"])}/trash" onsubmit="return confirm(\'Mover backup para a lixeira?\')"><button type="submit" aria-label="Mover para a lixeira" title="Mover para a lixeira">{icon("M4 6h16M9 6V4h6v2m-9 0 1 15h10l1-15M10 10v7m4-7v7")}</button></form>' if user['can_admin'] and row['backup_status'] == 'available' else ''}</div></td></tr>'''
        for row in rows
    ) or '<tr><td colspan="6" class="muted">Nenhum backup encontrado para este equipamento.</td></tr>'
    history_table = f'''<table><thead><tr><th>Data e hora</th><th>Arquivo</th><th>Método</th><th>Status</th><th>Tamanho</th><th>Ações</th></tr></thead><tbody>{history_rows}</tbody></table><footer class="equipment-history-footer"><span>Mostrando {len(rows)} de {len(rows)} resultados</span></footer>'''
    header_actions = f'''<form method="post" action="/equipment/{equipment_id}/ssh-test"><button class="secondary" type="submit">{icon('M2 8.8a14 14 0 0 1 20 0M5 12a10 10 0 0 1 14 0m-10 4a4 4 0 0 1 6 0M12 20h.01')}Testar conexão</button></form>
      {'' if ftp_push_olt else f'<form method="post" action="/equipment/{equipment_id}/jobs/run-now">{hidden("method", "ssh")}<button type="submit">{icon("m8 5 11 7-11 7Z")}Executar backup agora</button></form>'}
      <a class="button secondary" href="/equipment/{equipment_id}/edit">{icon('M4 16v4h4L19 9l-4-4L4 16Zm9-9 4 4')}Editar equipamento</a>''' if can_operate(user) else ''

    content = f"""
    <div class="equipment-detail-page">{compatibility_notice}
    <nav class="breadcrumbs ftp-wizard-breadcrumbs"><a href="/">Início</a><span>/</span><a href="/equipment">Equipamentos</a><span>/</span><strong>{e(display_name)}</strong></nav>
    <header class="equipment-detail-hero">
      <div><span class="eyebrow">Equipamento</span><h1>{e(display_name)}</h1><p>{e(item['hostname'])} · {e(item['ip_address'])}</p>
      <div class="equipment-hero-badges"><span class="badge {'equipment-online' if item['is_active'] else 'equipment-offline'}">● &nbsp;{'Ativo' if item['is_active'] else 'Inativo'}</span><span class="badge equipment-vendor">{e(item['vendor'] or 'Genérico')}</span><span class="badge equipment-method">{e(driver_group(item['ssh_backup_driver'] or '') or 'SSH')}</span><span class="badge equipment-environment">{e(item['environment'] or 'Sem ambiente')}</span>{'<span class="badge status-warning">⚠ FTP com atenção</span>' if not ftp_configured else ''}</div></div>
      <div class="equipment-hero-actions">{header_actions}</div>
    </header>
    {message("error", error)}{message("success", success)}{message("info", info)}
    <nav class="equipment-tabs equipment-detail-tabs" aria-label="Áreas do equipamento">
      <a href="#resumo" data-equipment-tab-link="resumo">Resumo geral</a>
      <a href="#historico" data-equipment-tab-link="historico">Histórico</a>
      <a href="#diagnostico" data-equipment-tab-link="diagnostico">Diagnóstico</a>
      <a href="#backup" data-equipment-tab-link="backup">Configuração avançada</a>
    </nav>
    <section id="resumo" class="equipment-tab-panel" data-equipment-tab-panel="resumo">
    <section class="equipment-health-grid">
      <article class="equipment-health-card state-green"><span class="health-icon"><svg class="equipment-ui-icon equipment-ssh-icon" aria-hidden="true" viewBox="0 0 24 24"><rect x="3.5" y="4.5" width="17" height="15" rx="2.2"/><path d="m7.5 9 3 3-3 3m5.5 0h3.5"/></svg></span><div><strong>Acesso {display_protocol}</strong><b>{'Sucesso' if backup_ok else 'Pendente'}</b><small>Último backup<br>{e(local_dt(last_backup['received_at']) if last_backup else 'Ainda não realizado')}</small></div></article>
      <article class="equipment-health-card state-orange"><span class="health-icon">{icon('M7 18H6a4 4 0 0 1-.6-8A6.5 6.5 0 0 1 18 9a4.5 4.5 0 0 1 0 9h-1M12 18V9m0 0-3 3m3-3 3 3')}</span><div><strong>Envio FTP</strong><b>{ftp_health_title}</b><small>{ftp_health_detail}</small></div></article>
      <article class="equipment-health-card state-blue"><span class="health-icon">{icon('M6 3v3m12-3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v14H4V6a1 1 0 0 1 1-1Z')}</span><div><strong>Agendamento</strong><b>{'Ativo' if scheduled_job else 'Manual'}</b><small>{'Rotina automática configurada' if scheduled_job else 'Próxima execução: sob demanda'}</small></div></article>
      <article class="equipment-health-card state-green"><span class="health-icon">{icon('M7 10V7a5 5 0 0 1 10 0v3M5 10h14v11H5Z M12 14v3')}</span><div><strong>Credencial {display_protocol}</strong><b>{'Ativa' if active_credential else 'Ausente'}</b><small>Porta {e(item['ssh_port'] or 22)}{f' · {e(mask_username(active_credential["username"]))}' if active_credential else ''}</small></div></article>
    </section>
    <section class="equipment-overview-grid">
      <article class="panel equipment-main-info"><h2>Informações principais</h2><dl><dt>Nome:</dt><dd>{e(display_name)}</dd><dt>POP / Localidade:</dt><dd>{e(item['pop'] or '-')}</dd><dt>Hostname:</dt><dd>{e(item['hostname'])}</dd><dt>Ambiente:</dt><dd>{e(item['environment'] or '-')}</dd><dt>IP / Host:</dt><dd>{e(item['ip_address'])}</dd><dt>Método de conexão:</dt><dd>{e(method_name)}</dd><dt>Fabricante:</dt><dd>{e(item['vendor'] or '-')}</dd><dt>Grupo:</dt><dd>{e(item['group_name'] or '-')}</dd><dt class="wide">Observações:</dt><dd class="wide">{e(item['notes'] or 'Sem observações.')}</dd></dl></article>
      <div class="equipment-overview-side"><article class="panel"><h2>Ações rápidas</h2><div class="equipment-quick-grid"><a href="#historico" data-equipment-tab-link="historico">{icon('M4 12a8 8 0 1 0 2-5M4 4v5h5M12 8v5l3 2')}Ver histórico</a><a href="#diagnostico" data-equipment-tab-link="diagnostico">{icon('M3 12h4l2-6 4 12 2-6h6')}Ver diagnóstico</a><a href="#backup" data-equipment-tab-link="backup">{icon('M12 16V4m0 0-4 4m4-4 4 4M5 13a4 4 0 0 0 1 7h12a4 4 0 0 0 1-7')}Corrigir FTP</a><a href="#backup" data-equipment-tab-link="backup">{icon('M12 3c5 0 8 2 8 4s-3 4-8 4-8-2-8-4 3-4 8-4Zm-8 4v10c0 2 3 4 8 4s8-2 8-4V7M4 12c0 2 3 4 8 4s8-2 8-4')}Gerenciar backup</a></div></article>
      <article class="panel equipment-mini-diagnostic"><h2>Diagnóstico simplificado</h2><ul><li class="ok">Conexão {access_protocol}:<span>OK</span></li><li class="ok">Credencial {access_protocol}:<span>{'OK' if active_credential else 'Pendente'}</span></li><li class="{'ok' if scheduled_job else 'warn'}">Scheduler:<span>{'Ativo' if scheduled_job else 'Desativado (manual)'}</span></li><li class="{'ok' if ftp_configured else 'warn'}">Configuração FTP:<span>{'OK' if ftp_configured else 'Atenção'}</span></li></ul></article></div>
    </section>
    <section class="equipment-bottom-grid"><article class="panel equipment-recent"><h2>Últimos backups</h2><table><thead><tr><th>Data / Hora</th><th>Arquivo</th><th>Método</th><th>Status</th><th>Ação</th></tr></thead><tbody>{recent_backups}</tbody></table></article><article class="panel equipment-next-steps"><h2>Próximos passos recomendados</h2><ul><li>{icon('M7 18H6a4 4 0 0 1-.6-8A6.5 6.5 0 0 1 18 9a4.5 4.5 0 0 1 0 9h-1M12 18V9m0 0-3 3m3-3 3 3')}<div><strong>Validar recebimento FTP</strong><small>Confirme se o servidor recebeu o último arquivo enviado.</small></div></li><li>{icon('M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM5 21a7 7 0 0 1 14 0')}<div><strong>Vincular conta FTP</strong><small>Associe uma conta FTP válida para receber os backups.</small></div></li><li>{icon('M6 3v3m12-3v3M4 9h16M5 5h14v15H5ZM8 14l2 2 5-5')}<div><strong>Ativar agendamento diário (opcional)</strong><small>Automatize backups diários para maior segurança.</small></div></li></ul><a class="button" href="#backup" data-equipment-tab-link="backup">Resolver agora</a></article></section>
    </section>
    <section id="backup" class="equipment-tab-panel" data-equipment-tab-panel="backup" hidden>
      <header class="equipment-section-heading"><div><h2>Configuração avançada</h2><p>Backup e integrações: gerencie o backup automático e as opções técnicas.</p></div></header>
      <section class="equipment-config-grid">
        <article class="panel"><div class="equipment-card-title"><h2>Conta FTP</h2><span class="badge status-{'success' if ftp_account_ready else 'warning'}">{'Pronta' if ftp_account_ready else 'Não configurada'}</span></div><p>{'Conta FTP vinculada e disponível para recebimento.' if ftp_account_ready else 'Nenhuma conta FTP vinculada a este equipamento.'}</p>{ftp_config_button}</article>
      </section>
      {mikrotik_ftp_panel}
      {ftp_missing_dialog}
      <section id="agendamentos-backup" class="equipment-schedule-section"><div class="equipment-section-heading"><div><div class="equipment-schedule-title"><h2>Agendamentos de backup</h2><span class="badge status-{'success' if scheduled_job else 'warning'}">{'Configurado' if scheduled_job else 'Não configurado'}</span></div><p>Configure e acompanhe a execução automática deste equipamento.</p></div>{jobs_create_action}</div><section class="panel table-panel"><h2>Agendamentos configurados</h2>{equipment_jobs_table}</section>{jobs_controls}</section>
    </section>
    <section id="historico" class="equipment-tab-panel" data-equipment-tab-panel="historico" hidden>
      <header class="equipment-section-heading"><div><h2>Histórico de backups</h2><p>Lista de backups realizados neste equipamento. Consulte arquivos, datas, métodos e status.</p></div></header>
      <div class="equipment-history-toolbar" aria-label="Filtros do histórico" data-equipment-history-filters><details class="equipment-history-period"><summary aria-label="Selecionar período"><span data-history-period-label>{e(history_period)}</span>{icon('M6 3v3m12-3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v13H4V6a1 1 0 0 1 1-1Z')}</summary><div class="equipment-history-calendar"><label>Data inicial<input type="date" data-history-date-from></label><label>Data final<input type="date" data-history-date-to></label><div><button class="secondary" type="button" data-history-date-clear>Limpar</button><button type="button" data-history-date-apply>Aplicar</button></div></div></details><label><span class="sr-only">Método</span><select data-history-method aria-label="Método"><option value="">Todos os métodos</option><option value="ssh">SSH</option><option value="ftp">FTP</option><option value="manual">Manual</option></select></label><label><span class="sr-only">Status</span><select data-history-status aria-label="Status"><option value="">Todos os status</option><option value="sucesso">Sucesso</option><option value="falha">Falha</option></select></label><label class="equipment-history-search"><span class="sr-only">Buscar arquivo</span><input type="search" data-history-search aria-label="Buscar arquivo" placeholder="Buscar arquivo..."></label><button class="secondary" type="button">Mais filtros</button></div>
      <section class="panel table-panel equipment-modern-table">{history_table}</section>
    </section>
    <section id="diagnostico" class="equipment-tab-panel" data-equipment-tab-panel="diagnostico" hidden>
      <header class="equipment-section-heading"><div><h2>Diagnóstico</h2><p>Acesso e integridade: teste a conexão, credenciais e o funcionamento do equipamento.</p></div>{f'<form method="post" action="/equipment/{equipment_id}/ssh-test"><button type="submit">Executar diagnóstico completo</button></form>' if can_operate(user) else ''}</header>
      <section class="equipment-diagnostic-grid">
        <article class="panel"><h2>1. Estado da conexão</h2><dl><dt>Conexão {access_protocol}</dt><dd><span class="badge status-{connection_state_class}">{connection_state}</span></dd><dt>Credencial ativa</dt><dd>{credential_name}</dd><dt>Último backup</dt><dd>{e(local_dt(last_backup['received_at']) if last_backup else 'Ainda não realizado')}</dd><dt>Porta</dt><dd>{e(item['ssh_port'] or 22)}</dd></dl><div class="actions"><form method="post" action="/equipment/{equipment_id}/ssh-test"><button type="submit">Testar conexão {access_protocol}</button></form></div></article>
        <article class="panel equipment-diagnostic-credential-card"><header><h2>2. Credencial ativa</h2></header><div class="equipment-credential-summary"><span class="equipment-avatar">{icon('M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM5 21a7 7 0 0 1 14 0')}</span><div><strong>{credential_name}</strong><small>Usuário: {credential_user}<span aria-hidden="true"> · </span>Porta: {e(item['ssh_port'] or 22)}<span class="equipment-inline-state {'active' if active_credential else 'inactive'}">{'Ativa' if active_credential else 'Não configurada'}</span></small><small>Criada em {credential_created_at}</small></div></div><footer class="actions equipment-credential-card-actions">{f'<button type="button" class="secondary" data-dialog-open="{dialog_id}">Editar</button><button type="button" class="secondary" data-dialog-open="{dialog_id}">Substituir</button><form method="post" action="/credentials/{active_credential["id"]}/toggle"><button class="danger" type="submit">Desativar</button></form>' if can_operate(user) and active_credential else (credential_add_action or '<span class="muted">Somente leitura</span>')}</footer></article>
        <article class="panel equipment-last-diagnostic"><div class="equipment-card-title"><h2>3. Último diagnóstico</h2><span class="badge status-{diagnostic_class}">{diagnostic_badge}</span></div><div class="notice {diagnostic_class}"><strong>{diagnostic_title}</strong><p>{diagnostic_description}</p></div><div class="actions equipment-diagnostic-footer"><button class="secondary" type="button" data-dialog-open="equipment-diagnostic-detail">Ver detalhes do diagnóstico</button></div></article>
        <dialog id="equipment-diagnostic-detail" class="equipment-diagnostic-dialog"><form method="dialog"><button class="dialog-close" aria-label="Fechar">×</button></form><header><div><span class="eyebrow">Diagnóstico {access_protocol}</span><h2>Detalhes do último teste</h2><p>Informações da conexão e autenticação do equipamento.</p></div><span class="badge status-{diagnostic_class}">{diagnostic_badge}</span></header><dl class="equipment-diagnostic-details"><dt>Executado em</dt><dd>{e(diagnostic_at or 'Ainda não executado')}</dd><dt>Método de conexão</dt><dd>{e(method_name)}</dd><dt>Destino testado</dt><dd>{e(item['ip_address'])}:{e(item['ssh_port'] or 22)}</dd><dt>Credencial utilizada</dt><dd>{credential_name} · {credential_user}</dd><dt>Resultado</dt><dd>{diagnostic_badge}</dd><dt>Mensagem</dt><dd class="{'diagnostic-error-detail' if diagnostic_error else ''}">{diagnostic_error or diagnostic_description}</dd></dl><footer><form method="dialog"><button class="secondary" type="submit">Fechar</button></form></footer></dialog>
      </section>
      {credential_form}
    </section>
    </div>
    """
    return content
