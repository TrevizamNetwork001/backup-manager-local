#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/backup-manager-local"
SERVICE_NAME="backup-manager-local"
WORKER_NAME="backup-manager-worker"
CLOUD_SYNC_NAME="backup-manager-cloud-sync"
TELEGRAM_BACKUP_NAME="backup-manager-telegram-backup"
NGINX_SITE="backup-manager-local"
STORAGE_DIR="/var/lib/backup-manager-local"
SECRET_DIR="/etc/backup-manager-local"
SECRET_KEY="${SECRET_DIR}/secret.key"
ENABLE_PURE_FTPD=0
FTP_CONTROL_PORT=21
FTP_PASSIVE_START=30000
FTP_PASSIVE_END=30100
FTP_PUBLIC_IP=""
FTP_PUBLIC_IPV6=""
ENABLE_FTPS=0
FTP_TLS_DOMAIN=""
ENABLE_UFW=0
ENABLE_LETSENCRYPT=0
LETSENCRYPT_DOMAIN=""
DRY_RUN=0
FORCE_REINSTALL=0
NGINX_BACKUP_DIR="/var/backups/backup-manager-local/nginx"
NGINX_HTTP_TEMPLATE="${APP_DIR}/deploy/nginx/${NGINX_SITE}-http.conf"
NGINX_LETSENCRYPT_TEMPLATE="${APP_DIR}/deploy/nginx/${NGINX_SITE}-letsencrypt.conf.template"
NGINX_TARGET="/etc/nginx/sites-available/${NGINX_SITE}.conf"
NGINX_BACKUP_PATH=""

usage() {
  echo "Uso: $0 [--dry-run] [--force-reinstall] [--enable-letsencrypt --letsencrypt-domain DOMINIO] ..."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --enable-pure-ftpd) ENABLE_PURE_FTPD=1 ;;
    --ftp-control-port) FTP_CONTROL_PORT="${2:?valor ausente}"; shift ;;
    --ftp-passive-start) FTP_PASSIVE_START="${2:?valor ausente}"; shift ;;
    --ftp-passive-end) FTP_PASSIVE_END="${2:?valor ausente}"; shift ;;
    --ftp-public-ip) FTP_PUBLIC_IP="${2:?valor ausente}"; shift ;;
    --ftp-public-ipv6) FTP_PUBLIC_IPV6="${2:?valor ausente}"; shift ;;
    --enable-ftps) ENABLE_FTPS=1 ;;
    --ftp-tls-domain) FTP_TLS_DOMAIN="${2:?valor ausente}"; shift ;;
    --enable-ufw) ENABLE_UFW=1 ;;
    --enable-letsencrypt) ENABLE_LETSENCRYPT=1 ;;
    --letsencrypt-domain) LETSENCRYPT_DOMAIN="${2:?valor ausente}"; shift ;;
    --dry-run) DRY_RUN=1 ;;
    --force-reinstall) FORCE_REINSTALL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Parametro desconhecido: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

installation_exists() {
  [[ -f "/etc/systemd/system/${SERVICE_NAME}.service" || -f "${APP_DIR}/data/backup_manager.sqlite3" ]]
}
if installation_exists; then
  if (( ! FORCE_REINSTALL )); then
    echo "Instalação existente detectada. Use scripts/deploy-update.sh para atualizar ou um script de configuração específico para habilitar recursos." >&2
    exit 3
  fi
  [[ "${FORCE_REINSTALL_CONFIRM:-}" == "I_UNDERSTAND_FORCE_REINSTALL" ]] || { echo "--force-reinstall exige FORCE_REINSTALL_CONFIRM=I_UNDERSTAND_FORCE_REINSTALL e nao deve ser usado em producao." >&2; exit 4; }
fi
if (( DRY_RUN )); then
  echo "DRY-RUN: nenhum pacote, arquivo, banco, servico ou firewall sera alterado."
  exit 0
fi

validate_letsencrypt() {
  (( ENABLE_LETSENCRYPT )) || return 0
  [[ "${LETSENCRYPT_DOMAIN}" =~ ^[A-Za-z0-9.-]+$ && "${LETSENCRYPT_DOMAIN}" != .* && "${LETSENCRYPT_DOMAIN}" != *. ]] || {
    echo "Dominio Let's Encrypt invalido ou ausente." >&2; return 1;
  }
  [[ -r "/etc/letsencrypt/live/${LETSENCRYPT_DOMAIN}/fullchain.pem" && -r "/etc/letsencrypt/live/${LETSENCRYPT_DOMAIN}/privkey.pem" ]] || {
    echo "Certificado Let's Encrypt existente nao encontrado para ${LETSENCRYPT_DOMAIN}." >&2; return 1;
  }
}

render_nginx_config() {
  local output="$1"
  if (( ENABLE_LETSENCRYPT )); then
    sed "s/__DOMAIN__/${LETSENCRYPT_DOMAIN}/g" "${NGINX_LETSENCRYPT_TEMPLATE}" >"${output}"
  else
    cp "${NGINX_HTTP_TEMPLATE}" "${output}"
  fi
}

detect_duplicate_https_blocks() {
  local candidate="$1"
  python3 - "${candidate}" "${NGINX_TARGET}" /etc/nginx/sites-enabled/* <<'PY'
import pathlib, re, sys

candidate, target, *paths = sys.argv[1:]
files = [pathlib.Path(candidate)]
target = pathlib.Path(target).resolve()
for raw in paths:
    path = pathlib.Path(raw)
    try:
        if path.resolve() == target:
            continue
        files.append(path)
    except OSError:
        pass

blocks = []
for path in files:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        continue
    for match in re.finditer(r"\bserver\s*\{(.*?)\n\}", text, re.S):
        body = match.group(1)
        listens = re.findall(r"^\s*listen\s+([^;]+);", body, re.M)
        if not any(re.search(r"(?:^|[\]:])443(?:\s|$)", value) for value in listens):
            continue
        names = tuple(re.findall(r"^\s*server_name\s+([^;]+);", body, re.M)) or ("_",)
        blocks.append((str(path), names, any("default_server" in value for value in listens)))

defaults = [block for block in blocks if block[2]]
duplicates = []
if len(defaults) > 1:
    duplicates.append("mais de um default_server em 443: " + ", ".join(block[0] for block in defaults))
seen = {}
for path, names, _ in blocks:
    for name_group in names:
        for name in name_group.split():
            if name == "_":
                continue
            if name in seen and seen[name] != path:
                duplicates.append(f"server_name {name} repetido em {seen[name]} e {path}")
            seen[name] = path
if duplicates:
    print("Blocos HTTPS duplicados detectados antes da aplicacao:", file=sys.stderr)
    print("\n".join(duplicates), file=sys.stderr)
    raise SystemExit(1)
PY
}

backup_and_install_nginx_config() {
  local candidate="$1" stamp
  stamp="$(date -u +%Y%m%d%H%M%S)"
  install -d -m 0700 "${NGINX_BACKUP_DIR}"
  if [[ -e "${NGINX_TARGET}" || -L "${NGINX_TARGET}" ]]; then
    NGINX_BACKUP_PATH="${NGINX_BACKUP_DIR}/${NGINX_SITE}.conf.${stamp}.bak"
    cp -a "${NGINX_TARGET}" "${NGINX_BACKUP_PATH}"
  fi
  install -m 0644 "${candidate}" "${NGINX_TARGET}"
}

restore_nginx_config() {
  if [[ -n "${NGINX_BACKUP_PATH}" ]]; then
    cp -a "${NGINX_BACKUP_PATH}" "${NGINX_TARGET}"
  else
    rm -f "${NGINX_TARGET}"
  fi
}

validate_served_certificate() {
  (( ENABLE_LETSENCRYPT )) || return 0
  local expected actual address certificate
  expected="$(openssl x509 -in "/etc/letsencrypt/live/${LETSENCRYPT_DOMAIN}/fullchain.pem" -noout -fingerprint -sha256)"
  for address in 127.0.0.1 '[::1]'; do
    certificate="$(timeout 10 openssl s_client -connect "${address}:443" -servername "${LETSENCRYPT_DOMAIN}" -showcerts </dev/null 2>/dev/null | sed -n '/BEGIN CERTIFICATE/,/END CERTIFICATE/p' | sed -n '1,/END CERTIFICATE/p')"
    [[ -n "${certificate}" ]] || { echo "Falha ao obter certificado via SNI em ${address}." >&2; return 1; }
    actual="$(printf '%s\n' "${certificate}" | openssl x509 -noout -fingerprint -sha256)"
    [[ "${actual}" == "${expected}" ]] || { echo "Certificado servido em ${address} nao e o Let's Encrypt esperado." >&2; return 1; }
    printf '%s\n' "${certificate}" | openssl x509 -noout -checkhost "${LETSENCRYPT_DOMAIN}" >/dev/null
  done
}

reload_and_validate_nginx() {
  if nginx -t && nginx -s reload && validate_served_certificate; then
    return 0
  fi
  echo "Reload ou validacao TLS falhou; restaurando configuracao Nginx anterior." >&2
  restore_nginx_config
  nginx -t
  nginx -s reload
  return 1
}

validate_ftp_parameters() {
  [[ "${FTP_CONTROL_PORT}" =~ ^[0-9]+$ && "${FTP_PASSIVE_START}" =~ ^[0-9]+$ && "${FTP_PASSIVE_END}" =~ ^[0-9]+$ ]] || return 1
  (( FTP_CONTROL_PORT >= 1 && FTP_CONTROL_PORT <= 65535 && FTP_PASSIVE_START >= 1 && FTP_PASSIVE_END <= 65535 && FTP_PASSIVE_START <= FTP_PASSIVE_END )) || return 1
  if (( FTP_PASSIVE_END - FTP_PASSIVE_START > 1000 )); then
    echo "Faixa passiva acima de 1001 portas recusada; reduza-a explicitamente." >&2; return 1
  fi
  python3 - "${FTP_PUBLIC_IP}" "${FTP_PUBLIC_IPV6}" <<'PY'
import ipaddress, sys
for value, version in zip(sys.argv[1:], (4, 6)):
    if value and ipaddress.ip_address(value).version != version:
        raise SystemExit(1)
PY
}

backup_pure_ftpd() {
  local destination="/var/backups/backup-manager-local/pure-ftpd/$(date -u +%Y%m%d%H%M%S)"
  install -d -m 0700 "${destination}"
  if [[ -d /etc/pure-ftpd ]]; then cp -a /etc/pure-ftpd "${destination}/pure-ftpd"; fi
  find "${destination}" -type f -printf '%P %s bytes\n' >"${destination}/MANIFEST.txt"
  echo "${destination}"
}

configure_pure_ftpd() {
  validate_ftp_parameters || { echo "Parametros FTP invalidos." >&2; return 1; }
  # O instalador pode ser executado diretamente como root em imagens mínimas.
  # Prepare o diretório/arquivo sudoers antes de qualquer configuração FTP.
  if ! command -v sudo >/dev/null 2>&1; then
    apt-get install -y sudo
  fi
  install -d -o root -g root -m 0755 /etc/sudoers.d
  systemctl stop pure-ftpd.service 2>/dev/null || true
  apt-get install -y pure-ftpd-common pure-ftpd
  # Alguns pacotes tentam iniciar o daemon durante a instalação.
  systemctl stop pure-ftpd.service 2>/dev/null || true
  local backup_dir
  backup_dir="$(backup_pure_ftpd)"
  if ! getent group backupftp >/dev/null; then groupadd --system backupftp; fi
  if ! id backupftp >/dev/null 2>&1; then useradd --system --gid backupftp --home-dir "${STORAGE_DIR}/ftp-incoming" --no-create-home --shell /usr/sbin/nologin backupftp; fi
  install -d -m 0750 -o root -g backupftp "${STORAGE_DIR}/ftp-incoming" "${STORAGE_DIR}/ftp-incoming/accounts"
  install -d -m 0755 /etc/pure-ftpd/conf /etc/pure-ftpd/auth
  local ftp_uid ftp_gid
  ftp_uid="$(id -u backupftp)"
  ftp_gid="$(id -g backupftp)"
  printf '%s\n' "${ftp_uid}" >/etc/pure-ftpd/conf/MinUID
  if [[ ! -e /etc/pure-ftpd/pureftpd.passwd ]]; then
    install -o root -g root -m 0600 /dev/null /etc/pure-ftpd/pureftpd.passwd
  fi
  if [[ ! -e /etc/pure-ftpd/pureftpd.pdb ]]; then
    pure-pw mkdb /etc/pure-ftpd/pureftpd.pdb -f /etc/pure-ftpd/pureftpd.passwd
    chown root:root /etc/pure-ftpd/pureftpd.pdb
    chmod 0600 /etc/pure-ftpd/pureftpd.pdb
  fi
  printf 'yes\n' >/etc/pure-ftpd/conf/NoAnonymous
  printf 'yes\n' >/etc/pure-ftpd/conf/ChrootEveryone
  printf 'no\n' >/etc/pure-ftpd/conf/AllowUserFXP
  printf 'no\n' >/etc/pure-ftpd/conf/AllowAnonymousFXP
  printf '%s %s\n' "${FTP_PASSIVE_START}" "${FTP_PASSIVE_END}" >/etc/pure-ftpd/conf/PassivePortRange
  printf '50\n' >/etc/pure-ftpd/conf/MaxClientsNumber
  printf '5\n' >/etc/pure-ftpd/conf/MaxClientsPerIP
  printf ',%s\n' "${FTP_CONTROL_PORT}" >/etc/pure-ftpd/conf/Bind
  printf '/etc/pure-ftpd/pureftpd.pdb\n' >/etc/pure-ftpd/conf/PureDB
  [[ -z "${FTP_PUBLIC_IP}" ]] || printf '%s\n' "${FTP_PUBLIC_IP}" >/etc/pure-ftpd/conf/ForcePassiveIP
  ln -sfn ../conf/PureDB /etc/pure-ftpd/auth/50pure
  if (( ENABLE_FTPS )); then
    [[ -n "${FTP_TLS_DOMAIN}" && -r "/etc/letsencrypt/live/${FTP_TLS_DOMAIN}/fullchain.pem" && -r "/etc/letsencrypt/live/${FTP_TLS_DOMAIN}/privkey.pem" ]] || { echo "Certificado Let's Encrypt do dominio FTP nao encontrado." >&2; return 1; }
    install -d -m 0700 /etc/ssl/private
    { sed -n '/BEGIN CERTIFICATE/,/END CERTIFICATE/p' "/etc/letsencrypt/live/${FTP_TLS_DOMAIN}/fullchain.pem"; sed -n '/BEGIN.*PRIVATE KEY/,/END.*PRIVATE KEY/p' "/etc/letsencrypt/live/${FTP_TLS_DOMAIN}/privkey.pem"; } >/etc/ssl/private/pure-ftpd.pem
    chmod 0600 /etc/ssl/private/pure-ftpd.pem
    printf '1\n' >/etc/pure-ftpd/conf/TLS
  else
    printf '0\n' >/etc/pure-ftpd/conf/TLS
  fi
  install -m 0750 -o root -g root "${APP_DIR}/scripts/ftp-admin-helper.py" /usr/local/sbin/backup-manager-ftp-admin
  install -m 0440 -o root -g root "${APP_DIR}/deploy/sudoers/backup-manager-ftp" /etc/sudoers.d/backup-manager-ftp
  visudo -cf /etc/sudoers.d/backup-manager-ftp >/dev/null || { rm -f /etc/sudoers.d/backup-manager-ftp; cp -a "${backup_dir}/pure-ftpd/." /etc/pure-ftpd/ 2>/dev/null || true; return 1; }
  python3 - "${FTP_CONTROL_PORT}" "${FTP_PASSIVE_START}" "${FTP_PASSIVE_END}" "${FTP_PUBLIC_IP}" "${FTP_PUBLIC_IPV6}" "${ENABLE_FTPS}" <<'PY'
import sqlite3, sys
db=sqlite3.connect('/opt/backup-manager-local/data/backup_manager.sqlite3')
for key,value in zip(('ftp_control_port','ftp_passive_start','ftp_passive_end','ftp_public_ip','ftp_public_ipv6','ftp_tls_enabled'),sys.argv[1:]):
    db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value))
db.execute("INSERT INTO settings(key,value) VALUES('ftp_enabled','1') ON CONFLICT(key) DO UPDATE SET value='1'")
db.commit()
PY
  visudo -cf /etc/sudoers.d/backup-manager-ftp >/dev/null || { systemctl stop pure-ftpd.service || true; return 1; }
  systemctl enable pure-ftpd.service
  if ! systemctl restart pure-ftpd.service || ! systemctl is-active --quiet pure-ftpd.service; then
    cp -a "${backup_dir}/pure-ftpd/." /etc/pure-ftpd/ 2>/dev/null || true
    systemctl restart pure-ftpd.service || true
    echo "Pure-FTPd nao iniciou; configuracao anterior restaurada." >&2
    return 1
  fi
}

if [[ "${EUID}" -ne 0 ]]; then
  echo "Execute como root." >&2
  exit 1
fi

cd "${APP_DIR}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 nao encontrado." >&2
  exit 1
fi

if ! command -v nginx >/dev/null 2>&1; then
  apt-get update
  apt-get install -y nginx
fi

apt-get update
apt-get install -y python3-paramiko python3-cryptography python3-maxminddb rclone
install -d -o root -g root -m 0750 "${SECRET_DIR}"
if [[ ! -e "${SECRET_DIR}/rclone.conf" ]]; then
  install -o root -g root -m 0600 /dev/null "${SECRET_DIR}/rclone.conf"
fi

validate_letsencrypt

mkdir -p "${APP_DIR}/data" /etc/nginx/sites-available /etc/nginx/sites-enabled "${SECRET_DIR}"
mkdir -p "${STORAGE_DIR}/backups" "${STORAGE_DIR}/trash" "${STORAGE_DIR}/quarantine" "${STORAGE_DIR}/temporary"
chmod 750 "${APP_DIR}/data"
chmod 750 "${STORAGE_DIR}" "${STORAGE_DIR}/backups" "${STORAGE_DIR}/trash" "${STORAGE_DIR}/quarantine" "${STORAGE_DIR}/temporary"
chmod 750 "${SECRET_DIR}"

if [[ ! -f "${SECRET_KEY}" ]]; then
  python3 - <<'PY'
from pathlib import Path
from cryptography.fernet import Fernet

path = Path("/etc/backup-manager-local/secret.key")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(Fernet.generate_key() + b"\n")
path.chmod(0o640)
PY
fi
chmod 640 "${SECRET_KEY}"

SCHEMA_VERSION="$(python3 -m backup_manager.cli schema-release-version)"
python3 -m backup_manager.cli migrate --to-version "${SCHEMA_VERSION}"
python3 -m backup_manager.cli integrity-check

if (( ENABLE_PURE_FTPD )); then configure_pure_ftpd; fi

while read -r profile unit; do
  [[ -z "${profile}" || "${profile}" == \#* ]] && continue
  [[ "${profile}" == "both" || "${profile}" == "install" ]] || continue
  [[ "${unit}" =~ ^backup-manager-[a-z0-9-]+\.(service|timer)$ ]] || { echo "Unidade inválida no manifesto: ${unit}" >&2; exit 1; }
  install -m 0644 "${APP_DIR}/deploy/systemd/${unit}" "/etc/systemd/system/${unit}"
done <"${APP_DIR}/deploy/systemd/units.manifest"
if ! id backup-manager-telegram >/dev/null 2>&1; then useradd --system --gid root --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin backup-manager-telegram; fi
chmod 0770 "${STORAGE_DIR}/temporary"
chmod 0770 "${APP_DIR}/data"
chown backup-manager-telegram:root "${APP_DIR}/data/backup_manager.sqlite3"
chmod 0640 "${APP_DIR}/data/backup_manager.sqlite3"
NGINX_CANDIDATE="$(mktemp)"
trap 'rm -f "${NGINX_CANDIDATE:-}"' EXIT
render_nginx_config "${NGINX_CANDIDATE}"
detect_duplicate_https_blocks "${NGINX_CANDIDATE}"
backup_and_install_nginx_config "${NGINX_CANDIDATE}"

rm -f /etc/nginx/sites-enabled/default
ln -sfn "/etc/nginx/sites-available/${NGINX_SITE}.conf" "/etc/nginx/sites-enabled/${NGINX_SITE}.conf"

if ! nginx -t; then
  restore_nginx_config
  echo "Configuracao Nginx invalida; configuracao anterior restaurada." >&2
  exit 1
fi
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.service"
systemctl enable --now "${WORKER_NAME}.timer"
systemctl enable --now backup-manager-lifecycle.timer
systemctl enable --now backup-manager-observability.timer
systemctl enable --now backup-manager-database-guard.timer
systemctl enable --now "${CLOUD_SYNC_NAME}.timer"
systemctl enable --now "${TELEGRAM_BACKUP_NAME}.timer"
if (( ENABLE_PURE_FTPD )); then systemctl enable --now backup-manager-ftp-importer.timer; fi
systemctl enable --now nginx.service
systemctl restart "${SERVICE_NAME}.service"
systemctl restart "${WORKER_NAME}.timer"
systemctl status "${WORKER_NAME}.timer" --no-pager >/dev/null
reload_and_validate_nginx

if (( ENABLE_PURE_FTPD && ENABLE_UFW )); then
  ufw allow "${FTP_CONTROL_PORT}/tcp" comment 'Backup Manager FTP control'
  ufw allow "${FTP_PASSIVE_START}:${FTP_PASSIVE_END}/tcp" comment 'Backup Manager FTP passive'
fi

echo "Backup Manager Local instalado."
echo "URL: http://$(hostname -I | awk '{print $1}')/"
echo "Admin inicial: admin / $(python3 -m backup_manager.cli temp-password)"
