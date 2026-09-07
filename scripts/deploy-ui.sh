#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="backup-manager-local.service"
LOGIN_URL="${BACKUP_MANAGER_LOGIN_URL:-http://127.0.0.1:8080/login}"

cd "${APP_DIR}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Erro: execute com sudo: sudo ${APP_DIR}/scripts/deploy-ui.sh" >&2
  exit 1
fi

echo "[1/7] Validando sintaxe Python"
python3 -m py_compile run.py backup_manager/*.py

echo "[2/7] Validando templates, CSS e JavaScript"
python3 -m backup_manager.presentation_check
bash -n scripts/run-dev.sh scripts/deploy-ui.sh

echo "[3/7] Validando diff"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git diff --check
fi

echo "[4/7] Executando suíte completa"
python3 -m unittest discover -s tests

echo "[5/7] Confirmando configuração segura de produção"
unit_file="$(systemctl show "${SERVICE_NAME}" -p FragmentPath --value)"
if [[ -z "${unit_file}" || ! -r "${unit_file}" ]]; then
  echo "Erro: unit ${SERVICE_NAME} não encontrada." >&2
  exit 1
fi
if grep -Eqi 'BACKUP_MANAGER_ENV=development|dev_server|--reload|--debug' "${unit_file}"; then
  echo "Erro: autoreload/debug detectado na unit de produção ${unit_file}." >&2
  exit 1
fi

echo "[6/7] Reiniciando ${SERVICE_NAME} após validações aprovadas"
systemctl restart "${SERVICE_NAME}"
for attempt in {1..15}; do
  if systemctl is-active --quiet "${SERVICE_NAME}"; then
    break
  fi
  if [[ "${attempt}" -eq 15 ]]; then
    systemctl --no-pager --full status "${SERVICE_NAME}" >&2 || true
    exit 1
  fi
  sleep 1
done

echo "[7/7] Verificando login e assets versionados"
login_html="$(mktemp)"
trap 'rm -f "${login_html}"' EXIT
login_ready=0
for attempt in {1..15}; do
  if curl --fail --silent --max-time 5 "${LOGIN_URL}" -o "${login_html}"; then
    login_ready=1
    break
  fi
  sleep 1
done
if [[ "${login_ready}" -ne 1 ]]; then
  echo "Erro: ${LOGIN_URL} não respondeu após o restart." >&2
  systemctl --no-pager --full status "${SERVICE_NAME}" >&2 || true
  exit 1
fi
grep -q 'login-reference-form' "${login_html}"
grep -Eq '/static/app\.css\?v=[0-9a-f]+-[0-9a-f]+' "${login_html}"
grep -Eq '/static/app\.js\?v=[0-9a-f]+-[0-9a-f]+' "${login_html}"

echo "deploy-ui ok service=${SERVICE_NAME} login=${LOGIN_URL}"
