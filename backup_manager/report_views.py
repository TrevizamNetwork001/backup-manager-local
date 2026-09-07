from __future__ import annotations

from html import escape
from urllib.parse import parse_qs, urlencode

from .reports import parse_filters as parse_report_filters
from .reports import filter_values as report_filter_values


def e(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def hidden(name: str, value: object) -> str:
    return f'<input type="hidden" name="{e(name)}" value="{e(value)}">'


def option_rows(rows, selected: str) -> str:
    return "".join(
        f'<option value="{e(row["id"])}" {"selected" if str(row["id"]) == selected else ""}>{e(row["name"])}</option>'
        for row in rows
    )


REPORT_SECTIONS = (
    ("/reports", "Visão geral", "grid"),
    ("/reports/backups", "Backups", "archive"),
    ("/reports/equipment", "Equipamentos", "server"),
    ("/reports/storage", "Armazenamento", "database"),
    ("/reports/ftp", "FTP", "upload"),
    ("/reports/telegram", "Telegram", "send"),
    ("/reports/audit", "Auditoria", "shield"),
    ("/reports/exports", "Exportações", "download"),
)


def report_icon(name: str) -> str:
    if name == "rclone":
        return '''<svg class="rclone-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
          <path d="M17.5 19H8.7a6.7 6.7 0 1 1 6.5-8.3 4.6 4.6 0 1 1 2.3 8.3Z"/>
        </svg>'''
    paths = {
        "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
        "archive": '<path d="M21 8v13H3V8M1 3h22v5H1zM10 12h4"/>',
        "server": '<rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 7h.01M7 17h.01"/>',
        "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v7c0 1.7 4 3 9 3s9-1.3 9-3V5M3 12v7c0 1.7 4 3 9 3s9-1.3 9-3v-7"/>',
        "upload": '<path d="M12 16V4M7 9l5-5 5 5M5 20h14"/>',
        "send": '<path d="M22 2 11 13m11-11-7 20-4-9-9-4Z"/>',
        "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>',
        "alert": '<path d="M10.3 3.6 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
        "reject": '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
        "activity": '<path d="M3 3v18h18"/><path d="m6 15 4-4 3 3 6-7"/>',
        "bolt": '<path d="m13 2-8 12h7l-1 8 8-12h-7l1-8Z"/>',
        "bell": '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
        "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
        "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
        "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/>',
        "pie": '<path d="M12 3v9h9A9 9 0 1 1 12 3Z"/><path d="M16 3.5A8 8 0 0 1 20.5 8H16Z"/>',
        "download": '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
        "filter": '<path d="M4 5h16M7 12h10M10 19h4"/>',
        "backup_stack": '<rect x="4" y="4" width="16" height="7" rx="2"/><rect x="4" y="13" width="16" height="7" rx="2"/><path d="M8 7.5h.01M8 16.5h.01M12 7.5h5M12 16.5h5"/>',
        "check_circle": '<circle cx="12" cy="12" r="9"/><path d="m8 12 2.6 2.6L16.5 9"/>',
        "alert_circle": '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 17h.01"/>',
        "upload_tray": '<path d="M12 15V4M7.5 8.5 12 4l4.5 4.5M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5"/>',
        "inbox": '<path d="M4 7h16v13H4zM3 4h18v3H3M8 12h8M12 12v5"/>',
        "manual_upload": '<path d="M5 11v9h14v-9M8 8l4-4 4 4M12 4v12"/>',
    }
    return f'<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{paths[name]}</svg>'


def report_query(environ: dict) -> tuple[dict[str, str], object]:
    query = parse_qs(environ.get("QUERY_STRING", ""))
    raw = {key: values[-1].strip() for key, values in query.items() if values}
    return raw, parse_report_filters(raw)


def report_tabs(current: str) -> str:
    links = "".join(
        f'<a class="report-tab {"active" if path == current else ""}" href="{path}">{report_icon(icon)}<span>{e(label)}</span></a>'
        for path, label, icon in REPORT_SECTIONS
    )
    return f'<nav class="report-tabs" aria-label="Seções de relatórios">{links}</nav>'


def report_filter_form(action: str, filters, option_data: dict, *, audit: bool = False, method: bool = False, status: bool = True) -> str:
    statuses = "".join(
        f'<option value="{value}" {"selected" if filters.status == value else ""}>{label}</option>'
        for value, label in (("success", "Sucesso"), ("failed", "Falhou"), ("partial", "Parcial"),
                             ("running", "Em execução"), ("cancelled", "Cancelado"))
    )
    methods = "".join(
        f'<option value="{value}" {"selected" if filters.method == value else ""}>{label}</option>'
        for value, label in (("ssh", "SSH"), ("ftp", "FTP"), ("manual", "Manual"), ("rclone", "rclone"), ("telegram", "Telegram"))
    )
    audit_fields = f'''
      <label>Usuário<select name="user_id"><option value="">Todos</option>{option_rows(option_data["users"], str(filters.user_id or ""))}</select></label>
      <label>Evento<input name="event" value="{e(filters.event)}" placeholder="Ex.: backup, lifecycle"></label>''' if audit else ""
    method_field = f'<label>Método<select name="method"><option value="">Todos</option>{methods}</select></label>' if method else ""
    status_field = f'<label>Status<select name="status"><option value="">Todos os status</option>{statuses}</select></label>' if status else ""
    return f'''
    <form method="get" action="{action}" class="panel report-filters">
      <div class="report-filter-head">
        <div><span class="report-filter-icon"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16M7 12h10M10 19h4"/></svg></span><span><strong>Filtrar relatório</strong><small>Refine os registros exibidos.</small></span></div>
        <a href="{action}">Limpar filtros</a>
      </div>
      <div class="report-filter-main">
        <label>Data inicial<input type="date" name="start" value="{e(filters.start)}"></label>
        <label>Data final<input type="date" name="end" value="{e(filters.end)}"></label>
        <label>Equipamento<select name="equipment_id"><option value="">Todos os equipamentos</option>{option_rows(option_data["equipment"], str(filters.equipment_id or ""))}</select></label>
        {status_field}
        {method_field}
        <button type="submit"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 5h16M7 12h10M10 19h4"/></svg>Aplicar filtros</button>
      </div>
      <details class="report-more-filters"><summary>Mais filtros</summary>
        <div class="report-filter-extra">
          <label>Empresa<input name="company" value="{e(filters.company)}" placeholder="Todas"></label>
          <label>Grupo<select name="group_id"><option value="">Todos</option>{option_rows(option_data["groups"], str(filters.group_id or ""))}</select></label>
          <label>Fabricante<select name="vendor_id"><option value="">Todos</option>{option_rows(option_data["vendors"], str(filters.vendor_id or ""))}</select></label>
          <label>POP<select name="pop_id"><option value="">Todos</option>{option_rows(option_data["pops"], str(filters.pop_id or ""))}</select></label>
          {audit_fields}
          <label>Por página<select name="per_page">{''.join(f'<option value="{size}" {"selected" if filters.per_page == size else ""}>{size}</option>' for size in (25, 50, 100))}</select></label>
        </div>
      </details>
    </form>'''


def report_header(current: str, title: str, description: str) -> str:
    return (f'<header class="page-head report-page-head"><div><span class="eyebrow">Análise operacional</span><h1>{e(title)}</h1>'
            f'<p>{e(description)}</p></div></header>{report_tabs(current)}')


def report_pager(path: str, filters, total: int) -> str:
    pages = max(1, (total + filters.per_page - 1) // filters.per_page)
    base = report_filter_values(filters, include_page=False)
    previous = f'{path}?{urlencode({**base, "page": filters.page - 1})}' if filters.page > 1 else ""
    following = f'{path}?{urlencode({**base, "page": filters.page + 1})}' if filters.page < pages else ""
    return f'''<div class="pager">
      {f'<a class="button secondary" href="{e(previous)}">Anterior</a>' if previous else ''}
      <span class="muted">Página {filters.page} de {pages} · {total} registros</span>
      {f'<a class="button secondary" href="{e(following)}">Próxima</a>' if following else ''}
    </div>'''


def duration_label(value: object) -> str:
    if value is None:
        return "-"
    seconds = int(value) / 1000
    return f"{seconds:.1f}s" if seconds < 60 else f"{seconds / 60:.1f}min"
