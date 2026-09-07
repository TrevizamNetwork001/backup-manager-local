from __future__ import annotations


def handle_equipment_run_now_logic(
    *,
    connect,
    equipment_id: int,
    user,
    environ: dict,
    form: dict[str, str],
    create_job,
    queue_run,
    equipment_redirect,
    queue_flash,
    get_setting,
    redirect,
):
    method = form.get("method", "dry_run")
    if method not in ("dry_run", "ssh"):
        method = "dry_run"
    error = ""
    queued = None
    conn = connect()
    try:
        job = conn.execute(
            """
            SELECT backup_jobs.*
            FROM backup_jobs JOIN equipment ON equipment.id = backup_jobs.equipment_id
            WHERE backup_jobs.equipment_id = ? AND backup_jobs.status = 'active' AND equipment.is_active = 1
              AND backup_jobs.method = ?
            ORDER BY backup_jobs.id DESC LIMIT 1
            """,
            (equipment_id, method),
        ).fetchone()
        if not job:
            try:
                job = create_job(conn, equipment_id, user["id"], "manual", method, False, "none", "", "", get_setting(conn, "timezone", "America/Sao_Paulo"), "")
            except ValueError as exc:
                error = f"Não foi possível preparar job: {exc}"
                conn.rollback()
        try:
            if not error:
                queued = queue_run(conn, job["id"], "manual", user["id"])
                if queued is None:
                    error = "Já existe uma execução de backup em andamento ou enfileirada."
                    conn.rollback()
                elif method == "ssh":
                    queue_flash(conn, environ, "info", "Backup SSH enfileirado com sucesso.")
                conn.commit()
        except ValueError as exc:
            error = f"Não foi possível enfileirar: {exc}"
            conn.rollback()
    finally:
        conn.close()
    if error:
        if method == "ssh":
            with connect() as flash_conn:
                queue_flash(flash_conn, environ, "error", error)
            return redirect(f"/equipment/{equipment_id}")
        return equipment_redirect(equipment_id, "error", error)
    if method == "ssh" and queued is not None:
        return redirect(f"/equipment/{equipment_id}?run={queued['uuid']}")
    success = "Backup SSH enfileirado." if method == "ssh" else "Execução enfileirada."
    return equipment_redirect(equipment_id, "success", success)
