#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${APP_DIR}"
export BACKUP_MANAGER_ENV=development
exec python3 -m backup_manager.dev_server
