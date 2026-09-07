from __future__ import annotations

from pathlib import Path

from .presentation import STATIC_DIR, TEMPLATE_DIR, validate_templates


REQUIRED_TEMPLATES = {
    "base.html",
    "login.html",
    "dashboard.html",
    "components/sidebar.html",
    "components/topbar.html",
}


def validate_css(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    depth = 0
    comment = False
    quote: str | None = None
    index = 0
    while index < len(source):
        pair, char = source[index:index + 2], source[index]
        if comment:
            if pair == "*/":
                comment = False
                index += 2
                continue
        elif quote:
            if char == quote and (index == 0 or source[index - 1] != "\\"):
                quote = None
        elif pair == "/*":
            comment = True
            index += 2
            continue
        elif char in "'\"":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                raise ValueError(f"CSS inválido: fechamento extra na posição {index}")
        index += 1
    if depth or comment or quote:
        raise ValueError(f"CSS inválido: blocos={depth}, comentário={comment}, aspas={quote}")


def validate_all() -> tuple[int, int]:
    existing = {str(path.relative_to(TEMPLATE_DIR)) for path in TEMPLATE_DIR.rglob("*.html")}
    missing = REQUIRED_TEMPLATES - existing
    if missing:
        raise ValueError(f"Templates obrigatórios ausentes: {', '.join(sorted(missing))}")
    placeholders = validate_templates()
    if not placeholders:
        raise ValueError("Nenhum placeholder de template encontrado.")
    validate_css(STATIC_DIR / "app.css")
    if not (STATIC_DIR / "app.js").is_file():
        raise ValueError("JavaScript principal ausente: static/app.js")
    return len(existing), len(placeholders)


def main() -> None:
    templates, placeholders = validate_all()
    print(f"presentation-check ok templates={templates} placeholders={placeholders} css=ok js=ok")


if __name__ == "__main__":
    main()
