from __future__ import annotations

from html import escape as e

from .mikrotik_ftp_scripts import SCRIPT_VERSION
from .storage import format_size


STATUS_LABELS = {
    "done": "Concluído", "completed": "Concluído", "validated": "Validado",
    "installed_valid": "Instalação válida", "installed": "Instalado",
    "repaired": "Corrigido", "needs_repair": "Precisa de atualização",
    "pending": "Pendente", "running": "Em andamento", "waiting_upload": "Aguardando arquivo",
    "validating": "Validando", "failed": "Falhou", "expired": "Expirado",
    "cancelled": "Cancelado", "not_executed": "Não executado", "unknown": "Desconhecido",
    "not_tested": "Não testado", "ready": "Pronto",
}


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status.replace("_", " ").capitalize())


def _timeline_percent(timeline: list[dict]) -> int:
    if not timeline:
        return 0
    reached = sum(step.get("status") in {"done", "completed", "validated", "failed"} for step in timeline)
    return round(reached * 100 / len(timeline))


def mikrotik_equipment_panel(*, item, mikrotik_ftp, mikrotik_last_test, mikrotik_last_upload, mikrotik_operation, mikrotik_artifacts, mikrotik_install_event, artifacts_v2, ftp_account, ftp_count, local_dt, can_operate, mask_username, generate_mikrotik_removal_script, ftp_operation_payload, job_form, equipment_id: int, onboarding: str = "") -> str:
    next_run = "Calculada no próprio MikroTik após a instalação"
    try:
        import json
        install_details = json.loads(mikrotik_install_event["details"] or "{}") if mikrotik_install_event else {}
    except (TypeError, ValueError):
        install_details = {"installation_state": "unknown", "status": "unknown",
                           "message": "Não foi possível carregar o resultado da última operação.", "timeline": []}
    if mikrotik_install_event and mikrotik_install_event["action"] == "mikrotik_ftp.credential_rotated":
        install_details = {"installation_state": "not_installed", "status": "ready",
                           "message": "Senha FTP atualizada. A instalação do script está pendente.", "timeline": []}
    installation_status = install_details.get("installation_state", "not_installed")
    needs_credential_rotation = (install_details.get("error_code") == "validation_failed"
                                 and "senha FTP" in str(install_details.get("message", "")))
    installation_labels = {"not_installed": "Não instalado", "installed": "Instalado e atualizado", "needs_repair": "Precisa de reparo", "unknown": "Configuração incompleta"}
    test_status = "not_tested"
    if mikrotik_last_test:
        test_status = {"pending": "running", "running": "running", "waiting_upload": "running", "validating": "running",
                       "validated": "validated", "expired": "expired"}.get(mikrotik_last_test["status"], "failed")
    test_labels = {"not_tested": "Não testado", "running": "Testando", "validated": "Validado", "failed": "Falhou", "expired": "Expirado"}
    script_actions = ""
    if can_operate:
        if needs_credential_rotation:
            script_actions = f'''<div class="actions mikrotik-compact-actions"><form method="post" action="/mikrotik-ftp/{mikrotik_ftp['id']}/rotate" data-credential-rotation-form>
                  <button type="submit">Atualizar senha FTP</button></form></div>'''
        else:
            test_action = ('''<button class="secondary" type="button" data-dialog-open="mikrotik-ftp-test-progress">Testar envio FTP</button>'''
                           if installation_status == "installed" else "")
            primary_label = "Aplicar atualização" if installation_status == "needs_repair" else ("Gerenciar backup" if installation_status == "installed" else "Configurar backup")
            script_actions = f'''<div class="actions mikrotik-compact-actions">
                      <button type="button" data-dialog-open="mikrotik-install-wizard">{primary_label}</button>{test_action}
                    </div>'''
    install_timeline = "".join(f'''<li class="operation-step status-{e(step.get('status', 'pending'))}"><span>{e(step.get('step', ''))}</span><strong>{e(_status_label(step.get('status', 'pending')))}</strong></li>''' for step in install_details.get("timeline", []) if isinstance(step, dict))
    install_percent = _timeline_percent(install_details.get("timeline", []))
    install_result = f'''<details class="operation-result" data-operation-result="installation" data-state="{e(install_details.get('status', installation_status))}"{' hidden' if not mikrotik_install_event else ''}>
                <div class="operation-result-header"><h3>Última operação de instalação</h3><span class="badge" data-operation-status>{e(_status_label(install_details.get('status', installation_status)))}</span></div>
                <p data-operation-message>{e(install_details.get('message', ''))}</p><div class="operation-live-progress"><progress max="100" value="{install_percent}" data-operation-progress></progress><span data-operation-percent>{install_percent}%</span></div><summary><span data-details-label>Ver detalhes</span></summary><div class="operation-detail"><ol class="operation-timeline" data-operation-timeline>{install_timeline}</ol>
                <small data-operation-time>{e(local_dt(mikrotik_install_event['created_at'] if mikrotik_install_event else None))}</small></div></details>'''
    ftp_payload = ftp_operation_payload(mikrotik_last_test) if mikrotik_last_test else None
    ftp_active = bool(ftp_payload and not ftp_payload["terminal"])
    active_payload = ftp_payload if ftp_active else None
    ftp_timeline = "".join(f'''<li class="operation-step status-{e(step['status'])}"><span>{e(step['step'])}</span><strong>{e(_status_label(step['status']))}</strong></li>''' for step in (active_payload["timeline"] if active_payload else []))
    ftp_percent = _timeline_percent(active_payload["timeline"] if active_payload else [])
    ftp_result = f'''<dialog id="mikrotik-ftp-test-progress" class="deployment-modal ftp-test-progress"{' data-dialog-auto-open' if ftp_active else ''}>
              <div class="operation-result" data-operation-result="ftp" data-state="{e(active_payload['status'] if active_payload else 'ready')}" data-status-url="{f'/mikrotik-ftp/tests/{mikrotik_last_test["uuid"]}' if ftp_active else ''}" data-terminal="{str(bool(active_payload and active_payload['terminal'])).lower()}">
                <div class="operation-result-header"><h3>Teste de envio FTP</h3><span class="badge" data-operation-status>{e(_status_label(active_payload['status'] if active_payload else 'ready'))}</span></div>
                <p data-operation-message>{e(active_payload['message'] if active_payload else 'Clique em Iniciar teste FTP para começar.')}</p><p class="muted" data-operation-recommendation>{e(active_payload['action_recommendation'] if active_payload else '')}</p><div class="operation-live-progress"><progress max="100" value="{ftp_percent}" data-operation-progress></progress><span data-operation-percent>{ftp_percent}%</span></div><p data-operation-countdown></p><div class="operation-detail"><ol class="operation-timeline" data-operation-timeline>{ftp_timeline}</ol>
                <dl class="operation-metadata"><dt>Início</dt><dd data-operation-started>{e(local_dt(mikrotik_last_test['started_at'] if ftp_active else None))}</dd><dt>Prazo</dt><dd data-operation-expires>{e(local_dt(mikrotik_last_test['expires_at'] if ftp_active else None))}</dd><dt>Última verificação</dt><dd data-operation-checked>{e(local_dt(mikrotik_last_test['last_check_at'] if ftp_active and 'last_check_at' in mikrotik_last_test.keys() else None))}</dd><dt>Arquivo esperado</dt><dd data-operation-filename>{e(mikrotik_last_test['expected_filename'] if ftp_active and 'expected_filename' in mikrotik_last_test.keys() else '')}</dd></dl>
                <small data-operation-time></small></div></div>
              <footer class="wizard-actions"><small>Ao ser aprovado, este painel fechará automaticamente.</small><div class="actions"><button type="button" class="secondary" data-dialog-close>Fechar</button><form method="post" data-mikrotik-operation="ftp" action="/mikrotik-ftp/{mikrotik_ftp['id']}/test"><button type="submit" class="ftp-test-button" data-retry-ftp>Iniciar teste FTP</button></form></div></footer></dialog>'''
    removal_script = generate_mikrotik_removal_script(mikrotik_ftp["routeros_major_version"])
    artifact_rows = "".join(
        f"<tr><td>{e(row['artifact_type'])}</td><td>{'Sim' if row['is_required'] else 'Não'}</td>"
        f"<td>{e(row['state'])}</td><td>{e(format_size(row['size_bytes']) if row['size_bytes'] is not None else '-')}</td>"
        f"<td>{e(local_dt(row['received_at']))}</td><td>{e(row['validation_message'] or '-')}</td>"
        f"<td>{'Sim' if row['state'] == 'late' else 'Não'}</td></tr>" for row in mikrotik_artifacts
    )
    artifact_panel = ""
    if artifacts_v2:
        artifact_panel = f'''<article class="panel"><h2>Operação de artefatos</h2>
                  <dl class="details"><dt>Estado agregado</dt><dd>{e(mikrotik_operation['status'] if mikrotik_operation else 'sem operação')}</dd></dl>
                  <table><thead><tr><th>Tipo</th><th>Obrigatório</th><th>Estado</th><th>Tamanho</th><th>Recebido</th><th>Validação</th><th>Atrasado</th></tr></thead>
                  <tbody>{artifact_rows or '<tr><td colspan="7">Nenhum artefato esperado.</td></tr>'}</tbody></table></article>'''
    return f'''<article id="ftp-push" class="panel mikrotik-compact-card" data-mikrotik-ftp-panel><div class="equipment-card-title"><div><span class="eyebrow">MikroTik FTP Push</span><h2>Backup no MikroTik</h2></div><span class="badge status-{'success' if installation_status == 'installed' else 'warning'}" data-installation-summary>{e(installation_labels.get(installation_status, 'Estado desconhecido'))}</span></div>
              <p>{'Script instalado e pronto para validação.' if installation_status == 'installed' else ('Atualize a senha FTP para continuar a instalação.' if needs_credential_rotation else 'Instale e valide o script de backup no equipamento.')}</p>{script_actions}{install_result}</article>
              <article class="panel"><h2>Último teste FTP</h2><dl class="details"><dt>Status</dt><dd data-ftp-status>{e(test_labels[test_status])}</dd>
              <dt>Último teste</dt><dd>{e(local_dt(mikrotik_last_test['created_at'] if mikrotik_last_test else None))}</dd>
              <dt>Resultado</dt><dd data-ftp-summary>{e((ftp_payload['message']) if ftp_payload else 'FTP ainda não testado')}</dd></dl>
              <p class="muted" data-operation-recommendation>{e(ftp_payload['action_recommendation'] if ftp_payload else '')}</p></article>{ftp_result}
              <article class="panel" data-mikrotik-received-summary><h2>Backups recebidos</h2><dl class="details"><dt>Último arquivo</dt><dd data-received-filename>{e(mikrotik_last_upload['original_filename'] if mikrotik_last_upload else '-')}</dd>
              <dt>Tipo</dt><dd data-received-type>{e(mikrotik_last_upload['file_type'] if mikrotik_last_upload else '-')}</dd><dt>Tamanho</dt><dd data-received-size>{e(format_size(mikrotik_last_upload['size_bytes']) if mikrotik_last_upload else '-')}</dd>
              <dt>Recebido em</dt><dd data-received-at>{e(local_dt(mikrotik_last_upload['received_at'] if mikrotik_last_upload else None))}</dd></dl></article>{artifact_panel}
              <details class="panel"><summary>Opções avançadas</summary><div class="actions block-actions">
                <form method="post" action="/mikrotik-ftp/{mikrotik_ftp['id']}/script" data-manual-script-form><button type="submit" class="secondary">Gerar/copiar script manual</button></form>
                <form method="post" action="/mikrotik-ftp/{mikrotik_ftp['id']}/rotate" data-credential-rotation-form><button type="submit" class="secondary">Rotacionar senha FTP</button></form>
                <button type="button" class="secondary" data-dialog-open="mikrotik-ftp-removal">Script de remoção</button>
                <form method="post" action="/mikrotik-ftp/{mikrotik_ftp['id']}/remove-ssh" onsubmit="return confirm('Remover os objetos do Backup Manager no MikroTik via SSH e desativar a integração?')"><button type="submit" class="danger">Remover do MikroTik via SSH</button></form>
                <form method="post" action="/mikrotik-ftp/{mikrotik_ftp['id']}/deactivate"><button class="danger" type="submit">Desativar integração</button></form></div></details>
              <dialog id="mikrotik-ftp-removal" class="deployment-modal"><form method="dialog"><button class="dialog-close" aria-label="Fechar">×</button></form>
                <h2>Script de remoção</h2><pre><code data-copy-source="mikrotik-removal-script">{e(removal_script)}</code></pre>
                <div class="actions"><button type="button" class="secondary" data-copy-target="mikrotik-removal-script">Copiar</button><button type="button" class="secondary" data-dialog-close>Fechar</button></div></dialog>
              <dialog id="mikrotik-manual-script" class="deployment-modal manual-script-modal" aria-labelledby="mikrotik-manual-title">
                <header><h2 id="mikrotik-manual-title">Instalação manual no MikroTik</h2><button type="button" class="dialog-close" data-manual-close aria-label="Fechar instalação manual">×</button><p>Copie o script e execute no terminal do RouterOS.</p><div class="compact-badges"><span class="badge">RouterOS v{mikrotik_ftp['routeros_major_version']}</span><span class="badge">Instalação manual</span><span class="badge">Script v{e(SCRIPT_VERSION)}</span></div></header>
                <div class="manual-script-body"><ol><li>Abra o terminal do MikroTik.</li><li>Copie o script.</li><li>Cole no terminal.</li><li>Aguarde a conclusão.</li><li>Volte à tela do equipamento.</li><li>Verifique a instalação.</li><li>Teste o envio FTP separadamente.</li></ol><p><strong>O script instala os objetos. Ele não executa o teste FTP.</strong></p><pre><code data-manual-script-content></code></pre></div>
                <footer><p class="copy-feedback" data-copy-feedback aria-live="polite"></p><button type="button" class="secondary" data-manual-close>Fechar</button><button type="button" data-manual-copy disabled>Copiar script</button></footer>
              </dialog>
              <dialog id="mikrotik-install-wizard" class="deployment-modal mikrotik-install-wizard" aria-labelledby="mikrotik-install-title"{' data-dialog-auto-open' if onboarding == 'ftp-install' else ''}>
                <form method="dialog"><button class="dialog-close" aria-label="Fechar">×</button></form>
                <header><span class="eyebrow">Instalação v{e(SCRIPT_VERSION)}</span><h2 id="mikrotik-install-title">Instalação guiada no MikroTik</h2><p>Confirme o destino e acompanhe cada etapa.</p></header>
                <form method="post" data-mikrotik-operation="install" data-install-wizard-form data-script-version="{e(SCRIPT_VERSION)}" action="/mikrotik-ftp/{mikrotik_ftp['id']}/{'repair-ssh' if installation_status == 'needs_repair' else 'install-ssh'}">
                  <dl class="details wizard-target"><dt>Equipamento de destino</dt><dd>{e(item['name'])}</dd><dt>Endereço</dt><dd>{e(item['ip_address'])}</dd></dl>
                  <p>A versão será detectada antes do envio de arquivos. Nenhuma alteração será feita em versões incompatíveis.</p>
                  <div class="wizard-progress"><div><strong data-install-stage>Pronto para iniciar</strong><span data-install-percent>0%</span></div><progress max="100" value="0" data-install-progress>0%</progress><p data-install-error hidden role="alert"></p></div>
                  <ol class="wizard-checklist"><li>Verificar os objetos existentes</li><li>Instalar o script e o scheduler</li><li>Validar a instalação</li></ol>
                  <label class="choice-card"><input type="checkbox" data-install-run-after checked><span><strong>Executar um backup completo ao concluir</strong><small>Executa o script no MikroTik e mostra sucesso ou o erro retornado pelo RouterOS.</small></span></label>
                  <footer class="wizard-actions"><button type="button" class="secondary" data-dialog-close>Cancelar</button><button type="submit">Iniciar instalação</button></footer>
                </form>
              </dialog>'''
