from __future__ import annotations

from .presentation import escape_html as e


def lifecycle_page_content(conn, *, policy, equipment, trashed, latest, latest_items, usage, local_dt, format_size, purge_confirmation: str, lifecycle_confirmation: str, empty_trash_confirmation: str) -> str:
    def icon(path: str) -> str:
        return f'<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{path}</svg>'

    icons = {
        "archive": '<path d="M21 8v13H3V8M1 3h22v5H1zM10 12h4"/>',
        "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v7c0 1.7 4 3 9 3s9-1.3 9-3V5M3 12v7c0 1.7 4 3 9 3s9-1.3 9-3v-7"/>',
        "trash": '<path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6"/>',
        "disk": '<circle cx="12" cy="12" r="9"/><path d="M8 12h8M12 8v8"/>',
        "settings": '<circle cx="12" cy="12" r="3"/><path d="M19 15a2 2 0 0 0 .4 2l-2.8 2.8a2 2 0 0 0-2-.4A2 2 0 0 0 13 21H9a2 2 0 0 0-1.6-1.6 2 2 0 0 0-2 .4L2.6 17A2 2 0 0 0 3 15.1 2 2 0 0 0 1 13V9a2 2 0 0 0 2-1.6 2 2 0 0 0-.4-2L5.4 2.6A2 2 0 0 0 7.3 3 2 2 0 0 0 9 1h4a2 2 0 0 0 1.6 2 2 2 0 0 0 2-.4l2.8 2.8a2 2 0 0 0-.4 2A2 2 0 0 0 21 9v4a2 2 0 0 0-2 2Z"/>',
        "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>',
        "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
        "layers": '<path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/>',
        "check": '<path d="M20 6 9 17l-5-5"/>',
        "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
        "play": '<path d="m8 5 11 7-11 7V5Z"/>',
        "warning": '<path d="M12 3 2 21h20L12 3Z"/><path d="M12 9v5M12 18h.01"/>',
    }
    equipment_rows = "".join(
        f'<tr><td><span class="lifecycle-equipment-cell"><i>{icon(icons["layers"])}</i><strong>{e(row["hostname"])}</strong></span></td><td><span class="lifecycle-value {"custom" if row["max_count"] is not None else "inherited"}">{e(row["max_count"] if row["max_count"] is not None else "Padrão global")}</span></td>'
        f'<td><span class="lifecycle-value {"custom" if row["max_age_days"] is not None else "inherited"}">{e(str(row["max_age_days"]) + " dias" if row["max_age_days"] is not None else "Padrão global")}</span></td><td>'
        f'<details class="lifecycle-row-editor"><summary>Personalizar</summary><form method="post" action="/lifecycle/policy"><input type="hidden" name="equipment_id" value="{row["id"]}">'
        f'<label>Quantidade<input name="max_count" type="number" min="0" placeholder="Global"></label><label>Idade em dias<input name="max_age_days" type="number" min="0" placeholder="Global"></label>'
        f'<button class="small" type="submit">Salvar exceção</button></form></details></td></tr>' for row in equipment
    )
    trash_rows = "".join(
        f"<tr><td>{e(row['hostname'])}</td><td>{e(row['original_filename'])}</td><td>{format_size(row['file_size'])}</td>"
        f"<td>{e(local_dt(row['trash_expires_at']))}</td><td><div class=\"actions lifecycle-trash-actions\"><form method=\"post\" action=\"/backups/{e(row['uuid'])}/restore\">"
        f'<button class="small secondary" type="submit">Restaurar</button></form><details><summary>Excluir</summary><form method="post" action="/backups/{e(row["uuid"])}/purge">'
        f'<label>Digite <strong>{purge_confirmation}</strong><input name="confirmation" autocomplete="off" required></label><button class="small danger" type="submit">Apagar definitivamente</button></form></details></div></td></tr>' for row in trashed
    ) or '<tr><td colspan="5" class="muted">Lixeira vazia.</td></tr>'
    mode_labels = {"automatic": "Execução automática", "execution": "Execução manual", "simulation": "Simulação", "health_check": "Verificação de integridade"}
    status_labels = {"completed": "Concluída", "failed": "Falhou", "running": "Em andamento"}
    last_mode = mode_labels.get(latest["mode"], latest["mode"]) if latest else "Nenhuma execução"
    last_status = status_labels.get(latest["status"], latest["status"]) if latest else "Aguardando"
    last_status_class = "success" if latest and latest["status"] == "completed" else "danger" if latest and latest["status"] == "failed" else "pending"
    last_at = local_dt(latest["created_at"]) if latest else "A rotina ainda não foi executada"
    last_candidates = latest["candidate_count"] if latest else 0
    last_candidate_bytes = format_size(latest["candidate_bytes"] if latest else 0)
    last_moved = latest["moved_count"] if latest else 0
    last_purged = latest["purged_count"] if latest else 0
    report_rows = "".join(
        f"<tr><td><span class=\"lifecycle-report-action\">{e(row['action'])}</span></td><td><strong>{e(row['original_filename'] or row['backup_uuid'] or row['ftp_received_file_id'])}</strong></td>"
        f"<td>{e(row['reason'])}</td><td>{format_size(row['file_size'])}</td><td><span class=\"lifecycle-result\">{e(row['result'])}</span></td></tr>" for row in latest_items
    ) or '<tr><td colspan="5" class="muted">Sem itens no último relatório.</td></tr>'
    metrics = "".join(f'<article class="stat lifecycle-stat"><span class="lifecycle-stat-icon">{icon(icons[key])}</span><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></article>' for label, value, detail, key in (
        ("Backups disponíveis", usage["available_count"], "Arquivos protegidos", "archive"),
        ("Espaço dos backups", format_size(usage["available_bytes"]), "Armazenamento utilizado", "database"),
        ("Na lixeira", format_size(usage["trashed_bytes"]), f'{len(trashed)} item(ns) restaurável(is)', "trash"),
        ("Espaço livre", format_size(usage["disk_free"]), "Disponível no servidor", "disk")))
    policy_status = "Ativa" if policy.is_enabled else "Inativa"
    policy_status_class = "active" if policy.is_enabled else "inactive"
    return f"""
    <section class="lifecycle-modern-page">
    <header class="lifecycle-hero"><span class="lifecycle-hero-icon">{icon(icons['shield'])}</span><div><span class="eyebrow">Armazenamento e segurança</span><h1>Retenção e limpeza</h1><p>Controle o ciclo de vida dos backups, recupere arquivos e acompanhe cada ação de limpeza.</p></div><span class="lifecycle-policy-state {policy_status_class}"><i></i><span>Política global</span><strong>{policy_status}</strong></span></header>
    <section class="stats lifecycle-stats">{metrics}</section>
    <section class="lifecycle-config-grid"><form method="post" action="/lifecycle/policy" class="panel lifecycle-policy"><div class="lifecycle-panel-title"><span>{icon(icons['settings'])}</span><div><span class="eyebrow">Configuração principal</span><h2>Política global</h2><p>Estas regras são aplicadas automaticamente a todos os equipamentos.</p></div></div><div class="lifecycle-form-grid">
      <label><span>Quantidade máxima</span><small>0 mantém todos os backups</small><div class="lifecycle-input-wrap">{icon(icons['layers'])}<input name="max_count" type="number" min="0" value="{policy.max_count}" required><b>arquivos</b></div></label>
      <label><span>Idade máxima</span><small>0 desativa o limite por idade</small><div class="lifecycle-input-wrap">{icon(icons['clock'])}<input name="max_age_days" type="number" min="0" value="{policy.max_age_days}" required><b>dias</b></div></label>
      <label><span>Retenção da lixeira</span><small>Prazo disponível para restauração</small><div class="lifecycle-input-wrap">{icon(icons['trash'])}<input name="trash_days" type="number" min="1" value="{policy.trash_retention_days}" required><b>dias</b></div></label>
      <label><span>Arquivos rejeitados</span><small>Prazo antes da remoção automática</small><div class="lifecycle-input-wrap">{icon(icons['warning'])}<input name="rejected_days" type="number" min="1" value="{policy.rejected_retention_days}" required><b>dias</b></div></label></div>
      <div class="lifecycle-form-footer"><span>{icon(icons['check'])} Exceções por equipamento continuam tendo prioridade.</span><button type="submit">{icon(icons['check'])} Salvar política</button></div></form>
      <article class="panel lifecycle-maintenance"><div class="lifecycle-panel-title"><span>{icon(icons['shield'])}</span><div><span class="eyebrow">Operação segura</span><h2>Manutenção</h2><p>Confira o impacto antes de movimentar arquivos.</p></div></div><div class="lifecycle-last-run"><header><div><span>Última execução</span><strong>{e(last_mode)}</strong><small>{e(last_at)}</small></div><em class="{last_status_class}"><i></i>{e(last_status)}</em></header><dl><div><dt>Candidatos</dt><dd>{last_candidates}</dd></div><div><dt>Espaço estimado</dt><dd>{last_candidate_bytes}</dd></div><div><dt>Movidos</dt><dd>{last_moved}</dd></div><div><dt>Expurgados</dt><dd>{last_purged}</dd></div></dl></div><div class="lifecycle-maintenance-actions">
      <form method="post" action="/lifecycle/simulate"><button class="secondary" type="submit">{icon(icons['search'])}<span><strong>Simular sem alterar arquivos</strong><small>Visualize o impacto da política atual</small></span></button></form>
      <form method="post" action="/lifecycle/health"><button class="secondary" type="submit">{icon(icons['check'])}<span><strong>Verificar integridade e hashes</strong><small>Confirme que os arquivos estão íntegros</small></span></button></form>
      <details class="lifecycle-danger-action"><summary>{icon(icons['play'])}<span><strong>Executar limpeza</strong><small>Mover candidatos para a lixeira</small></span></summary><form method="post" action="/lifecycle/execute"><label>Digite <strong>{lifecycle_confirmation}</strong> para confirmar<input name="confirmation" autocomplete="off" required></label><button class="danger" type="submit">Mover arquivos para a lixeira</button></form></details></div></article></section>
    <section class="panel table-panel lifecycle-table lifecycle-equipment-table"><div class="panel-heading"><span class="lifecycle-section-icon blue">{icon(icons['layers'])}</span><div><span class="eyebrow">Exceções</span><h2>Políticas por equipamento</h2><p>Personalize somente os equipamentos que não devem seguir a política global.</p></div><span class="lifecycle-table-count">{len(equipment)} equipamentos</span></div><div class="lifecycle-table-scroll"><table><thead><tr><th>Equipamento</th><th>Quantidade máxima</th><th>Idade máxima</th><th>Ação</th></tr></thead><tbody>{equipment_rows}</tbody></table></div></section>
    <section class="panel table-panel lifecycle-table"><div class="panel-heading"><span class="lifecycle-section-icon purple">{icon(icons['clock'])}</span><div><span class="eyebrow">Auditoria da rotina</span><h2>Relatório mais recente</h2><p>Ações calculadas na execução ou simulação mais recente.</p></div><span class="lifecycle-table-count">{len(latest_items)} itens</span></div><div class="lifecycle-table-scroll"><table><thead><tr><th>Ação</th><th>Arquivo</th><th>Motivo</th><th>Espaço estimado</th><th>Resultado</th></tr></thead><tbody>{report_rows}</tbody></table></div></section>
    <section class="panel table-panel lifecycle-table"><div class="panel-heading"><span class="lifecycle-section-icon orange">{icon(icons['trash'])}</span><div><span class="eyebrow">Recuperação</span><h2>Lixeira restaurável</h2><p>Restaure arquivos ou faça a exclusão definitiva individualmente.</p></div><span class="lifecycle-table-count">{len(trashed)} arquivos</span></div><div class="lifecycle-table-scroll"><table><thead><tr><th>Equipamento</th><th>Arquivo</th><th>Tamanho</th><th>Expira em</th><th>Ações</th></tr></thead><tbody>{trash_rows}</tbody></table></div></section>
    <section class="panel lifecycle-empty-trash"><div><span class="lifecycle-danger-icon">{icon(icons['trash'])}</span><div><h2>Esvaziar lixeira</h2><p>Os arquivos serão removidos definitivamente; o registro histórico será preservado.</p></div></div><details><summary>Esvaziar lixeira</summary><form method="post" action="/lifecycle/empty-trash"><label>Digite <strong>{empty_trash_confirmation}</strong> para confirmar<input name="confirmation" autocomplete="off" required></label><button class="danger" type="submit">Excluir backups definitivamente</button></form></details></section>
    </section>
    """
