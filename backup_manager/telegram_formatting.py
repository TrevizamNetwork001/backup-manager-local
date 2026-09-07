from __future__ import annotations

import html

STATUS_LABELS = {
    "healthy": "Normal", "warning": "Atenção", "critical": "Crítico",
    "configured": "Configurado", "queued": "Aguardando envio", "pending": "Aguardando envio",
    "sent": "Enviado", "retry_wait": "Aguardando nova tentativa", "failed": "Falhou",
    "skipped": "Ignorado", "cancelled": "Cancelado", "processing": "Processando",
}


def tg_escape(value: object) -> str:
    return html.escape(str(value or ""), quote=False)


def pt_number(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def pt_size(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    number = float(max(0, value))
    for unit in units:
        if number < 1024 or unit == units[-1]:
            return f"{int(number)} B" if unit == "B" else f"{pt_number(number)} {unit}"
        number /= 1024
    return f"{value} B"


def status_label(value: object) -> str:
    text = str(value or "")
    return STATUS_LABELS.get(text, text)


SEPARATOR = "────────────"
