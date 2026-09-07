#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/backup-manager-local"
STORAGE_DIR="/var/lib/backup-manager-local"
BACKUP_ROOT="/var/backups/backup-manager-local/enable-ftp"
DB="${APP_DIR}/data/backup_manager.sqlite3"
PURE_DIR="/etc/pure-ftpd"
PASSWD="${PURE_DIR}/pureftpd.passwd"
PUREDB="${PURE_DIR}/pureftpd.pdb"
SUDOERS="/etc/sudoers.d/backup-manager-ftp"
HELPER="/usr/local/sbin/backup-manager-ftp-admin"
SERVICE="/etc/systemd/system/backup-manager-ftp-importer.service"
TIMER="/etc/systemd/system/backup-manager-ftp-importer.timer"
DRY_RUN=0
BACKUP_DIR=""
COMMITTED=0

usage() {
  echo "Uso: sudo bash scripts/enable-ftp.sh [--dry-run]" >&2
}

for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Parametro desconhecido: ${arg}" >&2; usage; exit 2 ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || { echo "Execute como root." >&2; exit 1; }
[[ -f "${DB}" ]] || { echo "Instalacao existente nao encontrada: ${DB}" >&2; exit 3; }

required=(python3 sha256sum pure-pw pure-ftpd-wrapper visudo systemctl install cp find getent id timeout sort xargs)
for command_name in "${required[@]}"; do
  command -v "${command_name}" >/dev/null 2>&1 || { echo "Dependencia ausente: ${command_name}" >&2; exit 4; }
done
getent passwd backupftp >/dev/null || { echo "Usuario de sistema backupftp ausente." >&2; exit 4; }
getent group backupftp >/dev/null || { echo "Grupo de sistema backupftp ausente." >&2; exit 4; }

FTP_UID="$(id -u backupftp)"
FTP_GID="$(id -g backupftp)"
[[ "${FTP_UID}" =~ ^[0-9]+$ && "${FTP_GID}" =~ ^[0-9]+$ ]] || { echo "UID/GID backupftp invalidos." >&2; exit 4; }

if (( DRY_RUN )); then
  cat <<EOF
DRY-RUN: nenhuma alteracao sera realizada.
- criaria backup transacional de ${PURE_DIR}, PureDB/passwd, sudoers, helper, units e estado FTP do banco;
- geraria MANIFEST.sha256 e MANIFEST.txt em ${BACKUP_ROOT}/<timestamp>;
- reconciliaria somente Pure-FTPd, helper, sudoers e importer;
- usaria MinUID dinamico ${FTP_UID};
- preservaria PureDB, contas, homes e uploads existentes;
- validaria pure-ftpd-wrapper e visudo antes de qualquer restart;
- nao alteraria Nginx, certificados, interface, SSH ou UFW.
EOF
  exit 0
fi

backup_one() {
  local source="$1"
  local relative="${source#/}"
  if [[ -e "${source}" || -L "${source}" ]]; then
    install -d -m 0700 "${BACKUP_DIR}/files/$(dirname "${relative}")"
    cp -a -- "${source}" "${BACKUP_DIR}/files/${relative}"
    printf 'present\t%s\n' "${source}" >>"${BACKUP_DIR}/MANIFEST.txt"
  else
    printf 'absent\t%s\n' "${source}" >>"${BACKUP_DIR}/MANIFEST.txt"
  fi
}

restore_one() {
  local target="$1"
  local relative="${target#/}"
  local saved="${BACKUP_DIR}/files/${relative}"
  if [[ -e "${saved}" || -L "${saved}" ]]; then
    rm -rf -- "${target}"
    install -d -m 0755 "$(dirname "${target}")"
    cp -a -- "${saved}" "${target}"
  else
    rm -rf -- "${target}"
  fi
}

rollback() {
  local status=$?
  trap - ERR INT TERM EXIT
  if (( COMMITTED == 0 )) && [[ -n "${BACKUP_DIR}" ]]; then
    echo "Falha detectada; restaurando estado anterior a partir de ${BACKUP_DIR}." >&2
    systemctl stop pure-ftpd.service >/dev/null 2>&1 || true
    restore_one "${PURE_DIR}"
    restore_one "${SUDOERS}"
    restore_one "${HELPER}"
    restore_one "${SERVICE}"
    restore_one "${TIMER}"
    python3 - "${DB}" "${BACKUP_DIR}/ftp-setting.json" <<'PY' || true
import json, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
state = json.load(open(sys.argv[2], encoding="utf-8"))
if state["present"]:
    db.execute("INSERT INTO settings(key,value) VALUES('ftp_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (state["value"],))
else:
    db.execute("DELETE FROM settings WHERE key='ftp_enabled'")
db.commit()
PY
    systemctl daemon-reload >/dev/null 2>&1 || true
    if [[ -f "${BACKUP_DIR}/pure-ftpd.was-active" ]]; then systemctl restart pure-ftpd.service >/dev/null 2>&1 || true; fi
    if [[ -f "${BACKUP_DIR}/importer-timer.was-enabled" ]]; then systemctl enable backup-manager-ftp-importer.timer >/dev/null 2>&1 || true; else systemctl disable backup-manager-ftp-importer.timer >/dev/null 2>&1 || true; fi
    if [[ -f "${BACKUP_DIR}/importer-timer.was-active" ]]; then systemctl start backup-manager-ftp-importer.timer >/dev/null 2>&1 || true; fi
    echo "Rollback concluido." >&2
  fi
  exit "${status}"
}
trap rollback ERR INT TERM EXIT

umask 077
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${BACKUP_ROOT}/${timestamp}-$$"
install -d -o root -g root -m 0700 "${BACKUP_DIR}"
: >"${BACKUP_DIR}/MANIFEST.txt"
systemctl is-active --quiet pure-ftpd.service && : >"${BACKUP_DIR}/pure-ftpd.was-active" || true
systemctl is-enabled --quiet backup-manager-ftp-importer.timer && : >"${BACKUP_DIR}/importer-timer.was-enabled" || true
systemctl is-active --quiet backup-manager-ftp-importer.timer && : >"${BACKUP_DIR}/importer-timer.was-active" || true

backup_one "${PURE_DIR}"
backup_one "${PASSWD}"
backup_one "${PUREDB}"
backup_one "${SUDOERS}"
backup_one "${HELPER}"
backup_one "${SERVICE}"
backup_one "${TIMER}"
python3 - "${DB}" "${BACKUP_DIR}/ftp-setting.json" <<'PY'
import json, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
row = db.execute("SELECT value FROM settings WHERE key='ftp_enabled'").fetchone()
with open(sys.argv[2], "w", encoding="utf-8") as output:
    json.dump({"present": row is not None, "value": row[0] if row else None}, output)
PY
(
  cd "${BACKUP_DIR}"
  find files -type f -print0 | sort -z | xargs -0 -r sha256sum >MANIFEST.sha256
)

# Nunca recrie um PureDB existente: isso preserva contas e credenciais.
install -d -o root -g root -m 0755 "${PURE_DIR}/conf" "${PURE_DIR}/auth"
if [[ ! -e "${PASSWD}" ]]; then install -o root -g root -m 0600 /dev/null "${PASSWD}"; fi
if [[ ! -e "${PUREDB}" ]]; then
  pure-pw mkdb "${PUREDB}" -f "${PASSWD}"
fi
chown root:root "${PASSWD}" "${PUREDB}"
chmod 0600 "${PASSWD}" "${PUREDB}"

write_conf() { printf '%s\n' "$2" >"${PURE_DIR}/conf/$1"; chmod 0644 "${PURE_DIR}/conf/$1"; }
write_conf MinUID "${FTP_UID}"
write_conf NoAnonymous yes
write_conf ChrootEveryone yes
write_conf AllowUserFXP no
write_conf AllowAnonymousFXP no
write_conf KeepAllFiles yes
write_conf NoRename yes
# Arquivos novos ficam write-only (0200): STOR funciona e RETR falha; root/importer ainda le.
write_conf Umask '477 077'
ln -sfn ../conf/PureDB "${PURE_DIR}/auth/50pure"
# A instalacao dedicada autentica somente contas virtuais PureDB.
rm -f "${PURE_DIR}/auth/65unix" "${PURE_DIR}/auth/70pam"

install -o root -g root -m 0750 "${APP_DIR}/scripts/ftp-admin-helper.py" "${HELPER}"
install -d -o root -g root -m 0755 /etc/sudoers.d
install -o root -g root -m 0440 "${APP_DIR}/deploy/sudoers/backup-manager-ftp" "${SUDOERS}"
install -o root -g root -m 0644 "${APP_DIR}/deploy/systemd/backup-manager-ftp-importer.service" "${SERVICE}"
install -o root -g root -m 0644 "${APP_DIR}/deploy/systemd/backup-manager-ftp-importer.timer" "${TIMER}"
install -d -o root -g backupftp -m 0750 "${STORAGE_DIR}/ftp-incoming" "${STORAGE_DIR}/ftp-incoming/accounts"

visudo -cf "${SUDOERS}" >/dev/null
# --show-options percorre e converte todos os arquivos conf sem iniciar/listen no daemon.
timeout 10 pure-ftpd-wrapper --show-options >/dev/null
python3 -m py_compile "${HELPER}"

python3 - "${DB}" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("INSERT INTO settings(key,value) VALUES('ftp_enabled','1') ON CONFLICT(key) DO UPDATE SET value='1'")
db.commit()
PY

# Reinicios acontecem somente depois de todas as validacoes acima.
systemctl daemon-reload
systemctl enable pure-ftpd.service backup-manager-ftp-importer.timer >/dev/null
systemctl restart pure-ftpd.service
systemctl is-active --quiet pure-ftpd.service
systemctl restart backup-manager-ftp-importer.timer
systemctl is-active --quiet backup-manager-ftp-importer.timer

COMMITTED=1
trap - ERR INT TERM EXIT
echo "FTP habilitado com sucesso. Backup e manifesto: ${BACKUP_DIR}"
