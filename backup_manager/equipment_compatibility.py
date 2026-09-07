from __future__ import annotations

import unicodedata


DRIVER_VENDOR = {
    "mikrotik_routeros": "mikrotik",
    "huawei_vrp": "huawei",
    "huawei_router": "huawei",
    "huawei_olt_ssh_ftp": "huawei",
    "cisco_ios": "cisco",
    "zte_olt_ssh_ftp": "zte",
    "intelbras_gpon_ssh_ftp": "intelbras",
    "intelbras_epon_ssh_ftp": "intelbras",
    "vsol_olt_ssh_ftp": "vsol",
    "vsol_olt_telnet_cli": "vsol",
    "parks_olt_100_200_ssh_ftp": "parks",
    "parks_olt_300_400_ssh_ftp": "parks",
    "cdata_olt_ssh_ftp": "c-data",
    "fiberhome_olt_telnet_ftp": "fiberhome",
    "datacom_dmos_ssh": "datacom",
}

MIKROTIK_INCONSISTENCY = (
    "Operação MikroTik bloqueada: o fabricante e o driver persistidos não identificam "
    "simultaneamente um equipamento MikroTik RouterOS ativo. Revise o cadastro."
)


def vendor_key(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").strip())
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


def compatibility_error(vendor_name: str | None, driver: str) -> str:
    expected = DRIVER_VENDOR.get(str(driver or "").strip())
    if not expected:
        return ""
    actual = vendor_key(vendor_name)
    if actual == expected:
        return ""
    label = str(vendor_name or "não informado").strip() or "não informado"
    return f'O driver "{driver}" não é compatível com o fabricante "{label}".'


def is_strict_mikrotik(equipment, vendor_name: str | None = None, *, require_active: bool = False) -> bool:
    vendor = vendor_name if vendor_name is not None else equipment["vendor"]
    active = bool(equipment["is_active"]) if require_active else True
    return active and vendor_key(vendor) == "mikrotik" and equipment["ssh_backup_driver"] == "mikrotik_routeros"


def require_mikrotik_equipment(conn, equipment_id: int, *, require_active: bool = True):
    equipment = conn.execute(
        """SELECT equipment.*,vendors.name AS vendor FROM equipment
           LEFT JOIN vendors ON vendors.id=equipment.vendor_id WHERE equipment.id=?""",
        (equipment_id,),
    ).fetchone()
    if not equipment or not is_strict_mikrotik(equipment, equipment["vendor"], require_active=require_active):
        raise ValueError(MIKROTIK_INCONSISTENCY)
    return equipment


def require_mikrotik_integration(conn, integration_id: int, *, require_active: bool = True):
    integration = conn.execute(
        "SELECT * FROM mikrotik_ftp_integrations WHERE id=? AND is_active=1", (integration_id,)
    ).fetchone()
    if not integration:
        raise ValueError("Integração FTP Push inativa ou ausente.")
    equipment = require_mikrotik_equipment(
        conn, integration["equipment_id"], require_active=require_active
    )
    if integration["equipment_id"] != equipment["id"]:
        raise ValueError(MIKROTIK_INCONSISTENCY)
    return integration, equipment
