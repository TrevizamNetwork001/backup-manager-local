from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode, urlsplit
from wsgiref.util import setup_testing_defaults

os.environ.setdefault("BACKUP_MANAGER_DATA", tempfile.mkdtemp(prefix="backup-manager-presentation-test-"))
os.environ.setdefault("BACKUP_MANAGER_DB", os.path.join(os.environ["BACKUP_MANAGER_DATA"], "test.sqlite3"))
os.environ.setdefault("BACKUP_MANAGER_STORAGE_ROOT", tempfile.mkdtemp(prefix="backup-manager-presentation-storage-"))
os.environ.setdefault("BACKUP_MANAGER_SECRET_KEY", os.path.join(os.environ["BACKUP_MANAGER_DATA"], "secret.key"))
os.environ.setdefault("BACKUP_MANAGER_SERVICE_SNAPSHOT", os.path.join(os.environ["BACKUP_MANAGER_DATA"], "service-status.json"))

from backup_manager.app import application
from backup_manager.db import TEMP_ADMIN_PASSWORD, migrate
from backup_manager.presentation import (
    TEMPLATE_DIR,
    development_mode,
    render_template,
    static_url,
    static_version,
    validate_templates,
)
from backup_manager.presentation_check import validate_all, validate_css


ROOT = Path(__file__).resolve().parents[1]


class WsgiClient:
    def __init__(self) -> None:
        self.cookie = ""

    def request(self, method: str, path: str, data: dict[str, str] | None = None):
        body = urlencode(data or {}).encode("utf-8")
        parsed = urlsplit(path)
        environ: dict = {}
        setup_testing_defaults(environ)
        stream = tempfile.SpooledTemporaryFile()
        try:
            stream.write(body)
            stream.seek(0)
            environ.update({
                "REQUEST_METHOD": method, "PATH_INFO": parsed.path,
                "QUERY_STRING": parsed.query, "CONTENT_LENGTH": str(len(body)),
                "CONTENT_TYPE": "application/x-www-form-urlencoded", "wsgi.input": stream,
            })
            if self.cookie:
                environ["HTTP_COOKIE"] = self.cookie
            captured: dict = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            response = b"".join(application(environ, start_response)).decode("utf-8", errors="replace")
            headers = dict(captured["headers"])
            if "Set-Cookie" in headers:
                self.cookie = headers["Set-Cookie"].split(";", 1)[0]
            return captured["status"], headers, response
        finally:
            stream.close()


class PresentationArchitectureTests(unittest.TestCase):
    def test_telegram_topic_mapping_uses_modern_guided_layout(self) -> None:
        view = (ROOT / "backup_manager/telegram_backup_views.py").read_text(encoding="utf-8")
        css = (ROOT / "static/app.css").read_text(encoding="utf-8")
        javascript = (ROOT / "static/app.js").read_text(encoding="utf-8")
        self.assertIn("telegram-backup-tabs", view)
        self.assertIn("telegram-topics-page", view)
        self.assertIn("data-topic-mapping-form", view)
        self.assertIn("Grupo de equipamentos", view)
        self.assertIn("ID do tópico", view)
        self.assertNotIn("<p><a href=\"/telegram-backup?view=settings\"", view)
        self.assertIn(".telegram-topic-form", css)
        self.assertIn("@media (max-width: 650px)", css)
        self.assertIn("data-topic-mapping-type", javascript)

    def test_telegram_backup_settings_use_guided_operational_form(self) -> None:
        view = (ROOT / "backup_manager/telegram_backup_views.py").read_text(encoding="utf-8")
        css = (ROOT / "static/app.css").read_text(encoding="utf-8")
        self.assertIn("telegram-settings-page", view)
        self.assertIn("telegram-settings-toggle", view)
        self.assertIn("Limites e novas tentativas", view)
        self.assertIn("Tamanho máximo do arquivo", view)
        self.assertIn("As políticas têm prioridade", view)
        self.assertIn(".telegram-settings-form", css)

    def test_telegram_destinations_use_purpose_cards_and_safe_list(self) -> None:
        view = (ROOT / "backup_manager/telegram_backup_views.py").read_text(encoding="utf-8")
        css = (ROOT / "static/app.css").read_text(encoding="utf-8")
        self.assertIn("telegram-destinations-page", view)
        self.assertIn("telegram-destination-purpose", view)
        self.assertIn("Arquivos de backup", view)
        self.assertIn("Enviar arquivo de teste", view)
        self.assertIn("Nenhum destino cadastrado", view)
        self.assertIn(".telegram-destination-form", css)

    def test_telegram_policies_use_conditional_scope_and_file_controls(self) -> None:
        view = (ROOT / "backup_manager/telegram_backup_views.py").read_text(encoding="utf-8")
        javascript = (ROOT / "static/app.js").read_text(encoding="utf-8")
        self.assertIn("telegram-policies-page", view)
        self.assertIn("data-telegram-policy-form", view)
        self.assertIn("Todos os equipamentos", view)
        self.assertIn("Todos os tipos de arquivo", view)
        self.assertIn("Nível de compactação", view)
        self.assertIn("data-policy-scope", javascript)

    def test_telegram_queue_and_history_translate_and_filter_items(self) -> None:
        view = (ROOT / "backup_manager/telegram_backup_views.py").read_text(encoding="utf-8")
        javascript = (ROOT / "static/app.js").read_text(encoding="utf-8")
        css = (ROOT / "static/app.css").read_text(encoding="utf-8")
        self.assertIn("telegram-items-page", view)
        self.assertIn("Fila de envios", view)
        self.assertIn("Histórico de envios", view)
        self.assertIn("Arquivo acima do limite", view)
        self.assertIn("data-telegram-item-search", view)
        self.assertIn("data-telegram-items-page", javascript)
        self.assertIn(".telegram-items-panel", css)

    def test_shared_topbar_keeps_user_area_in_flow_across_viewports(self) -> None:
        topbar = render_template(
            "components/topbar.html", full_name="Administrador",
            username="admin", role_label="Administrador",
            client_ip="198.51.100.25", initial="A", avatar_content="A",
            avatar_options="opções", avatar_csrf="token",
        )
        css = (ROOT / "static/app.css").read_text(encoding="utf-8")
        self.assertIn('class="topbar-tools"', topbar)
        self.assertIn('class="topbar-user-text"', topbar)
        self.assertIn('class="topbar-user-name"', topbar)
        self.assertIn('class="topbar-user-meta"', topbar)
        self.assertIn('class="topbar-account-menu"', topbar)
        self.assertIn('href="/change-password"', topbar)
        self.assertIn('action="/profile/avatar"', topbar)
        self.assertIn(".topbar-tools { display: flex;", css)
        self.assertIn("flex-wrap: nowrap", css)
        self.assertIn("min-height: 48px", css)
        self.assertIn("max-height: 48px", css)
        self.assertIn(".topbar-user-text { display: flex;", css)
        self.assertIn(".topbar-user-name,.topbar-user-meta", css)
        self.assertIn("@media (max-width: 860px)", css)
        self.assertIn("@media (max-width: 520px)", css)
        self.assertNotIn(".topbar-user > div { display: none; }", css)

    def test_dashboard_environment_header_icon_has_bounded_svg_style(self) -> None:
        css = (ROOT / "static/app.css").read_text(encoding="utf-8")
        dashboard = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
        self.assertIn('class="panel dashboard-environments"', dashboard)
        self.assertIn(".dashboard-environments > header > i { display: grid; width: 27px;", css)
        self.assertIn(".dashboard-environments > header > i svg { width: 15px; height: 15px;", css)

    def test_dashboard_security_header_icon_has_bounded_svg_style(self) -> None:
        css = (ROOT / "static/app.css").read_text(encoding="utf-8")
        dashboard = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")
        self.assertIn('class="panel dashboard-security', dashboard)
        self.assertIn(".dashboard-security > header > i { display: grid; width: 27px;", css)
        self.assertIn(".dashboard-security > header > i svg { width: 15px; height: 15px;", css)

    def test_login_and_dashboard_are_runtime_templates(self) -> None:
        self.assertTrue((TEMPLATE_DIR / "login.html").is_file())
        self.assertTrue((TEMPLATE_DIR / "dashboard.html").is_file())
        self.assertIn("autocomplete=\"username\"", render_template("login.html", error_message=""))
        self.assertIn("autocomplete=\"current-password\"", render_template("login.html", error_message=""))
        self.assertIn("dashboard.html", (ROOT / "backup_manager/dashboard_views.py").read_text(encoding="utf-8"))
        self.assertGreater(len(validate_templates()), 10)

    def test_dashboard_refreshes_when_returning_to_foreground(self) -> None:
        dashboard = (TEMPLATE_DIR / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn('document.addEventListener("visibilitychange"', dashboard)
        self.assertIn('window.addEventListener("pageshow"', dashboard)
        self.assertIn("window.location.reload()", dashboard)

    def test_template_changes_are_read_without_process_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dynamic.html"
            target.write_text("primeiro {{value}}", encoding="utf-8")
            with mock.patch("backup_manager.presentation.TEMPLATE_DIR", Path(directory)):
                self.assertEqual(render_template("dynamic.html", value="valor"), "primeiro valor")
                target.write_text("segundo {{value}}", encoding="utf-8")
                self.assertEqual(render_template("dynamic.html", value="valor"), "segundo valor")

    def test_static_version_changes_only_when_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "app.css"
            target.write_text("a{}", encoding="utf-8")
            first = static_version(target)
            self.assertEqual(static_version(target), first)
            time.sleep(0.002)
            target.write_text("a{color:red}", encoding="utf-8")
            self.assertNotEqual(static_version(target), first)
        self.assertRegex(static_url("app.css"), r"^/static/app\.css\?v=[0-9a-f]+-[0-9a-f]+$")
        self.assertRegex(static_url("app.js"), r"^/static/app\.js\?v=[0-9a-f]+-[0-9a-f]+$")

    def test_development_mode_is_explicit_and_production_service_has_no_reload(self) -> None:
        self.assertFalse(development_mode({}))
        self.assertFalse(development_mode({"BACKUP_MANAGER_ENV": "production"}))
        self.assertTrue(development_mode({"BACKUP_MANAGER_ENV": "development"}))
        service = (ROOT / "deploy/systemd/backup-manager-local.service").read_text(encoding="utf-8")
        self.assertNotIn("development", service.lower())
        self.assertNotIn("reload", service.lower())
        self.assertIn("backup_manager.dev_server", (ROOT / "scripts/run-dev.sh").read_text(encoding="utf-8"))

    def test_deploy_ui_validates_before_controlled_restart(self) -> None:
        templates, placeholders = validate_all()
        self.assertGreaterEqual(templates, 5)
        self.assertGreater(placeholders, 10)
        script = (ROOT / "scripts/deploy-ui.sh").read_text(encoding="utf-8")
        tests_at = script.index("python3 -m unittest discover -s tests")
        restart_at = script.index('systemctl restart "${SERVICE_NAME}"')
        login_at = script.index('curl --fail --silent --max-time')
        self.assertLess(tests_at, restart_at)
        self.assertLess(restart_at, login_at)
        for forbidden in ("git pull", "git reset", "git checkout", " migrate", "rm -rf"):
            self.assertNotIn(forbidden, script)
        self.assertIn("systemctl is-active", script)
        self.assertIn("BACKUP_MANAGER_ENV=development", script)
        self.assertIn("login_ready=0", script)
        self.assertIn('Erro: ${LOGIN_URL} não respondeu após o restart.', script)

    def test_css_validator_rejects_unbalanced_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.css"
            invalid.write_text(".broken { color: red;", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_css(invalid)

    def test_versioned_assets_and_cache_headers(self) -> None:
        migrate(31)
        client = WsgiClient()
        status, headers, body = client.request("GET", "/login")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("/static/app.css?v=", body)
        self.assertIn("/static/app.js?v=", body)
        version = static_url("app.css").split("?v=", 1)[1]
        status, headers, _ = client.request("GET", f"/static/app.css?v={version}")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(headers["Cache-Control"], "public, max-age=31536000, immutable")
        status, headers, _ = client.request("GET", "/static/app.css")
        self.assertEqual(headers["Cache-Control"], "no-cache")


class TemplatePageIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(os.environ["BACKUP_MANAGER_DATA"], ignore_errors=True)
        shutil.rmtree(os.environ["BACKUP_MANAGER_STORAGE_ROOT"], ignore_errors=True)
        os.makedirs(os.environ["BACKUP_MANAGER_DATA"], exist_ok=True)
        os.makedirs(os.environ["BACKUP_MANAGER_STORAGE_ROOT"], exist_ok=True)
        os.environ["BACKUP_MANAGER_SECRET_KEY"] = os.path.join(os.environ["BACKUP_MANAGER_DATA"], "secret.key")
        migrate(31)
        self.client = WsgiClient()

    def test_login_and_dashboard_still_render_through_templates(self) -> None:
        status, _, login = self.client.request("GET", "/login")
        self.assertTrue(status.startswith("200"))
        self.assertIn("login-reference-page", login)
        self.assertIn("login-brand-content", login)
        self.assertIn("login-reference-form", login)
        self.assertIn("Backup Manager", login)
        self.assertIn("Acesso ao sistema", login)
        self.assertIn('action="/login"', login)
        self.assertIn('method="post"', login)
        self.assertIn('name="username"', login)
        self.assertIn('name="password"', login)
        self.assertIn('autocomplete="username"', login)
        self.assertIn('type="submit"', login)
        self.assertIn("Entrar no sistema", login)
        self.client.request("POST", "/login", {"username": "admin", "password": TEMP_ADMIN_PASSWORD})
        self.client.request("POST", "/change-password", {
            "current_password": TEMP_ADMIN_PASSWORD, "new_password": "NovaSenha!2026",
            "confirm_password": "NovaSenha!2026",
        })
        self.client.request("POST", "/setup", {
            "installation_name": "Backup Manager Lab", "provider_name": "Provedor Local",
            "timezone": "America/Sao_Paulo", "storage_root": os.environ["BACKUP_MANAGER_STORAGE_ROOT"],
            "primary_environment": "Producao", "pops": "POP Principal, Sao Paulo, SP",
        })
        status, _, dashboard = self.client.request("GET", "/")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Centro de Operações", dashboard)
        self.assertIn("Atividade recente", dashboard)
        self.assertIn("Próximas execuções", dashboard)


if __name__ == "__main__":
    unittest.main()
