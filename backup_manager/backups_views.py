from __future__ import annotations


def backups_page_context(conn, filters: dict[str, str]) -> dict[str, object]:
    page = max(1, int(filters.get("page") or 1))
    per_page = 25
    where = ["1 = 1"]
    params: list[object] = []
    joins = """
        FROM backups
        JOIN equipment ON equipment.id = backups.equipment_id
        LEFT JOIN environments ON environments.id = equipment.environment_id
        LEFT JOIN equipment_groups ON equipment_groups.id = equipment.group_id
        LEFT JOIN vendors ON vendors.id = equipment.vendor_id
        LEFT JOIN pops ON pops.id = equipment.pop_id
    """
    for field, column in (
        ("equipment_id", "equipment.id"),
        ("environment_id", "environments.id"),
        ("group_id", "equipment_groups.id"),
        ("vendor_id", "vendors.id"),
        ("pop_id", "pops.id"),
    ):
        if filters.get(field):
            where.append(f"{column} = ?")
            params.append(filters[field])
    source_methods = {"manual", "ssh", "ftp", "sftp", "tftp", "api", "system"}
    backup_statuses = {"available", "trashed", "quarantined", "failed", "deleted"}
    if filters.get("source_method") in source_methods:
        where.append("backups.source_method = ?")
        params.append(filters["source_method"])
    if filters.get("backup_status") in backup_statuses:
        where.append("backups.backup_status = ?")
        params.append(filters["backup_status"])
    if filters.get("name"):
        where.append("backups.original_filename LIKE ?")
        params.append(f"%{filters['name']}%")
    if filters.get("start"):
        where.append("backups.received_at >= ?")
        params.append(filters["start"])
    if filters.get("end"):
        where.append("backups.received_at <= ?")
        params.append(filters["end"] + " 23:59:59")
    where_sql = " WHERE " + " AND ".join(where)
    rows = conn.execute(
        f"""
        SELECT backups.*, equipment.hostname, equipment.ip_address,
               environments.name AS environment, equipment_groups.name AS group_name,
               vendors.name AS vendor, pops.name AS pop,
               (SELECT duration_ms FROM backup_job_runs
                WHERE created_backup_id = backups.id ORDER BY id DESC LIMIT 1) AS duration_ms
        {joins}
        {where_sql}
        ORDER BY backups.received_at DESC, backups.id DESC
        LIMIT ? OFFSET ?
        """,
        (*params, per_page, (page - 1) * per_page),
    ).fetchall()
    total = conn.execute(f"SELECT COUNT(*) {joins} {where_sql}", params).fetchone()[0]
    equipment = conn.execute("SELECT id, hostname AS name FROM equipment ORDER BY hostname").fetchall()
    environments = conn.execute("SELECT id, name FROM environments ORDER BY name").fetchall()
    groups = conn.execute("SELECT id, name FROM equipment_groups ORDER BY name").fetchall()
    vendors = conn.execute("SELECT id, name FROM vendors ORDER BY name").fetchall()
    pops = conn.execute("SELECT id, name FROM pops ORDER BY name").fetchall()
    summary = conn.execute(
        f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN backups.backup_status = 'available' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN backups.backup_status = 'failed' THEN 1 ELSE 0 END) AS failed,
                   SUM(CASE WHEN backups.source_method = 'manual' THEN 1 ELSE 0 END) AS manual
            {joins} {where_sql}""",
        params,
    ).fetchone()
    methods = conn.execute(
        f"""SELECT backups.source_method AS method, COUNT(*) AS total
            {joins} {where_sql}
            GROUP BY backups.source_method ORDER BY total DESC LIMIT 4""",
        params,
    ).fetchall()
    return {
        "page": page,
        "per_page": per_page,
        "rows": rows,
        "total": total,
        "equipment": equipment,
        "environments": environments,
        "groups": groups,
        "vendors": vendors,
        "pops": pops,
        "summary": summary,
        "methods": methods,
    }


def backups_page_content(
    *,
    user,
    filters: dict[str, str],
    ctx: dict[str, object],
    local_dt,
    option_rows,
    e,
    format_size,
    backup_action_buttons,
    report_icon,
) -> str:
    page = ctx["page"]
    per_page = ctx["per_page"]
    rows = ctx["rows"]
    total = ctx["total"]
    equipment = ctx["equipment"]
    environments = ctx["environments"]
    groups = ctx["groups"]
    vendors = ctx["vendors"]
    pops = ctx["pops"]
    summary = ctx["summary"]
    methods = ctx["methods"]
    select_status = "".join(f'<option value="{s}" {"selected" if filters.get("backup_status") == s else ""}>{s}</option>' for s in ("available", "trashed", "quarantined", "failed", "deleted"))
    select_method = "".join(f'<option value="{s}" {"selected" if filters.get("source_method") == s else ""}>{s}</option>' for s in ("manual", "ssh", "ftp", "sftp", "tftp", "api", "system"))
    method_labels = {"ssh": "Backup via SSH", "ftp": "Recebimento FTP", "manual": "Importação manual", "sftp": "SFTP", "tftp": "TFTP", "api": "API", "system": "Sistema"}
    status_labels = {"available": "Concluído", "failed": "Falhou", "trashed": "Na lixeira", "quarantined": "Quarentena", "deleted": "Excluído"}
    rows_html = "".join(
        f"""
        <tr>
          <td><strong>{e(local_dt(row['received_at']))}</strong></td>
          <td><div class="report-table-primary"><a class="inline-link" href="/equipment/{row['equipment_id']}"><strong>{e(row['hostname'])}</strong></a><span>{e(row['vendor'] or 'Fabricante não informado')}</span></div></td>
          <td><div class="report-method-cell method-{e(row['source_method'])}"><span class="report-method-icon">{report_icon('inbox' if row['source_method'] == 'ftp' else 'manual_upload' if row['source_method'] == 'manual' else 'archive')}</span><div><strong>{e(method_labels.get(row['source_method'], row['source_method']))}</strong><span>{e('Manual' if row['source_method'] == 'manual' else row['hostname'])}</span></div></div></td>
          <td><span class="badge status-{e('success' if row['backup_status'] == 'available' else row['backup_status'])}">{e(status_labels.get(row['backup_status'], row['backup_status']))}</span></td>
          <td>{'-' if row['duration_ms'] is None else f'{int(row["duration_ms"]) / 1000:.1f}s'}</td>
          <td><strong>{format_size(row['file_size'])}</strong></td>
          <td><a class="backup-details-button" href="/backups/{e(row['uuid'])}">Detalhes</a></td>
        </tr>
        """
        for row in rows
    )
    if not rows_html:
        rows_html = '<tr><td colspan="7" class="report-table-empty">Nenhum backup encontrado.</td></tr>'
    base_filters = {k: v for k, v in filters.items() if k != "page" and v}
    prev_url = "/backups?" + __import__("urllib.parse").parse.urlencode({**base_filters, "page": page - 1}) if page > 1 else ""
    next_url = "/backups?" + __import__("urllib.parse").parse.urlencode({**base_filters, "page": page + 1}) if page * per_page < total else ""
    pager = f"""
      <div class="pager">
        {f'<a class="button secondary" href="{prev_url}">Anterior</a>' if prev_url else ''}
        <span class="muted">Pagina {page} de {max(1, ((total - 1) // per_page) + 1)}</span>
        {f'<a class="button secondary" href="{next_url}">Próxima</a>' if next_url else ''}
      </div>
    """
    total_count = int(summary["total"] or 0)
    completed = int(summary["completed"] or 0)
    failed = int(summary["failed"] or 0)
    manual = int(summary["manual"] or 0)
    pct = lambda value: (100 * value / total_count) if total_count else 0
    cards = (("backup_stack", "Total de backups", total_count, "no período selecionado"), ("check_circle", "Concluídos", completed, f"{pct(completed):.1f}% do total"), ("alert_circle", "Falhas", failed, f"{pct(failed):.1f}% do total"), ("upload_tray", "Importações manuais", manual, f"{pct(manual):.1f}% do total"))
    cards_html = "".join(f'<article class="stat report-stat"><span class="report-stat-icon">{report_icon(icon)}</span><div><span>{label}</span><strong>{value}</strong><small>{note}</small></div></article>' for icon, label, value, note in cards)
    method_totals = {row["method"]: int(row["total"]) for row in methods}
    method_items = "".join(
        f'<li class="method-{method}"><i></i><span>{method_labels[method]}</span><strong><b>{method_totals.get(method, 0)}</b><small>{pct(method_totals.get(method, 0)):.1f}%</small></strong></li>'
        for method in ("ssh", "manual", "ftp")
    )
    return f"""
    <section class="backup-reference-page">
    <header class="backup-reference-head"><div><span class="eyebrow">Análise operacional</span><h1>Backups</h1><p>Consulte as execuções e os arquivos registrados no período selecionado.</p></div><a class="backup-head-action" href="/equipment">Ver equipamentos <b>→</b></a></header>
    <form method="get" action="/backups" class="panel report-filters backup-report-filters"><div class="report-filter-head"><div><span class="report-filter-icon">{report_icon('filter')}</span><span><strong>Filtrar relatório</strong><small>Refine os registros exibidos.</small></span></div><a href="/backups">Limpar filtros</a></div><div class="report-filter-main"><label>Data inicial<input type="date" name="start" value="{e(filters.get('start', ''))}"></label><label>Data final<input type="date" name="end" value="{e(filters.get('end', ''))}"></label><label>Equipamento<select name="equipment_id"><option value="">Todos os equipamentos</option>{option_rows(equipment, filters.get('equipment_id', ''))}</select></label><label>Status<select name="backup_status"><option value="">Todos os status</option>{select_status}</select></label><label>Método<select name="source_method"><option value="">Todos</option>{select_method}</select></label><button type="submit">{report_icon('filter')}Aplicar filtros</button></div><details class="report-more-filters"><summary>Mais filtros</summary><div class="report-filter-extra"><label>Ambiente<select name="environment_id"><option value="">Todos</option>{option_rows(environments, filters.get('environment_id', ''))}</select></label><label>Grupo<select name="group_id"><option value="">Todos</option>{option_rows(groups, filters.get('group_id', ''))}</select></label><label>Fabricante<select name="vendor_id"><option value="">Todos</option>{option_rows(vendors, filters.get('vendor_id', ''))}</select></label><label>POP<select name="pop_id"><option value="">Todos</option>{option_rows(pops, filters.get('pop_id', ''))}</select></label><label>Nome original<input name="name" value="{e(filters.get('name', ''))}"></label></div></details></form>
    <section class="stats backup-report-stats">{cards_html}</section>
    <section class="backup-report-layout"><section class="panel table-panel report-table-panel backup-results-panel"><div class="panel-heading"><div><h2><span>{report_icon('database')}</span>Backups encontrados</h2></div></div><table class="report-modern-table"><thead><tr><th>Data e hora</th><th>Equipamento</th><th>Método e origem</th><th>Status</th><th>Duração</th><th>Tamanho</th><th>Ações</th></tr></thead><tbody>{rows_html}</tbody></table>{pager}</section><aside class="backup-report-side"><article class="panel backup-methods-card"><h2>Métodos mais usados</h2><ul class="report-method-list">{method_items}</ul></article><article class="panel backup-export-card"><h2>Últimas exportações</h2><div class="backup-export-empty"><span>{report_icon('archive')}</span><div><strong>Relatório de backups</strong><small>Exporte os dados do período selecionado</small></div><a href="/reports/exports" aria-label="Abrir exportações">{report_icon('download')}</a></div><a class="report-panel-link" href="/reports/exports">Ver todas as exportações <b>→</b></a></article></aside></section>
    </section>
    """
