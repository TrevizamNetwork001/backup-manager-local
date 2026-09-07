from __future__ import annotations

from .equipment_dependencies import equipment_dependencies


def equipment_page_context(conn) -> dict[str, object]:
    equipment = conn.execute(
        """
        SELECT equipment.*, vendors.name AS vendor, equipment_groups.name AS group_name,
               pops.name AS pop, environments.name AS environment
        FROM equipment
        LEFT JOIN vendors ON vendors.id = equipment.vendor_id
        LEFT JOIN equipment_groups ON equipment_groups.id = equipment.group_id
        LEFT JOIN pops ON pops.id = equipment.pop_id
        LEFT JOIN environments ON environments.id = equipment.environment_id
        ORDER BY hostname
        """
    ).fetchall()
    vendors = conn.execute("SELECT * FROM vendors ORDER BY name").fetchall()
    groups = conn.execute("SELECT * FROM equipment_groups ORDER BY name").fetchall()
    pops = conn.execute("SELECT * FROM pops ORDER BY name").fetchall()
    environments = conn.execute("SELECT * FROM environments ORDER BY name").fetchall()
    dependency_map = {item["id"]: equipment_dependencies(conn, item["id"]) for item in equipment}
    return {
        "equipment": equipment,
        "vendors": vendors,
        "groups": groups,
        "pops": pops,
        "environments": environments,
        "dependency_map": dependency_map,
    }

