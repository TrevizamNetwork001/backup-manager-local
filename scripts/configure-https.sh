#!/usr/bin/env bash
set -euo pipefail

ACTION=""
DOMAIN=""
EMAIL=""
CONFIRM=0
DRY_RUN=0
APP_DIR="${BACKUP_MANAGER_HOME:-/opt/backup-manager-local}"
SITE="/etc/nginx/sites-available/backup-manager-local"
ENABLED="/etc/nginx/sites-enabled/backup-manager-local"
TEMPLATE="${APP_DIR}/deploy/nginx/backup-manager-local-letsencrypt.conf.template"

while (($#)); do
  case "$1" in
    --action) ACTION="${2:-}"; shift 2 ;;
    --domain|--letsencrypt-domain) DOMAIN="${2:-}"; shift 2 ;;
    --email) EMAIL="${2:-}"; shift 2 ;;
    --confirm) CONFIRM=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "Parâmetro desconhecido." >&2; exit 2 ;;
  esac
done

[[ "${ACTION}" == "issue" || "${ACTION}" == "renew" ]] || { echo "Ação HTTPS inválida." >&2; exit 2; }
[[ "${DOMAIN}" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || { echo "Domínio inválido." >&2; exit 2; }
[[ "${EMAIL}" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || { echo "E-mail inválido." >&2; exit 2; }
((CONFIRM)) || { echo "Confirmação explícita ausente; nenhuma configuração foi alterada." >&2; exit 3; }

if ((DRY_RUN)); then
  echo "DRY-RUN: ${ACTION} HTTPS para ${DOMAIN}; configuração existente seria preservada e validada."
  exit 0
fi

[[ "${EUID}" -eq 0 ]] || { echo "O helper HTTPS requer execução administrativa." >&2; exit 4; }
command -v certbot >/dev/null || { echo "Certbot não está instalado." >&2; exit 4; }
command -v nginx >/dev/null || { echo "Nginx não está instalado." >&2; exit 4; }
[[ -r "${TEMPLATE}" ]] || { echo "Template HTTPS não encontrado." >&2; exit 4; }

if [[ "${ACTION}" == "renew" ]]; then
  cert_path="/etc/letsencrypt/live/${DOMAIN}/cert.pem"
  before=""
  [[ -r "${cert_path}" ]] && before="$(sha256sum "${cert_path}" | cut -d' ' -f1)"
  if ! renew_output="$(certbot renew --cert-name "${DOMAIN}" --noninteractive --deploy-hook "nginx -t && systemctl reload nginx" 2>&1)"; then
    echo "Não foi possível verificar a renovação do certificado. Consulte os logs do Certbot." >&2
    exit 5
  fi
  after=""
  [[ -r "${cert_path}" ]] && after="$(sha256sum "${cert_path}" | cut -d' ' -f1)"
  if [[ -n "${before}" && -n "${after}" && "${before}" != "${after}" ]]; then
    echo "Certificado renovado com sucesso."
  else
    echo "Certificado válido. A renovação ainda não é necessária."
  fi
  exit 0
fi

timestamp="$(date -u +%Y%m%d%H%M%S)"
backup="${SITE}.before-https-${timestamp}"
candidate="$(mktemp --tmpdir=/etc/nginx/sites-available)"
cleanup() { rm -f "${candidate}"; }
trap cleanup EXIT

certbot certonly --nginx --domain "${DOMAIN}" --email "${EMAIL}" --agree-tos --noninteractive --keep-until-expiring
[[ -r "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" && -r "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" ]] || {
  echo "Certificado Let's Encrypt não encontrado após a emissão." >&2
  exit 5
}

sed "s/__DOMAIN__/${DOMAIN}/g" "${TEMPLATE}" >"${candidate}"
chmod 0644 "${candidate}"
if [[ -e "${SITE}" ]]; then
  cp -a "${SITE}" "${backup}"
fi
mv "${candidate}" "${SITE}"
ln -sfn "${SITE}" "${ENABLED}"

if ! nginx -t; then
  if [[ -e "${backup}" ]]; then
    cp -a "${backup}" "${SITE}"
  fi
  nginx -t >/dev/null 2>&1 || true
  echo "Configuração recusada; a versão anterior foi restaurada." >&2
  exit 6
fi

systemctl reload nginx
systemctl enable --now certbot.timer >/dev/null 2>&1 || true
echo "HTTPS instalado para ${DOMAIN}; cópia anterior preservada em ${backup}."
