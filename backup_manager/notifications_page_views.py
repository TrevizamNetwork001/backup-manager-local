from __future__ import annotations

from .notifications import WEEKDAY_LABELS, notification_next_send, notification_page_context
from .presentation import escape_html as e


def notifications_page_content(*, context: dict[str, object], csrf: str, local_dt, _notification_badge) -> str:
    row = context["row"]
    diag = context["diag"]
    history = context["history"]
    queue_stats = context["queue_stats"]
    summary_settings = context["summary_settings"]
    summary_history = context["summary_history"]
    recent_failed = context["recent_failed"]
    last_success = context["last_success"]
    last_failure = context["last_failure"]
    last_sent = context["last_sent"]
    stats = {item["status"]: item["total"] for item in queue_stats}
    status_labels = {"configured": "Configurado", "queued": "Aguardando envio", "pending": "Aguardando envio",
                     "sent": "Enviado", "failed": "Falhou", "retry": "Aguardando nova tentativa", "retry_wait": "Aguardando nova tentativa",
                     "test_queued": "Aguardando envio", "not_verified": "Não verificado", "not_configured": "Não verificado", "verified": "Verificado"}
    label_status = lambda value: status_labels.get(value, value)
    token_status = "configurado" if row["token_encrypted"] else "não configurado"
    verified = bool(diag.get("telegram_diag_at"))
    integration_status = "verified" if verified and diag.get("telegram_diag_can_send") == "1" else "failed" if verified and diag.get("telegram_diag_can_send") == "0" else "not_verified"
    bot_name = diag.get("telegram_diag_bot") or "Identificado após o teste"
    destination_name = diag.get("telegram_diag_chat") or "Identificado após o teste"
    telegram_svg = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m21 3-7.4 18-4.2-7.1L3 10.5 21 3Z"/><path d="m9.4 13.9 4.1-3.8"/></svg>'
    bot_svg = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="7" width="16" height="12" rx="3"/><path d="M12 3v4M8 12h.01M16 12h.01M8 16h8"/></svg>'
    destination_svg = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/></svg>'
    test_svg = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="m9 12 2 2 4-5"/></svg>'
    success_svg = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 7 9 18l-5-5"/></svg>'
    failure_svg = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 17h.01"/></svg>'
    clock_svg = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>'
    route_svg = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 6h5a3 3 0 0 1 3 3v1M16 14v-4M13 13l3 3 3-3M6 8v8"/></svg>'
    timezone_name = row["timezone"] or "UTC"
    summary_defs = (
        ("daily", "Resumo diário", "Resumo das ocorrências do dia anterior.", "Período: dia anterior", bool(row["daily_summary_enabled"]), row["daily_summary_time"], None),
        ("weekly", "Resumo semanal", "Consolida os últimos sete dias completos.", "Período: sete dias completos anteriores", bool(row["weekly_summary_enabled"]), row["weekly_summary_time"], row["weekly_summary_day"]),
        ("executive", "Resumo executivo", "Enviado apenas quando houver alterações administrativas relevantes.", "Período: dia anterior · Somente quando houver atividade relevante", bool(summary_settings["executive_enabled"]), summary_settings["executive_time"], None),
    )
    summary_icons = {
        "daily": '<svg aria-hidden="true" viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16M8 14h3M8 17h6"/></svg>',
        "weekly": '<svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01"/></svg>',
        "executive": '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/><path d="m4 7 6-4 6 7 5-4"/></svg>',
    }
    def render_summary(item) -> str:
        kind, title, description, period, enabled, scheduled, weekday = item
        schedule = f"{WEEKDAY_LABELS[weekday] if weekday is not None else 'Todos os dias'} às {e(scheduled)}"
        next_send = notification_next_send(enabled, scheduled, timezone_name, weekday)
        return f'''<article class="notification-summary-row {'enabled' if enabled else 'disabled'}" data-summary="{kind}">
          <header><span class="notification-summary-icon">{summary_icons[kind]}</span><div><span class="notification-summary-state"><i></i>{'Ativado' if enabled else 'Desativado'}</span><h3>{title}</h3></div></header>
          <p>{description}</p><small class="notification-summary-period">{period}</small>
          <dl class="summary-schedule"><div><dt>Programação</dt><dd>{schedule}</dd></div><div><dt>Próximo envio</dt><dd>{e(next_send)}</dd></div></dl>
          <div class="actions"><button type="button" class="small secondary" data-summary-edit="{kind}">Editar</button><form method="post" action="/settings/notifications/summary-test" data-notification-test><input type="hidden" name="csrf_token" value="{csrf}"><input type="hidden" name="kind" value="{kind}"><button class="small" type="submit">Enviar teste</button></form></div></article>'''
    summary_rows = "".join(render_summary(item) for item in summary_defs)
    weekday_options = "".join(f'<option value="{number}"{" selected" if number == row["weekly_summary_day"] else ""}>{label}</option>' for number,label in enumerate(WEEKDAY_LABELS))
    event_labels = {"ftp_received_file": "Backup recebido por FTP", "backup_completed": "Backup SSH concluído",
                    "backup_late": "Backup atrasado", "backup_late_recovery": "Backup normalizado",
                    "telegram.basic_test": "Teste do Telegram"}
    error_labels = {"TELEGRAM_DELIVERY_FAILED": "Falha na entrega pelo Telegram"}
    def history_items(items, *, summary=False) -> str:
        rendered = []
        for index, item in enumerate(items):
            status = item["status"] if summary else item["attempt_status"]
            successful = status in {"sent", "success"}
            title = ({"daily": "Resumo diário", "weekly": "Resumo semanal", "executive": "Resumo executivo"}.get(item["summary_type"], item["summary_type"]) if summary else item["subject"])
            event = (item["destination_name"] if summary else event_labels.get(item["event_type"], item["subject"]))
            error = error_labels.get(item["error_code"], "Sem falhas registradas" if not item["error_code"] else "Falha no processamento")
            attempt = "" if summary else f" · Tentativa {item['attempt']}"
            date = item["sent_at"] or item["created_at"] if summary else item["attempt_at"]
            rendered.append(f'''<article data-history-item data-history-status="{'success' if successful else 'failed'}"{' hidden' if index >= 8 else ''}><span class="notification-history-event-icon {'success' if successful else 'failed'}">{success_svg if successful else failure_svg}</span><div><time>{e(local_dt(date))}</time><strong>{e(title)}</strong><small>{e(event)}{attempt} · {e(error)}</small></div>{_notification_badge(status)}</article>''')
        return "".join(rendered) or '<p class="muted">Nenhum registro encontrado.</p>'
    sent_total = sum(1 for item in history if item["attempt_status"] == "sent") + sum(1 for item in summary_history if item["status"] == "sent")
    failed_total = len(history) + len(summary_history) - sent_total
    permission_value = diag.get("telegram_diag_can_send")
    permission_label = "Pode enviar" if permission_value == "1" else "Não pode enviar" if permission_value == "0" else "Não verificada"
    permission_state = "success" if permission_value == "1" else "failed" if permission_value == "0" else "pending"
    return f"""
    <section class="notification-modern-page">
    <header class="notification-modern-hero"><span class="notification-modern-hero-icon"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg></span><div><span class="eyebrow">Alertas e comunicação</span><h1>Notificações</h1><p>Gerencie o Telegram, os alertas operacionais e os resumos automáticos em um só lugar.</p></div><span class="notification-modern-state {'active' if row['is_enabled'] else 'inactive'}"><i></i><small>Canal Telegram</small><strong>{'Ativo' if row['is_enabled'] else 'Desativado'}</strong></span></header>
    <section class="notification-status-strip" aria-label="Status das notificações"><span>Pendentes <strong>{stats.get('pending', 0)}</strong></span><span>Em nova tentativa <strong>{stats.get('retry_wait', 0)}</strong></span><span>Falhas nas últimas 24h <strong>{recent_failed}</strong></span><span>Último envio <strong>{e(local_dt(last_sent))}</strong></span></section>
    <section class="panel notification-integration">
      <div class="panel-title notification-integration-title"><span class="notification-integration-icon">{telegram_svg}</span><div><span class="eyebrow">Canal de mensagens</span><h2>Integração Telegram</h2><p class="muted">Bot, destino e atividade recente em uma única visão.</p></div><div class="notification-integration-state"><small>Estado da integração</small>{_notification_badge(integration_status)}</div></div>
      <div class="notification-integration-overview">
        <section class="notification-connection"><header><h3>Configuração atual</h3><p>Identificação utilizada para enviar os alertas.</p></header><dl><div><span>{bot_svg}</span><dt>Bot configurado</dt><dd>{e(bot_name)}</dd></div><div><span>{destination_svg}</span><dt>Destino principal</dt><dd>{e(destination_name)}</dd></div></dl></section>
        <section class="notification-activity"><header><h3>Atividade recente</h3><p>Últimas verificações e entregas registradas.</p></header><dl><div><span>{test_svg}</span><dt>Teste mais recente</dt><dd>{e(local_dt(row['last_test_at']))}</dd></div><div class="success"><span>{success_svg}</span><dt>Último envio bem-sucedido</dt><dd>{e(local_dt(last_success))}</dd></div><div class="failure"><span>{failure_svg}</span><dt>Última falha registrada</dt><dd>{e(local_dt(last_failure))}</dd></div></dl></section>
      </div>
      <div class="actions notification-integration-actions"><form method="post" action="/settings/notifications/test" data-notification-test><input type="hidden" name="csrf_token" value="{csrf}"><button type="submit">{test_svg} Testar envio</button></form><button type="button" class="secondary" data-toggle-details="telegram-config" aria-expanded="false">{telegram_svg} Editar configuração</button></div>
      <details id="telegram-config" class="notification-editor"><summary><span>{telegram_svg}</span><div><strong>Editar configuração do Telegram</strong><small>Ajuste o canal, a entrega e os horários dos resumos.</small></div></summary><form method="post" action="/settings/notifications" data-notification-settings><input type="hidden" name="csrf_token" value="{csrf}">
        <section class="notification-editor-section notification-channel-state"><header><span>{telegram_svg}</span><div><h3>Estado do canal</h3><p>Controle o envio de alertas e resumos pelo Telegram.</p></div></header><label class="notification-switch"><input type="checkbox" name="is_enabled" value="1" {'checked' if row['is_enabled'] else ''}><span><i></i></span><div><strong>Habilitar notificações</strong><small>Permite que o worker processe e envie novas mensagens.</small></div></label></section>
        <section class="notification-editor-section"><header><span>{bot_svg}</span><div><h3>Bot e destino</h3><p>Credenciais utilizadas pelo canal principal.</p></div></header><div class="notification-form-grid"><label><span>Token do bot <em>{token_status}</em></span><span class="notification-secret-input"><input name="token" type="password" autocomplete="new-password" placeholder="Deixe vazio para manter o token atual"><button type="button" data-secret-toggle aria-label="Mostrar token" title="Mostrar ou ocultar o token digitado"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></svg></button></span><small>Use o botão para conferir o novo token digitado. O token salvo não é recuperado.</small></label><label><span>Chat ID</span><input name="chat_id" value="{e(row['chat_id'])}" required aria-describedby="chat-id-help"><small id="chat-id-help">Identificador do grupo, canal ou conversa no Telegram.</small></label></div></section>
        <section class="notification-editor-section"><header><span>{test_svg}</span><div><h3>Regras de entrega</h3><p>Defina repetição e horário de referência das mensagens.</p></div></header><div class="notification-form-grid"><label><span>Intervalo entre alertas semelhantes</span><div class="notification-input-unit"><input name="cooldown_minutes" type="number" min="1" max="10080" value="{row['cooldown_minutes']}" required><b>minutos</b></div><small>Evita mensagens repetidas durante o intervalo configurado.</small></label><label><span>Fuso horário</span><input name="timezone" value="{e(row['timezone'])}" required><small>Usado nos horários, resumos e janela de manutenção.</small></label></div></section>
        <section class="notification-editor-section notification-summary-settings"><header><span>{summary_icons['daily']}</span><div><h3>Agenda dos resumos</h3><p>Ative e escolha quando cada consolidação será enviada.</p></div></header><div class="summary-editors"><fieldset data-summary-panel="daily"><legend><span>{summary_icons['daily']}</span>Resumo diário</legend><label class="notification-switch compact"><input type="checkbox" name="daily_enabled" value="1" {'checked' if row['daily_summary_enabled'] else ''}><span><i></i></span><strong>Ativado</strong></label><label>Horário<input name="daily_time" type="time" value="{e(row['daily_summary_time'])}" required></label></fieldset><fieldset data-summary-panel="weekly"><legend><span>{summary_icons['weekly']}</span>Resumo semanal</legend><label class="notification-switch compact"><input type="checkbox" name="weekly_enabled" value="1" {'checked' if row['weekly_summary_enabled'] else ''}><span><i></i></span><strong>Ativado</strong></label><label>Dia da semana<select name="weekly_day" required>{weekday_options}</select></label><label>Horário<input name="weekly_time" type="time" value="{e(row['weekly_summary_time'])}" required></label></fieldset><fieldset data-summary-panel="executive"><legend><span>{summary_icons['executive']}</span>Resumo executivo</legend><label class="notification-switch compact"><input type="checkbox" name="executive_enabled" value="1" {'checked' if summary_settings['executive_enabled'] else ''}><span><i></i></span><strong>Ativado</strong></label><label>Horário<input name="executive_time" type="time" value="{e(summary_settings['executive_time'])}" required></label></fieldset></div></section>
        <details class="advanced-settings notification-advanced"><summary><span>{route_svg}</span><div><strong>Configurações avançadas</strong><small>Tópicos, destinos, manutenção e diagnóstico.</small></div></summary><div class="notification-advanced-grid">
          <section class="notification-maintenance"><header><span>{clock_svg}</span><div><h4>Janela de manutenção</h4><p>Pause alertas não críticos durante um período programado.</p></div><em data-maintenance-state>{'Ativada' if row['maintenance_enabled'] else 'Desativada'}</em></header><div class="maintenance-toggle"><label class="notification-switch compact"><input type="checkbox" name="maintenance_enabled" value="1" {'checked' if row['maintenance_enabled'] else ''} data-maintenance-toggle><span><i></i></span><strong>Usar janela de manutenção</strong></label><div class="notification-form-grid" data-maintenance-times {' ' if row['maintenance_enabled'] else 'hidden'}><label>Início<input name="maintenance_start" type="time" value="{e(row['maintenance_start'])}" required></label><label>Fim<input name="maintenance_end" type="time" value="{e(row['maintenance_end'])}" required></label></div><small>Alertas críticos continuam sendo processados.</small></div></section>
          <section class="notification-routing"><header><span>{route_svg}</span><div><h4>Roteamento das mensagens</h4><p>Direcione mensagens para conversas e tópicos específicos.</p></div></header><div><a href="/telegram-backup?view=topics"><span>{telegram_svg}</span><div><strong>Configurar tópicos</strong><small>Organize alertas por assunto</small></div><b>›</b></a><a href="/telegram-backup?view=destinations"><span>{destination_svg}</span><div><strong>Destinos avançados</strong><small>Gerencie grupos, canais e chats</small></div><b>›</b></a></div></section>
          <section class="notification-diagnostic"><header><span>{test_svg}</span><div><h4>Diagnóstico do canal</h4><p>Resultado da última verificação registrada.</p></div></header><dl><div><dt>Permissão para enviar</dt><dd class="{permission_state}"><i></i>{permission_label}</dd></div><div><dt>Última verificação</dt><dd>{e(local_dt(diag.get('telegram_diag_at')))}</dd></div><div><dt>Resultado do último teste</dt><dd>{e(label_status(row['last_status']))}</dd></div></dl></section>
        </div></details>
        <footer class="notification-editor-actions"><button type="button" class="secondary" data-toggle-details="telegram-config">Cancelar</button><button type="submit">Salvar alterações</button></footer>
      </form></details></section>
    <section class="panel notification-summaries"><div class="panel-title"><div><h2>Resumos automáticos</h2><p class="muted">Frequência, período considerado e próximo envio.</p></div></div><div class="notification-summary-grid">{summary_rows}</div></section>
    <section class="panel notification-preview-card"><span class="notification-preview-icon">{test_svg}</span><div><h2>Prévia das mensagens</h2><p class="muted">Confira o formato de cada resumo com dados ilustrativos antes de realizar um teste.</p></div><div class="actions"><button type="button" class="secondary" data-dialog-open="notification-preview">Ver prévia</button></div></section>
    <dialog id="notification-preview" class="deployment-modal notification-preview">
      <button type="button" class="dialog-close secondary" aria-label="Fechar prévia" data-dialog-close>×</button>
      <header class="notification-preview-head"><span>{telegram_svg}</span><div><span class="eyebrow">Simulação visual</span><h2>Prévia das mensagens</h2><p>Veja como cada formato será apresentado no Telegram.</p></div></header>
      <div class="preview-tabs" role="tablist" aria-label="Tipos de mensagem"><button type="button" role="tab" aria-selected="true" data-preview-tab="daily">{summary_icons['daily']}<span>Diário</span></button><button type="button" role="tab" aria-selected="false" data-preview-tab="weekly">{summary_icons['weekly']}<span>Semanal</span></button><button type="button" role="tab" aria-selected="false" data-preview-tab="executive">{summary_icons['executive']}<span>Operacional</span></button></div>
      <aside class="notification-preview-notice">{test_svg}<span><strong>Conteúdo ilustrativo</strong><small>Os dados abaixo são fictícios e nenhuma mensagem será enviada.</small></span></aside>
      <section class="notification-preview-device"><header><span>{telegram_svg}</span><div><strong>Backup Manager Local</strong><small>bot · prévia</small></div><i></i><i></i><i></i></header><div class="notification-preview-chat">
        <article class="notification-preview-message" data-preview-panel="daily"><strong>📦 Backup Manager Local</strong><span>📅 Resumo diário — período anterior</span><hr><b>🟡 Sistema requer atenção</b><p>1 equipamento está sem backup no período.</p><dl><div><dt>✅ Backups concluídos</dt><dd>5</dd></div><div><dt>⚠️ Sem backup</dt><dd>1</dd></div></dl><h4>📡 Equipamentos</h4><p>Monitorados: <strong>5</strong><br>Com backup no período: <strong>4</strong></p><time>09:00 ✓✓</time></article>
        <article class="notification-preview-message" data-preview-panel="weekly" hidden><strong>📦 Backup Manager Local</strong><span>📅 Resumo semanal — sete dias completos</span><hr><b>✅ Operação estável</b><p>Consolidação semanal concluída.</p><dl><div><dt>✅ Backups concluídos</dt><dd>31</dd></div><div><dt>⚠️ Ocorrências</dt><dd>2</dd></div></dl><time>08:00 ✓✓</time></article>
        <article class="notification-preview-message" data-preview-panel="executive" hidden><strong>✅ Backup recebido por FTP</strong><span>Evento operacional</span><hr><p>🖥️ Equipamento: <strong>OLT Centro</strong><br>📄 Arquivo: <strong>backup-exemplo.cfg</strong><br>🕒 Horário: <strong>03:00</strong></p><time>03:00 ✓✓</time></article>
      </div></section>
    </dialog>
    <details class="panel notification-history"><summary><span><b>Histórico recente</b><small>Notificações e resumos processados</small></span><span class="notification-history-summary"><em>{sent_total} enviados</em><em class="failed">{failed_total} falhas</em></span></summary><div class="responsive-history">
      <nav class="notification-history-filters" aria-label="Filtrar histórico"><button type="button" class="active" data-history-filter="all">Todos</button><button type="button" data-history-filter="success">Enviados</button><button type="button" data-history-filter="failed">Com falha</button></nav>
      <section class="notification-history-group" data-history-group><header><div><h3>Notificações</h3><p>Alertas e eventos operacionais enviados ao Telegram.</p></div><span>{len(history)} registros recentes</span></header><div class="notification-history-list">{history_items(history)}</div><button type="button" class="notification-history-more" data-history-more>Mostrar mais notificações</button></section>
      <section class="notification-history-group" data-history-group><header><div><h3>Resumos</h3><p>Execuções dos resumos diário, semanal e executivo.</p></div><span>{len(summary_history)} registros recentes</span></header><div class="notification-history-list">{history_items(summary_history, summary=True)}</div><button type="button" class="notification-history-more" data-history-more>Mostrar mais resumos</button></section>
    </div></details>
    </section>
    """
