from __future__ import annotations

import json


def handle_mikrotik_ftp_create_logic(
    *,
    conn,
    user,
    equipment_id: int,
    form: dict[str, str],
    previous_account,
    create_integration,
    mikrotik_integration_config,
    generate_install_script,
    run_helper,
    audit,
    redirect_with_message,
    mikrotik_script_response,
    equipment_detail_page,
    environ: dict,
):
    credential_action = form.get("credential_action", "isolated")
    destination = f"/equipment/{equipment_id}" + ("?onboarding=ftp-install" if form.get("onboarding") == "ftp" else "")
    retention = int(form["retention_days"]) if form.get("retention_days") else None
    integration, account, password = create_integration(
        conn, equipment_id=equipment_id, routeros_version=int(form.get("routeros_version", "0")),
        backup_format=form.get("backup_format", ""), schedule_mode=form.get("schedule_mode", ""),
        schedule_time=form.get("schedule_time", ""), ftp_host=form.get("ftp_host", ""),
        ftp_port=int(form.get("ftp_port", "0")), ftp_directory=form.get("ftp_directory", "/"),
        user_id=user["id"], retention_days=retention,
        credential_action=credential_action,
    )
    script = ""
    if password:
        config = mikrotik_integration_config(conn, integration, include_secret=True)
        script = generate_install_script(config, include_secret=True)
    audit(conn, user["id"], "mikrotik_ftp.created", "mikrotik_ftp_integration", integration["uuid"],
          json.dumps({"equipment_id": equipment_id, "routeros": integration["routeros_major_version"], "format": integration["backup_format"]}, separators=(",", ":")), environ.get("REMOTE_ADDR", ""))
    if credential_action == "rotate":
        audit(conn, user["id"], "mikrotik_ftp.credential_rotated", "ftp_account", account["uuid"], "{}", environ.get("REMOTE_ADDR", ""))
    account_id, account_uuid = account["id"], account["uuid"]
    if password:
        ok, detail = run_helper("create-or-update-account", account_id, password)
        if ok:
            conn.execute("UPDATE ftp_accounts SET sync_status='synced',sync_error='' WHERE id=?", (account_id,))
        else:
            conn.execute("UPDATE mikrotik_ftp_integrations SET is_active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?", (integration["id"],))
            if previous_account:
                conn.execute("""UPDATE ftp_accounts SET password_hash_or_secret_reference=?,is_active=?,sync_status=?,
                        sync_error=?,deleted_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (previous_account["password_hash_or_secret_reference"], previous_account["is_active"],
                         previous_account["sync_status"], previous_account["sync_error"], previous_account["deleted_at"], account_id))
            else:
                conn.execute("UPDATE ftp_accounts SET is_active=0,deleted_at=CURRENT_TIMESTAMP,sync_status='failed',sync_error=? WHERE id=?", (detail, account_id))
        audit(conn, user["id"], "mikrotik_ftp.credential_provisioned" if ok else "mikrotik_ftp.provision_failed", "ftp_account", account_uuid, json.dumps({"status": "ok" if ok else "failed", "error": "" if ok else detail}), environ.get("REMOTE_ADDR", ""))
        conn.commit()
        if not ok:
            return redirect_with_message(f"/equipment/{equipment_id}", "error", "A sincronização com o Pure-FTPd falhou; a criação da integração foi revertida.")
        return redirect_with_message(
            destination, "success",
            "FTP Push configurado. Continue pela Instalação guiada via SSH.",
        )
    return redirect_with_message(
        destination, "success",
        "Conta FTP existente vinculada. Continue pela Instalação guiada; se a senha não estiver disponível, rotacione a credencial.",
    )
