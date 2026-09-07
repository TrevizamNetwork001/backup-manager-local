from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from pathlib import Path


COUNTRY_DB = Path(os.environ.get("BACKUP_MANAGER_GEOIP_COUNTRY_DB", "/usr/share/GeoIP/GeoLite2-Country.mmdb"))
ASN_DB = Path(os.environ.get("BACKUP_MANAGER_GEOIP_ASN_DB", "/usr/share/GeoIP/GeoLite2-ASN.mmdb"))


def _record(path: Path, address: str) -> dict:
    try:
        import maxminddb  # type: ignore[import-not-found]
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 512 * 1024 * 1024:
            return {}
        with maxminddb.open_database(str(path)) as reader:
            value = reader.get(address)
        return value if isinstance(value, dict) else {}
    except (ImportError, OSError, ValueError):
        return {}


@lru_cache(maxsize=4096)
def lookup_ip(address: str) -> dict[str, object]:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return {"country_code": "", "country_name": "Não identificado", "asn": None,
                "organization": "Não identificada", "available": False}
    if not parsed.is_global:
        return {"country_code": "", "country_name": "Rede local", "asn": None,
                "organization": "Rede privada", "available": COUNTRY_DB.is_file() or ASN_DB.is_file()}
    country = _record(COUNTRY_DB, str(parsed))
    asn = _record(ASN_DB, str(parsed))
    country_data = country.get("country") or country.get("registered_country") or {}
    names = country_data.get("names") or {}
    return {
        "country_code": str(country_data.get("iso_code") or ""),
        "country_name": str(names.get("pt-BR") or names.get("en") or "Não identificado"),
        "asn": asn.get("autonomous_system_number"),
        "organization": str(asn.get("autonomous_system_organization") or "Não identificada"),
        "available": COUNTRY_DB.is_file() or ASN_DB.is_file(),
    }


def database_status() -> dict[str, object]:
    try:
        import maxminddb  # type: ignore[import-not-found]  # noqa: F401
        reader = True
    except ImportError:
        reader = False
    return {"reader": reader, "country": COUNTRY_DB.is_file(), "asn": ASN_DB.is_file(),
            "country_path": str(COUNTRY_DB), "asn_path": str(ASN_DB)}
