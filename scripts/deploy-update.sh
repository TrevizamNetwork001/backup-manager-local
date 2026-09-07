#!/usr/bin/env bash
set -euo pipefail

if [[ "${BACKUP_MANAGER_DEPLOY_LOCKED:-0}" != "1" ]]; then
  exec python3 - "$0" "$@" <<'PY'
import fcntl, os, stat, sys
path = "/run/lock/backup-manager-deploy.lock"
flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
if not getattr(os, "O_NOFOLLOW", 0):
    raise SystemExit("DEPLOY_LOCK_INVALID")
descriptor = os.open(path, flags, 0o600)
if not stat.S_ISREG(os.fstat(descriptor).st_mode):
    raise SystemExit("DEPLOY_LOCK_INVALID")
os.fchmod(descriptor, 0o600)
try:
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("DEPLOY_LOCKED")
environment = os.environ.copy()
environment["BACKUP_MANAGER_DEPLOY_LOCKED"] = "1"
os.set_inheritable(descriptor, True)
os.execve("/usr/bin/bash", ["bash", sys.argv[1], *sys.argv[2:]], environment)
PY
fi

APP_DIR="/opt/backup-manager-local"
SERVICE_NAME="backup-manager-local"
WORKER_NAME="backup-manager-worker"
FTP_IMPORTER_NAME="backup-manager-ftp-importer"
LIFECYCLE_NAME="backup-manager-lifecycle"
OBSERVABILITY_NAME="backup-manager-observability"
DATABASE_GUARD_NAME="backup-manager-database-guard"
CLOUD_SYNC_NAME="backup-manager-cloud-sync"
TELEGRAM_BACKUP_NAME="backup-manager-telegram-backup"
UPDATE_CHECK_NAME="backup-manager-update-check"
UPDATE_DOWNLOAD_NAME="backup-manager-update-download"
NGINX_SITE="backup-manager-local"
DB_PATH="${APP_DIR}/data/backup_manager.sqlite3"
DB_BACKUP_DIR="${APP_DIR}/data/deploy-backups"
NGINX_SOURCE="${APP_DIR}/deploy/nginx/${NGINX_SITE}.conf"
NGINX_TARGET="/etc/nginx/sites-available/${NGINX_SITE}.conf"

STEPS=()
STARTED_WORKER="nao"
NGINX_CONFIG_CHANGED="nao"
SQLITE_BACKUP_PATH=""

log() {
  printf '\n==> %s\n' "$*"
}

record() {
  STEPS+=("$1: $2")
}

fail() {
  local message="$1"
  record "falha" "${message}"
  printf '\nERRO: %s\n' "${message}" >&2
  summary
  exit 1
}

run_step() {
  local label="$1"
  shift
  log "${label}"
  if "$@"; then
    record "${label}" "ok"
  else
    local code=$?
    record "${label}" "falhou (${code})"
    printf '\nERRO: etapa falhou: %s\n' "${label}" >&2
    summary
    exit "${code}"
  fi
}

summary() {
  printf '\n===== Resumo deploy-update =====\n'
  printf 'Diretorio: %s\n' "${APP_DIR}"
  printf 'Commit: %s\n' "$(git rev-parse --short HEAD 2>/dev/null || printf 'indisponivel')"
  printf 'Backup SQLite: %s\n' "${SQLITE_BACKUP_PATH:-nao criado}"
  printf 'Nginx config alterada: %s\n' "${NGINX_CONFIG_CHANGED}"
  printf 'Worker iniciado agora: %s\n' "${STARTED_WORKER}"
  printf '\nEtapas:\n'
  local step
  for step in "${STEPS[@]}"; do
    printf '%s\n' "- ${step}"
  done
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Execute como root." >&2
    return 1
  fi
}

enter_project_root() {
  cd "${APP_DIR}"
  [[ -d .git && -f scripts/install.sh && -d backup_manager ]]
}

report_git() {
  echo "Commit atual:"
  git log -1 --oneline
  echo
  echo "Status:"
  git status
}

check_python_compile() {
  mapfile -t python_files < <(git ls-files '*.py')
  if [[ "${#python_files[@]}" -eq 0 ]]; then
    echo "Nenhum arquivo Python rastreado pelo git."
    return 0
  fi
  python3 -m py_compile "${python_files[@]}"
}

run_tests() {
  python3 -m unittest discover -s tests
}

run_integrity_check() {
  local result
  result="$(python3 -m backup_manager.cli integrity-check)"
  echo "${result}"
  [[ "${result}" == "ok" ]]
}

backup_sqlite() {
  if [[ ! -f "${DB_PATH}" ]]; then
    echo "Banco SQLite ainda nao existe: ${DB_PATH}"
    return 0
  fi
  install -d -m 0750 "${DB_BACKUP_DIR}"
  SQLITE_BACKUP_PATH="${DB_BACKUP_DIR}/backup_manager.sqlite3.deploy-$(date -u +%Y%m%d%H%M%S).bak"
  DB_PATH="${DB_PATH}" SQLITE_BACKUP_PATH="${SQLITE_BACKUP_PATH}" python3 - <<'PY'
import os
import sqlite3
from pathlib import Path

source = Path(os.environ["DB_PATH"])
target = Path(os.environ["SQLITE_BACKUP_PATH"])
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
target.chmod(0o640)
PY
  echo "${SQLITE_BACKUP_PATH}"
}

apply_migrations() {
  local SCHEMA_VERSION
  SCHEMA_VERSION="$(python3 -m backup_manager.cli schema-release-version)"
  python3 -m backup_manager.cli migrate --to-version "${SCHEMA_VERSION}"
}

quiesce_runtime() {
  systemctl stop "${WORKER_NAME}.timer" "${FTP_IMPORTER_NAME}.timer" "${LIFECYCLE_NAME}.timer" \
    "${DATABASE_GUARD_NAME}.timer" "${CLOUD_SYNC_NAME}.timer" "${TELEGRAM_BACKUP_NAME}.timer" 2>/dev/null || true
  systemctl stop "${WORKER_NAME}.service" "${FTP_IMPORTER_NAME}.service" "${LIFECYCLE_NAME}.service" \
    "${DATABASE_GUARD_NAME}.service" "${CLOUD_SYNC_NAME}.service" "${TELEGRAM_BACKUP_NAME}.service" 2>/dev/null || true
  systemctl stop "${SERVICE_NAME}.service"
}

check_schema() {
  python3 -m backup_manager.cli schema-check
}

sync_systemd_units() {
  if ! id backup-manager-telegram >/dev/null 2>&1; then
    useradd --system --gid root --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin backup-manager-telegram
  fi
  chmod 0770 /var/lib/backup-manager-local/temporary "${APP_DIR}/data"
  chown backup-manager-telegram:root "${DB_PATH}"
  chmod 0640 "${DB_PATH}"
  while read -r profile unit; do
    [[ -z "${profile}" || "${profile}" == \#* ]] && continue
    [[ "${profile}" == "both" || "${profile}" == "update" ]] || continue
    [[ "${unit}" =~ ^backup-manager-[a-z0-9-]+\.(service|timer)$ ]] || { echo "Unidade inválida no manifesto: ${unit}" >&2; exit 1; }
    install -m 0644 "${APP_DIR}/deploy/systemd/${unit}" "/etc/systemd/system/${unit}"
  done <"${APP_DIR}/deploy/systemd/units.manifest"
  systemctl daemon-reload
}

sync_nginx_config_if_changed() {
  echo "Nginx e certificados preservados; deploy nao altera HTTPS/HTTP."
}

restart_services() {
  systemctl restart "${SERVICE_NAME}.service"
  systemctl restart "${WORKER_NAME}.timer"
  if systemctl is-enabled --quiet "${FTP_IMPORTER_NAME}.timer" 2>/dev/null; then systemctl restart "${FTP_IMPORTER_NAME}.timer"; fi
  systemctl enable --now "${LIFECYCLE_NAME}.timer"
  systemctl enable --now "${OBSERVABILITY_NAME}.timer"
  systemctl enable --now "${DATABASE_GUARD_NAME}.timer"
  systemctl enable --now "${CLOUD_SYNC_NAME}.timer"
  systemctl enable --now "${TELEGRAM_BACKUP_NAME}.timer"
  systemctl enable --now "${UPDATE_CHECK_NAME}.timer"
}

start_worker_if_safe() {
  if systemctl is-active --quiet "${WORKER_NAME}.service"; then
    echo "${WORKER_NAME}.service ja esta ativo; rodada manual ignorada."
    return 0
  fi
  local due_jobs
  due_jobs="$(python3 -m backup_manager.cli jobs-due)"
  echo "${due_jobs}"
  if [[ "${due_jobs}" != "nenhum job vencido" ]]; then
    echo "Ha jobs vencidos; rodada manual ignorada para nao alterar dados de storage durante o deploy."
    return 0
  fi
  systemctl start "${WORKER_NAME}.service"
  STARTED_WORKER="sim"
}

validate_services() {
  systemctl is-active --quiet "${SERVICE_NAME}.service"
  systemctl is-active --quiet "${WORKER_NAME}.timer"
  systemctl is-active --quiet nginx.service
  if systemctl is-enabled --quiet "${FTP_IMPORTER_NAME}.timer" 2>/dev/null; then systemctl is-active --quiet "${FTP_IMPORTER_NAME}.timer"; fi
  systemctl is-active --quiet "${LIFECYCLE_NAME}.timer"
  systemctl is-active --quiet "${OBSERVABILITY_NAME}.timer"
  systemctl is-active --quiet "${DATABASE_GUARD_NAME}.timer"
  systemctl is-active --quiet "${CLOUD_SYNC_NAME}.timer"
  systemctl is-active --quiet "${TELEGRAM_BACKUP_NAME}.timer"
  systemctl is-active --quiet "${UPDATE_CHECK_NAME}.timer"
}

validate_ftp_if_present() {
  if command -v pure-pw >/dev/null 2>&1; then
    python3 -m backup_manager.cli ftp-config-check
  else
    echo "Pure-FTPd nao instalado; validacao FTP ignorada."
  fi
}

validate_nginx() {
  nginx -t
  if [[ "${NGINX_CONFIG_CHANGED}" == "sim" ]]; then
    nginx -s reload
  else
    echo "Nginx nao recarregado: configuracao sem alteracao."
  fi
}

validate_login() {
  curl --fail --silent --show-error --insecure --max-time 10 https://127.0.0.1/login >/dev/null
}

validate_readiness() {
  curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8080/health/ready >/dev/null
}

main() {
  run_step "exigir execucao como root" require_root
  run_step "executar a partir da raiz do projeto" enter_project_root
  run_step "git status e commit atual" report_git
  run_step "git diff --check" git diff --check
  run_step "bash -n scripts/install.sh" bash -n scripts/install.sh
  run_step "python3 -m py_compile" check_python_compile
  run_step "testes automatizados" run_tests
  run_step "integrity-check" run_integrity_check
  run_step "interromper aplicação e workers antes da migration" quiesce_runtime
  run_step "backup seguro do SQLite" backup_sqlite
  run_step "aplicar migrations" apply_migrations
  run_step "validar schema atualizado" check_schema
  run_step "atualizar units systemd" sync_systemd_units
  run_step "atualizar nginx somente se mudou" sync_nginx_config_if_changed
  run_step "reiniciar backup-manager-local e timer" restart_services
  run_step "iniciar rodada do worker somente se seguro" start_worker_if_safe
  run_step "validar servicos ativos" validate_services
  run_step "validar readiness" validate_readiness
  run_step "validar FTP quando presente" validate_ftp_if_present
  run_step "validar nginx -t" validate_nginx
  run_step "validar GET /login" validate_login
  summary
}

main "$@"
