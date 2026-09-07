from __future__ import annotations

import re
import hashlib
import unicodedata
from datetime import datetime
from dataclasses import dataclass

from . import mikrotik_routeros_v6, mikrotik_routeros_v7

FORMATS = {"backup", "rsc", "both"}
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
SCRIPT_VERSION = "5.8"
EXECUTION_NAMESPACE_RE = re.compile(r"^[a-f0-9]{12}$")


class ScriptError(ValueError):
    pass


def normalize_clock_date(value: str) -> str:
    """Reference implementation for RouterOS date-format snapshots."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ScriptError("Data do relógio RouterOS inválida.") from None
        return value
    match = re.fullmatch(r"([a-zA-Z]{3})/(\d{1,2})/(\d{4})", value)
    if not match:
        raise ScriptError("Formato de data do relógio RouterOS não reconhecido.")
    months = {month: index for index, month in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
    month = months.get(match.group(1).lower())
    if month is None:
        raise ScriptError("Mês do relógio RouterOS não reconhecido.")
    normalized = f"{match.group(3)}-{month:02d}-{int(match.group(2)):02d}"
    try:
        datetime.strptime(normalized, "%Y-%m-%d")
    except ValueError:
        raise ScriptError("Data do relógio RouterOS inválida.") from None
    return normalized


@dataclass(frozen=True)
class ScriptConfig:
    device_id: int
    device_name: str
    routeros_version: int
    backup_format: str
    schedule_mode: str
    schedule_frequency: str
    schedule_time: str
    ftp_host: str
    ftp_port: int
    ftp_username: str
    ftp_password: str
    ftp_directory: str = "/"
    execution_namespace: str = ""


def sanitize_identity(value: str, equipment_id: int) -> str:
    ascii_value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii").lower()
    clean = re.sub(r"[^a-z0-9._-]+", "-", ascii_value)
    while ".." in clean:
        clean = clean.replace("..", ".")
    while "--" in clean:
        clean = clean.replace("--", "-")
    clean = clean.strip(".-")[:48].rstrip(".-")
    return clean or f"mikrotik-{equipment_id}"


def sanitize_device_name(value: str) -> str:
    return sanitize_identity(value, 0)


def routeros_quote(value: str) -> str:
    if any(char in value for char in "\r\n\x00"):
        raise ScriptError("Valor contém caractere não permitido.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def routeros_source_quote(value: str) -> str:
    """Quote a multiline script body for a RouterOS source property."""
    if "\x00" in value:
        raise ScriptError("Script contém caractere não permitido.")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return '"' + escaped.replace("\r\n", "\\r\\n").replace("\r", "\\r\\n").replace("\n", "\\r\\n") + '"'


def validate_config(config: ScriptConfig) -> None:
    if config.device_id < 1 or config.routeros_version not in {6, 7}:
        raise ScriptError("Equipamento ou versão RouterOS inválida.")
    if config.backup_format not in FORMATS:
        raise ScriptError("Formato de backup inválido.")
    if config.schedule_mode not in {"manual", "scheduled"} or config.schedule_frequency != "daily":
        raise ScriptError("Agendamento inválido.")
    if not TIME_RE.fullmatch(config.schedule_time):
        raise ScriptError("Horário inválido.")
    if (not isinstance(config.ftp_port, int) or isinstance(config.ftp_port, bool)
            or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", config.ftp_host.strip())
            or not 1 <= config.ftp_port <= 65535):
        raise ScriptError("Servidor FTP inválido.")
    if not config.device_name.strip() or not config.ftp_username.strip():
        raise ScriptError("Nome do equipamento e usuário FTP são obrigatórios.")
    for value in (config.ftp_host, config.ftp_username, config.ftp_password, config.ftp_directory):
        routeros_quote(value)
    if config.execution_namespace and not EXECUTION_NAMESPACE_RE.fullmatch(config.execution_namespace):
        raise ScriptError("Namespace de execução inválido.")


def execution_namespace(config: ScriptConfig) -> str:
    if config.execution_namespace:
        return config.execution_namespace
    material = f"{config.device_id}:{config.ftp_username}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:12]


def config_fingerprint(config: ScriptConfig) -> str:
    """Identify non-secret values embedded in the managed RouterOS script."""
    material = "\0".join((
        str(config.device_id),
        sanitize_identity(config.device_name, config.device_id),
        str(config.routeros_version),
        config.backup_format,
        config.ftp_host.strip().lower(),
        str(config.ftp_port),
        config.ftp_username,
        config.ftp_directory.strip("/"),
    )).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


def build_execution_id(namespace: str, started_at: datetime, sequence: int) -> str:
    """Reference format used by RouterOS; sequence separates same-second runs."""
    if not EXECUTION_NAMESPACE_RE.fullmatch(namespace) or not 1 <= sequence <= 9_999_999_999:
        raise ScriptError("Identificador de execução inválido.")
    return f"x{namespace}-{started_at.strftime('%Y%m%d%H%M%S')}-{sequence}"


def _template(version: int):
    return mikrotik_routeros_v6 if version == 6 else mikrotik_routeros_v7


def _date_normalizer() -> list[str]:
    # RouterOS 7.10+ returns ISO; older releases traditionally use mon/D/YYYY.
    # The checks are explicit so we do not depend on :find / nil comparisons.
    return [
        ':local bmDateOriginal [/system clock get date]',
        ':log info ("backup-manager: data original: " . $bmDateOriginal)',
        ':local bmDateNormalized ""',
        ':local bmDateLen [:len $bmDateOriginal]',
        ':if (($bmDateLen = 10) and ([:pick $bmDateOriginal 4 5] = "-") and ([:pick $bmDateOriginal 7 8] = "-")) do={',
        '  :set bmDateNormalized $bmDateOriginal',
        '} else={',
        '  :if (($bmDateLen < 9) or ($bmDateLen > 11) or ([:pick $bmDateOriginal 3 4] != "/")) do={ :error "backup-manager: unsupported clock date format" }',
        '  :local bmMon [:pick $bmDateOriginal 0 3]',
        '  :local bmLastSlash -1',
        '  :for bmI from=4 to=8 do={ :if ([:pick $bmDateOriginal $bmI ($bmI + 1)] = "/") do={ :set bmLastSlash $bmI } }',
        '  :if (([:typeof $bmLastSlash] != "num") or ($bmLastSlash < 5) or (($bmDateLen - $bmLastSlash) != 5)) do={ :error "backup-manager: unsupported clock date format" }',
        '  :local bmDay [:pick $bmDateOriginal 4 $bmLastSlash]',
        '  :if (([:len $bmDay] < 1) or ([:len $bmDay] > 2)) do={ :error "backup-manager: invalid clock day" }',
        '  :if ([:len $bmDay] = 1) do={ :set bmDay ("0" . $bmDay) }',
        '  :local bmYear [:pick $bmDateOriginal ($bmLastSlash + 1) $bmDateLen]',
        '  :if ([:len $bmYear] != 4) do={ :error "backup-manager: invalid clock year" }',
        '  :local bmMonths "jan01feb02mar03apr04may05jun06jul07aug08sep09oct10nov11dec12"',
        '  :local bmPos [:find $bmMonths $bmMon]',
        '  :if (([:typeof $bmPos] != "num") or (($bmPos % 5) != 0)) do={ :error "backup-manager: unknown clock month" }',
        '  :set bmDateNormalized ($bmYear . "-" . [:pick $bmMonths ($bmPos + 3) ($bmPos + 5)] . "-" . $bmDay)',
        '}',
        ':if (([:len $bmDateNormalized] != 10) or ([:pick $bmDateNormalized 4 5] != "-") or ([:pick $bmDateNormalized 7 8] != "-")) do={ :error "backup-manager: invalid normalized date" }',
        ':log info ("backup-manager: data normalizada: " . $bmDateNormalized)',
        ':local bmTime [/system clock get time]',
        ':local bmStamp ($bmDateNormalized . "." . [:pick $bmTime 0 2] . [:pick $bmTime 3 5] . [:pick $bmTime 6 8])',
    ]


def _main_source(config: ScriptConfig, *, include_secret: bool) -> str:
    template = _template(config.routeros_version)
    password = config.ftp_password if include_secret else "ROTATE-CREDENTIAL-TO-GENERATE-DEPLOYABLE-SCRIPT"
    directory = config.ftp_directory.strip("/")
    safe_identity = sanitize_identity(config.device_name, config.device_id)
    make_backup = config.backup_format in {"backup", "both"}
    make_rsc = config.backup_format in {"rsc", "both"}
    lines = [
        template.HEADER,
        f"# backup-manager-version={SCRIPT_VERSION} device={config.device_id}",
        f"# backup-manager-config={config_fingerprint(config)}",
        ':global backupManagerFtpRunning',
        ':global backupManagerFtpSequence',
        ':global backupManagerFtpLastBase',
        ':if ($backupManagerFtpRunning = true) do={ :error "backup-manager already running" }',
        ':set backupManagerFtpRunning true',
        ':if ([:typeof $backupManagerFtpSequence] != "num") do={ :set backupManagerFtpSequence 0 }',
        ':set backupManagerFtpSequence ($backupManagerFtpSequence + 1)',
        ':if ($backupManagerFtpSequence > 9999999999) do={ :set backupManagerFtpSequence 1 }',
        f":local bmHost {routeros_quote(config.ftp_host)}",
        f":local bmPort {config.ftp_port}",
        f":local bmUser {routeros_quote(config.ftp_username)}",
        f":local bmPassword {routeros_quote(password)}",
        f":local bmRemoteDir {routeros_quote(directory)}",
        ':local bmBackup ""',
        ':local bmRsc ""',
        ':do {',
        '  :log info "backup-manager: iniciando rotina"',
        '  :local bmIdentityRaw [/system identity get name]',
        '  :log info ("backup-manager: identity original: " . $bmIdentityRaw)',
        f'  :local bmSafe {routeros_quote(safe_identity)}',
        '  :log info ("backup-manager: identity configurada: " . $bmSafe)',
        *["  " + line for line in _date_normalizer()],
        '  :local bmBase ($bmSafe . "." . $bmDateNormalized . "-" . [:pick $bmTime 0 2] . "-" . [:pick $bmTime 3 5] . "-" . [:pick $bmTime 6 8] . "." . $backupManagerFtpSequence)',
        '  :set backupManagerFtpLastBase $bmBase',
        '  :log info ("backup-manager: nome-base gerado: " . $bmBase)',
        '  :set bmBackup ($bmBase . ".backup")',
        '  :set bmRsc ($bmBase . ".rsc")',
        '  :log info "backup-manager: criando arquivos"',
        *(['  /system backup save name=$bmBase'] if make_backup else []),
        *(['  ' + template.export_command("bmBase")] if make_rsc else []),
        '  :delay 5s',
        *(['  :local bmBackupIds [/file find where name=$bmBackup]',
           '  :if ([:len $bmBackupIds] != 1) do={ :error "backup file unavailable" }',
           '  :if ([/file get $bmBackupIds size] <= 0) do={ :error "backup file empty" }'] if make_backup else ['  :local bmBackupIds ""']),
        *(['  :local bmRscIds [/file find where name=$bmRsc]',
           '  :if ([:len $bmRscIds] != 1) do={ :error "export file unavailable" }',
           '  :if ([/file get $bmRscIds size] <= 0) do={ :error "export file empty" }'] if make_rsc else ['  :local bmRscIds ""']),
        '  :log info ("backup-manager: arquivos localizados backup=" . [:len $bmBackupIds] . " export=" . [:len $bmRscIds])',
        '  :local bmRemoteBackup $bmBackup',
        '  :local bmRemoteRsc $bmRsc',
        '  :if ([:len $bmRemoteDir] > 0) do={',
        '    :set bmRemoteBackup ($bmRemoteDir . "/" . $bmBackup)',
        '    :set bmRemoteRsc ($bmRemoteDir . "/" . $bmRsc)',
        '  }',
        *(['  :log info ("backup-manager: upload do backup iniciado: " . $bmBackup)',
           '  ' + template.upload_command("bmBackup", "bmRemoteBackup"),
           '  :log info "backup-manager: upload do backup concluido"'] if make_backup else []),
        *(['  :log info ("backup-manager: upload do export iniciado: " . $bmRsc)',
           '  ' + template.upload_command("bmRsc", "bmRemoteRsc"),
           '  :log info "backup-manager: upload do export concluido"'] if make_rsc else []),
        '  :log info "backup-manager: limpeza local iniciada"',
        '  :do {',
        '    :foreach bmFileId in=$bmBackupIds do={ /file remove $bmFileId }',
        '    :foreach bmFileId in=$bmRscIds do={ /file remove $bmFileId }',
        '  } on-error={',
        '    :log warning ("backup-manager: falha na limpeza local: " . $message)',
        '  }',
        '  :log info "backup-manager: limpeza local concluida"',
        '  :log info "backup-manager: rotina concluida"',
        '} on-error={',
        '  :local bmOriginalError $message',
        '  :log error ("backup-manager: falha original: " . $bmOriginalError)',
        '  :local bmCleanupBackup [/file find where name=$bmBackup]',
        '  :local bmCleanupRsc [/file find where name=$bmRsc]',
        '  :log info ("backup-manager: limpeza de erro iniciada backup=" . [:len $bmCleanupBackup] . " export=" . [:len $bmCleanupRsc])',
        '  :do {',
        '    :foreach bmFileId in=$bmCleanupBackup do={ /file remove $bmFileId }',
        '    :foreach bmFileId in=$bmCleanupRsc do={ /file remove $bmFileId }',
        '  } on-error={',
        '    :log warning ("backup-manager: falha na limpeza de erro: " . $message)',
        '  }',
        '  :log info "backup-manager: limpeza de erro concluida"',
        '  :set bmPassword ""',
        '  :set backupManagerFtpRunning false',
        '  :error ("backup-manager: falha na rotina: " . $bmOriginalError)',
        '}',
        ':set bmPassword ""',
        ':set backupManagerFtpRunning false',
    ]
    return "\n".join(lines)


def generate_install_script(config: ScriptConfig, *, include_secret: bool = True) -> str:
    validate_config(config)
    source = _main_source(config, include_secret=include_secret)
    lines = [
        _template(config.routeros_version).HEADER,
        ':local bmOldScripts [/system script find where name="backup-manager"]',
        ':if ([:len $bmOldScripts] > 0) do={ /system script remove $bmOldScripts }',
        f'/system script add name="backup-manager" policy=ftp,read,write,policy,test,sensitive source={routeros_source_quote(source)}',
        ':local bmOldSchedulers [/system scheduler find where name="backup-manager"]',
        ':if ([:len $bmOldSchedulers] > 0) do={ /system scheduler remove $bmOldSchedulers }',
    ]
    if config.schedule_mode == "scheduled":
        lines.append(
            f'/system scheduler add name="backup-manager" interval=1d start-time={config.schedule_time}:00 '
            'on-event="/system script run backup-manager" disabled=no policy=ftp,read,write,policy,test,sensitive'
        )
    else:
        lines.append(
            f'/system scheduler add name="backup-manager" interval=1d start-time={config.schedule_time}:00 '
            'on-event="/system script run backup-manager" disabled=yes policy=ftp,read,write,policy,test,sensitive'
        )
    return "\n".join(lines)


def _test_source(config: ScriptConfig, *, include_secret: bool) -> str:
    password = config.ftp_password if include_secret else "ROTATE-CREDENTIAL-TO-GENERATE-DEPLOYABLE-SCRIPT"
    template = _template(config.routeros_version)
    directory = config.ftp_directory.strip("/")
    lines = [
        template.HEADER + " - temporary integration test",
        f':local bmHost {routeros_quote(config.ftp_host)}',
        f':local bmPort {config.ftp_port}',
        f':local bmUser {routeros_quote(config.ftp_username)}',
        f':local bmPassword {routeros_quote(password)}',
        f':local bmBase {routeros_quote(f"backup-manager-test-{config.device_id}")}',
        ':local bmFile ($bmBase . ".rsc")',
        ':local bmRemote $bmFile',
    ]
    if directory:
        lines.append(f':set bmRemote ({routeros_quote(directory + "/")} . $bmFile)')
    lines.append(':do {')
    # /export file= is available throughout supported RouterOS 6 and 7. Avoid
    # /file add, which is unavailable on early RouterOS 7 releases.
    lines.append('  ' + template.export_command("bmBase"))
    lines.extend([
        '  :delay 2s',
        '  :local bmFileIds [/file find where name=$bmFile]',
        '  :if ([:len $bmFileIds] != 1) do={ :error "test file unavailable" }',
        '  :if ([/file get $bmFileIds size] <= 0) do={ :error "test file empty" }',
        '  ' + template.upload_command("bmFile", "bmRemote"),
        '  /file remove $bmFileIds',
        '} on-error={',
        '  :local bmOriginalError $message',
        '  :local bmCleanup [/file find where name=$bmFile]',
        '  :if ([:len $bmCleanup] > 0) do={ /file remove $bmCleanup }',
        '  :set bmPassword ""',
        '  :error ("backup-manager ftp test failed: " . $bmOriginalError)',
        '}',
        ':set bmPassword ""',
    ])
    return "\n".join(lines)


def generate_test_import(config: ScriptConfig) -> str:
    validate_config(config)
    return "\n".join([
        _template(config.routeros_version).HEADER + " - integration test",
        _test_source(config, include_secret=True),
    ])


def generate_test_script(config: ScriptConfig, *, include_secret: bool = False) -> str:
    validate_config(config)
    if not include_secret:
        raise ScriptError("O teste FTP separado requer uma credencial disponível.")
    return generate_test_import(config)


def generate_removal_script(version: int) -> str:
    if version not in {6, 7}:
        raise ScriptError("Versão RouterOS inválida.")
    return "\n".join([
        _template(version).HEADER + " - removal",
        '/system scheduler remove [find where name="backup-manager"]',
        '/system script remove [find where name="backup-manager"]',
        ':log info "backup-manager-ftp objects removed"',
    ])
