from __future__ import annotations

from .presentation import escape_html as e, render_template


def dashboard_page_content(
    *,
    snapshot,
    user,
    install_name: str,
    provider: str,
    local_dt,
    relative_time,
    format_size,
    quantity,
    short_identifier,
    can_operate,
    on_health_check=None,
) -> str:
    level_labels = {"green": "Normal", "yellow": "Atenção", "red": "Crítico"}
    technical_levels = {"green": "VERDE", "yellow": "AMARELO", "red": "VERMELHO"}
    counts, storage = snapshot["counts"], snapshot["storage"]
    service_names = {
        "backup-manager-local": "Painel administrativo",
        "worker": "Processamento de backups",
        "scheduler": "Agendamentos automáticos",
        "ftp-importer": "Recebimento por FTP",
        "pure-ftpd": "Serviço de transferência FTP",
        "cloud-worker": "Sincronização externa",
        "update-worker": "Atualizações do sistema",
    }
    term_names = {
        "Worker": "Processamento",
        "Scheduler": "Agendador",
        "Storage": "Armazenamento",
        "Jobs": "Agendamentos",
        "Cloud sync worker": "Sincronização externa",
    }
    primary_cards = (
        ("Backups SSH", counts["ssh"]),
        ("Backups FTP", counts["ftp"]),
        ("Rejeitados", counts["rejected"]),
        ("Espaço livre", format_size(storage["free"])),
        ("Agendamentos ativos", counts["jobs_active"]),
    )
    secondary_cards = (
        ("Lixeira", counts["trash"]),
        ("Backups armazenados", format_size(storage["backup_bytes"])),
        ("Eventos de auditoria", counts["audit"]),
    )
    stats = "".join(
        f'<article class="stat noc-stat"{(" data-technical-label=\"Jobs ativos\"" if label == "Agendamentos ativos" else "")}>'
        f'<span>{e(label)}</span><strong>{e(value)}</strong></article>'
        for label, value in primary_cards
    )
    secondary_stats = "".join(
        f'<article class="secondary-stat"><span>{e(label)}</span><strong>{e(value)}</strong></article>'
        for label, value in secondary_cards
    )
    cloud_indicator = next((item for item in snapshot["indicators"] if item["label"] == "Backup externo"), None)
    if cloud_indicator:
        secondary_stats += (
            f'<article class="secondary-stat status-{cloud_indicator["level"]}"><span>'
            f'<i class="noc-dot"></i> Backup externo</span><strong>{e(cloud_indicator["value"])}</strong></article>'
        )
    alert_rows = []
    for item in snapshot["alerts"]:
        alert_actions = ""
        if item["source"] == "Integridade" and not snapshot["health"]["last_check"] and user["can_admin"]:
            alert_actions = (
                '<form method="post" action="/lifecycle/health">'
                '<button class="button small secondary" type="submit">Executar verificação</button></form>'
            )
        elif item.get("equipment_id"):
            alert_actions = f'<a class="row-action" href="/equipment/{item["equipment_id"]}">Ver equipamento</a>'
        elif item["source"] in {"Jobs", "SSH"}:
            alert_actions = '<a class="row-action" href="/jobs">Ver jobs</a>'
        display_source = term_names.get(item["source"], item["source"])
        display_message = str(item["message"]).replace("worker", "processamento").replace("jobs", "agendamentos").replace("job", "agendamento")
        alert_rows.append(
            f'<li class="noc-alert status-{item["level"]}"><span class="noc-dot"></span><div>'
            f'<strong>{e(display_source)}</strong><p>{e(display_message)}</p>{alert_actions}</div></li>'
        )
    alert_html = "".join(alert_rows) or '<li class="empty-state"><strong>Nenhum alerta ativo</strong><p>A operação está normal no momento.</p></li>'

    def service_state(item: dict) -> str:
        if item["level"] == "red":
            return "Com falha"
        if item["summary"] == "não habilitado":
            return "Não configurado"
        if item["key"] == "worker" and item["active"] == "inactive" and item["level"] == "green":
            return "Ocioso"
        if item["key"] == "worker" and item["active"] in {"activating", "deactivating"} and item["level"] == "green":
            return "Em execução"
        if item["active"] == "active":
            return "Ativo"
        if item["level"] == "yellow":
            return "Aguardando dados"
        return "Inativo"

    service_html = "".join(
        f'<article class="service-card" title="Nome técnico: {e(item["label"])}"{(" data-legacy-label=\"Aplicacao\"" if item["key"] == "backup-manager-local" else "")}>'
        f'<span class="noc-dot status-{item["level"]}"></span><div><strong>{e(service_names.get(item["key"], item["label"]))}</strong>'
        f'<small>{e(item["summary"])}</small></div><span class="badge status-{item["level"]}">{e(service_state(item))}</span></article>'
        for item in snapshot["services"]
    )
    equipment_html = "".join(
        f'<tr><td><span class="noc-dot status-{item["level"]}"></span></td>'
        f'<td><a class="inline-link" href="/equipment/{item["id"]}">{e(item["name"])}</a><small class="table-sub">{e(item["hostname"])}</small></td>'
        f'<td>{e(item["environment"] or "-")}</td><td>{e(local_dt(item["last_backup"]))}</td><td>{e(item["method"])}</td>'
        f'<td>{e("-" if item["age_hours"] is None else f"{item["age_hours"]:.1f}h")}</td>'
        f'<td><span class="badge status-{item["level"]}">{e(item["status"])}</span></td>'
        f'<td><a class="row-action" href="/equipment/{item["id"]}">Ver equipamento</a></td></tr>'
        for item in snapshot["equipment"]
    ) or '<tr><td colspan="8" class="muted">Nenhum equipamento ativo.</td></tr>'
    timeline_html = "".join(
        f'<li class="timeline-item"><span class="noc-dot status-{item["level"]}"></span><div><strong>{e(item["label"])}</strong>'
        f'<p>{e(item["entity"])} · <span title="{e(item["entity_id"] or "-")}">{e(short_identifier(item["entity_id"]))}</span></p><time>{e(local_dt(item["created_at"]))}</time></div></li>'
        for item in snapshot["timeline"][:6]
    ) or '<li class="muted">Nenhum evento operacional registrado.</li>'
    coverage, success_rate = snapshot["coverage"], snapshot["success_rate"]
    state_reasons = [] if snapshot["level"] == "green" else [quantity(len(snapshot["alerts"]), "alerta ativo", "alertas ativos")]
    if snapshot["level"] != "green" and coverage["unprotected"]:
        state_reasons.append(quantity(coverage["unprotected"], "equipamento sem backup válido", "equipamentos sem backup válido"))
    if snapshot["level"] != "green" and snapshot["jobs"]["active_failed"]:
        state_reasons.append(quantity(snapshot["jobs"]["active_failed"], "falha ativa", "falhas ativas"))
    if snapshot["level"] == "green":
        state_reasons = ["Nenhuma ação necessária"]
    state_reasons = state_reasons[:3]
    global_reason_html = "".join(f"<span>{e(reason)}</span>" for reason in state_reasons)
    timeline_link = '<a class="panel-link" href="/audit">Ver todos os eventos</a>' if user["can_admin"] else ""
    quick_actions = '<a class="button secondary" href="/jobs">Ver falhas</a>'
    if can_operate(user):
        quick_actions = (
            '<a class="button" href="/equipment">Novo equipamento</a>'
            '<a class="button secondary" href="/jobs">Ver falhas</a>'
        )
    if user["can_admin"]:
        quick_actions += '<form method="post" action="/lifecycle/health"><button class="secondary" type="submit">Executar verificação de integridade</button></form>'
    last = snapshot["last_backup"]
    last_html = (
        f'<a class="operational-card-link" href="/equipment/{last["equipment_id"]}"><strong>{e(last["equipment_name"] or last["hostname"])}</strong>'
        f'<span>{e(local_dt(last["received_at"]))} · {e(relative_time(last["received_at"]))}</span>'
        f'<small>{e((last["source_method"] or "-").upper())} · {e(last["environment"] or "Sem ambiente")}</small></a>'
        if last else '<div class="empty-state"><strong>Nenhum backup concluído</strong><p>As últimas execuções aparecerão aqui.</p></div>'
    )
    upcoming = snapshot["next_backup"]
    schedule_labels = {"daily": "Agendamento diário", "weekly": "Agendamento semanal", "monthly": "Agendamento mensal", "interval": "Agendamento periódico"}
    next_is_overdue = bool(upcoming and upcoming["next_run_at"] < snapshot["generated_at"])
    next_html = (
        f'<a class="operational-card-link" href="/equipment/{upcoming["equipment_id"]}" data-technical-schedule="Job diário"><strong>{e(upcoming["equipment_name"] or upcoming["hostname"])}</strong>'
        f'<span>{e(local_dt(upcoming["next_run_at"]))}{" · Atrasado" if next_is_overdue else ""}</span><small>{e(schedule_labels.get(upcoming["schedule_type"], "Backup agendado"))} · {e(upcoming["environment"] or "Sem ambiente")}</small></a>'
        if upcoming else '<div class="empty-state"><strong>Nenhum backup agendado</strong><p>Crie um agendamento para programar a próxima execução.</p></div>'
    )
    reason_html = "".join(f'<span>{e(quantity(count, reason))}</span>' for reason, count in coverage["reasons"].items())
    coverage_html = (
        '<div class="empty-state"><strong>Nenhum equipamento cadastrado</strong><p>A cobertura será calculada após o cadastro.</p></div>'
        if not coverage["total"]
        else f'<strong class="operational-value">{coverage["percent"]}%</strong><span>{coverage["protected"]} de {coverage["total"]} equipamentos protegidos</span><small>{reason_html or "Todos em dia"}</small>'
    )
    rate_html = (
        '<div class="empty-state"><strong>Sem execuções nos últimos 7 dias</strong><p>A taxa considera somente jobs concluídos, com falha ou expirados.</p></div>'
        if success_rate["percent"] is None
        else f'<strong class="operational-value">{str(success_rate["percent"]).replace(".", ",")}%</strong><span>{quantity(success_rate["success"], "concluído", "concluídos")} · {quantity(success_rate["failed"], "falha", "falhas")}</span><small>Últimos 7 dias · {success_rate["total"]} execuções</small>'
    )
    environment_html = "".join(
        f'<article class="environment-summary"><strong>{e(item["name"])}</strong><span>{quantity(item["equipment"], "equipamento", "equipamentos")}</span><span>{item["percent"]}% em dia</span><small>{quantity(item["failures_24h"], "falha ativa", "falhas ativas")}</small></article>'
        for item in snapshot["environments"]
    )
    environment_html = environment_html or '<div class="empty-state"><strong>Nenhum ambiente com equipamento</strong><p>Os ambientes em operação aparecerão aqui.</p></div>'
    methods_html = "".join(
        f'<span><strong>{e(method)}</strong> {quantity(count, "equipamento", "equipamentos")}</span>'
        for method, count in sorted(snapshot["method_distribution"].items())
    ) or '<span>Nenhum método em uso</span>'
    def dash_icon(path: str) -> str:
        return (f'<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{path}</svg>')

    dashboard_kpis = "".join((
        f'''<article><span class="dashboard-kpi-icon">{dash_icon('<path d="M3.5 6c0-2 3.8-3.5 8.5-3.5S20.5 4 20.5 6v12c0 2-3.8 3.5-8.5 3.5S3.5 20 3.5 18V6Z" fill="currentColor" stroke="none"/><path d="M4 6c0 2 3.6 3.4 8 3.4S20 8 20 6M4 12c0 2 3.6 3.4 8 3.4s8-1.4 8-3.4" stroke="#e5f6ed" stroke-width="1.35"/>')}</span><div><small>Backups hoje</small><strong>{counts['backups_today']}</strong><p>sucessos: <b>{counts['backups_today']}</b> <span>falhas ativas: {snapshot['jobs']['active_failed']}</span></p></div></article>''',
        f'''<article><span class="dashboard-kpi-icon">{dash_icon('<path d="M12 2.2 20 5v6.2c0 5.1-3.2 8.8-8 11-4.8-2.2-8-5.9-8-11V5l8-2.8Z" fill="currentColor" stroke="none"/><path d="m8.3 11.8 2.3 2.3 5.1-5.1" stroke="white" stroke-width="2.1"/>')}</span><div><small>Taxa de sucesso</small><strong>{str(success_rate['percent']).replace('.', ',') + '%' if success_rate['percent'] is not None else '-'}</strong><p>últimos 7 dias</p></div></article>''',
        f'''<article><span class="dashboard-kpi-icon">{dash_icon('<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>')}</span><div><small>Equipamentos protegidos</small><strong>{coverage['protected']} <em>/ {coverage['total']}</em></strong><p>em dia: <b>{coverage['protected']}</b> <span>atenção: {coverage['unprotected']}</span></p></div></article>''',
        f'''<article class="dashboard-storage-kpi"><span class="dashboard-kpi-icon">{dash_icon('<path d="m12 2.7 9.2 4.1L12 10.9 2.8 6.8 12 2.7Z" fill="currentColor" stroke="none"/><path d="m2.8 10.1 2.1-.9 7.1 3.2 7.1-3.2 2.1.9v2.4L12 16.6l-9.2-4.1v-2.4Z" fill="currentColor" stroke="none"/><path d="m2.8 15.8 2.1-.9 7.1 3.2 7.1-3.2 2.1.9v2.4L12 22.3l-9.2-4.1v-2.4Z" fill="currentColor" stroke="none"/>')}</span><div><small>Armazenamento usado</small><strong>{e(format_size(storage['used']))} <em>/ {e(format_size(storage['total']))}</em></strong><i><b style="width:{min(100, storage['percent'])}%"></b></i><p>{storage['percent']}% usado</p></div></article>''',
        f'''<article class="dashboard-alert-kpi status-{snapshot['level']}"><span class="dashboard-kpi-icon">{dash_icon('<path d="M10.3 3.6 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0Z" fill="currentColor" stroke="none"/><path d="M12 9v4M12 17h.01" stroke="white" stroke-width="2"/>')}</span><div><small>Alertas ativos</small><strong>{len(snapshot['alerts'])}</strong><a href="#alertas-atuais">ver detalhes</a></div></article>''',
    ))
    dashboard_services = "".join(
        f'''<span class="status-{item['level']}"><i>{'✓' if item['level'] == 'green' else '!'}</i><strong>{e(term_names.get(item['label'], item['label']).replace('Worker', 'Processamento'))}</strong><small>{'OK' if item['level'] == 'green' else 'Em atenção' if item['level'] == 'yellow' else 'Falha'}</small></span>'''
        for item in snapshot["indicators"] if item["label"] in {"SSH", "FTP", "Worker", "Scheduler", "Disco", "Telegram notificações"}
    )
    attention_lines = "".join(
        f'<li><i>◆</i><span><strong>{e(term_names.get(item["source"], item["source"]))}</strong> — {e(str(item["message"]).replace("worker", "processamento"))}</span></li>'
        for item in snapshot["alerts"][:3]
    ) or '<li class="empty"><span>Nenhuma ação necessária.</span></li>'
    activity_lines = "".join(
        f'<li><i class="status-{item["level"]}"></i><strong>{e(item["label"])}</strong><time>{e(local_dt(item["created_at"]))}</time></li>'
        for item in snapshot["timeline"][:5]
    ) or '<li class="empty"><span>Nenhuma atividade recente.</span></li>'
    schedule_method_labels = {"ssh": "Backup SSH", "ftp": "Backup FTP", "dry_run": "Verificação"}
    upcoming_lines = "".join(
        f'<li>{dash_icon("<rect x=\"3\" y=\"5\" width=\"18\" height=\"16\" rx=\"2\"/><path d=\"M16 3v4M8 3v4M3 10h18\"/>")}<a href="/equipment/{item["equipment_id"]}">{e(item["equipment_name"] or item["hostname"])}</a><time>{e(local_dt(item["next_run_at"]))}</time><span>{e(schedule_method_labels.get(item["method"], str(item["method"]).upper()))}</span></li>'
        for item in snapshot.get("upcoming_backups", [])
    ) or '<li class="empty"><span>Nenhuma execução agendada.</span></li>'
    running_lines = "".join(
        f'<li><i class="status-{"green" if item["status"] == "running" else "yellow"}"></i><a href="/equipment/{item["equipment_id"]}">{e(item["equipment_name"] or item["hostname"])}</a><span>{"Em execução" if item["status"] == "running" else "Na fila"}</span></li>'
        for item in snapshot.get("running_operations", [])
    ) or (
        '<p class="dashboard-empty-running"><i>'
        f'{dash_icon("<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"m8 12 2.6 2.6L16 9\"/>")}'
        '</i><span>Nenhuma operação em andamento</span></p>'
    )
    critical_equipment = "".join(
        f'<tr><td><i class="status-{item["level"]}">{dash_icon("<circle cx=\"12\" cy=\"12\" r=\"7\"/>")}</i></td><td><a href="/equipment/{item["id"]}">{e(item["name"])}</a></td><td>{e(item["environment"] or "-")}</td><td>{e(local_dt(item["last_backup"]))}</td><td>{e(item["method"])}</td><td><span class="badge status-{item["level"]}">{e(item["status"])}</span></td></tr>'
        for item in snapshot["equipment"][:5]
    ) or '<tr><td colspan="6">Nenhum equipamento ativo.</td></tr>'
    dashboard_environments = "".join(
        f'<article><i class="status-{"green" if item["percent"] == 100 else "yellow"}">✓</i><div><strong>{e(item["name"])}</strong><span>{quantity(item["equipment"], "equipamento", "equipamentos")} · {item["percent"]}% em dia · {quantity(item["failures_24h"], "falha ativa", "falhas ativas")}</span></div></article>'
        for item in snapshot["environments"]
    ) or '<p>Nenhum ambiente com equipamento.</p>'
    security = snapshot["security"]
    security_html = (
        f'<article><i>{dash_icon("<path d=\"M10.3 3.6 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0Z\"/><path d=\"M12 9v4M12 17h.01\"/>")}</i><div><strong>{security["failed_24h"]}</strong><span>tentativas em 24h</span></div></article>'
        f'<article><i>{dash_icon("<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M3 12h18M12 3c3 3 4 6 4 9s-1 6-4 9c-3-3-4-6-4-9s1-6 4-9Z\"/>")}</i><div><strong>{security["ips_24h"]}</strong><span>IPs distintos</span></div></article>'
        f'<article><i>{dash_icon("<rect x=\"5\" y=\"10\" width=\"14\" height=\"11\" rx=\"2\"/><path d=\"M8 10V7a4 4 0 0 1 8 0v3M12 14v3\"/>")}</i><div><strong>{security["throttled_24h"]}</strong><span>acessos bloqueados</span></div></article>'
        f'<article><i>{dash_icon("<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"m8 12 2.6 2.6L16 9\"/>")}</i><div><strong>{security["successful_24h"]}</strong><span>logins válidos</span></div></article>'
    )
    content = render_template(
        "dashboard.html",
        install_name=e(install_name),
        provider=e(provider),
        generated_at=e(local_dt(snapshot["generated_at"])),
        level=snapshot["level"],
        technical_level=technical_levels[snapshot["level"]],
        level_label=level_labels[snapshot["level"]],
        global_reason_html=global_reason_html,
        quick_actions=quick_actions,
        indicators="".join(
            f'<article class="health-indicator status-{item["level"]}"><span class="noc-dot"></span><div><strong>{e(term_names.get(item["label"], item["label"]))}</strong><small>{e(str(item["value"]).replace("jobs", "agendamentos").replace("job", "agendamento"))}</small></div></article>'
            for item in snapshot["indicators"] if item["label"] != "Backup externo"
        ),
        stats=stats,
        secondary_stats=secondary_stats,
        last_html=last_html,
        next_html=next_html,
        coverage_level=coverage["level"],
        unprotected=coverage["unprotected"],
        unprotected_summary=reason_html or ("Todos em dia" if coverage["total"] else "Nenhum equipamento cadastrado"),
        coverage_html=coverage_html,
        rate_html=rate_html,
        alert_count=quantity(len(snapshot["alerts"]), "alerta", "alertas"),
        alert_html=alert_html,
        service_html=service_html,
        equipment_count=quantity(len(snapshot["equipment"]), "ativo", "ativos"),
        equipment_html=equipment_html,
        storage_percent=storage["percent"],
        storage_meter_percent=min(100, storage["percent"]),
        storage_total=format_size(storage["total"]),
        storage_used=format_size(storage["used"]),
        storage_free=format_size(storage["free"]),
        storage_backup_bytes=format_size(storage["backup_bytes"]),
        jobs_queued=quantity(counts["jobs_queued"], "agendamento", "agendamentos"),
        failed_24h=quantity(snapshot["jobs"]["failed_24h"], "falha", "falhas"),
        health_problems=quantity(snapshot["health"]["problems"], "problema", "problemas"),
        environment_html=environment_html,
        methods_html=methods_html,
        timeline_html=timeline_html,
        timeline_link=timeline_link,
        dashboard_kpis=dashboard_kpis,
        dashboard_services=dashboard_services,
        attention_lines=attention_lines,
        activity_lines=activity_lines,
        upcoming_lines=upcoming_lines,
        running_lines=running_lines,
        critical_equipment=critical_equipment,
        dashboard_environments=dashboard_environments,
        security_level=security["level"],
        security_label=security["label"],
        security_html=security_html,
    )
    content = content.replace("Armazenamento e jobs", "Armazenamento e agendamentos").replace("Jobs na fila", "Agendamentos na fila")
    return content
