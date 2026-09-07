from __future__ import annotations

import os
import re
from html import escape
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT_DIR / "templates"
STATIC_DIR = ROOT_DIR / "static"
PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z][a-zA-Z0-9_]*)\s*}}")
_STATIC_VERSIONS: dict[Path, tuple[int, int, str]] = {}


def escape_html(value: object) -> str:
    """Escape display values consistently, including NULLs and numbers."""
    return escape("" if value is None else str(value), quote=True)


def development_mode(environ: dict[str, str] | None = None) -> bool:
    """O reload é opt-in e nunca é inferido do host ou da requisição."""
    source = environ if environ is not None else os.environ
    return source.get("BACKUP_MANAGER_ENV", "").lower() == "development"


def render_template(name: str, **context: object) -> str:
    """Lê o template em cada renderização para refletir alterações sem restart."""
    target = (TEMPLATE_DIR / name).resolve()
    if not str(target).startswith(str(TEMPLATE_DIR.resolve())) or not target.is_file():
        raise FileNotFoundError(f"Template não encontrado: {name}")
    source = target.read_text(encoding="utf-8")

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            raise KeyError(f"Variável ausente no template {name}: {key}")
        return str(context[key])

    return PLACEHOLDER_RE.sub(replace, source)


def static_version(path: Path) -> str:
    """Usa mtime_ns e tamanho; o cache evita recomputar a versão sem alteração."""
    stat = path.stat()
    cached = _STATIC_VERSIONS.get(path)
    signature = (stat.st_mtime_ns, stat.st_size)
    if cached and cached[:2] == signature:
        return cached[2]
    version = f"{stat.st_mtime_ns:x}-{stat.st_size:x}"
    _STATIC_VERSIONS[path] = (*signature, version)
    return version


def static_url(name: str) -> str:
    target = (STATIC_DIR / name).resolve()
    if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
        raise FileNotFoundError(f"Arquivo estático não encontrado: {name}")
    return f"/static/{name}?v={static_version(target)}"


def validate_templates() -> list[str]:
    """Retorna placeholders encontrados; também valida leitura e nomes."""
    names: list[str] = []
    for target in sorted(TEMPLATE_DIR.rglob("*.html")):
        source = target.read_text(encoding="utf-8")
        names.extend(match.group(1) for match in PLACEHOLDER_RE.finditer(source))
    return names
