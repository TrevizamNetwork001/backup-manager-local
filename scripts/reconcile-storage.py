#!/usr/bin/python3
"""Reconciliação conservadora: preserva arquivos e histórico; nunca exclui."""
import argparse, hashlib, os, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path

DB=Path(os.environ.get("BACKUP_MANAGER_DB","/opt/backup-manager-local/data/backup_manager.sqlite3"))
BACKUPS=Path("/var/lib/backup-manager-local/backups")
QUARANTINE=Path("/var/lib/backup-manager-local/quarantine")

def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--apply",action="store_true"); args=parser.parse_args()
    conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row; conn.execute("PRAGMA foreign_keys=ON")
    registered={str((BACKUPS/r["relative_path"]).resolve()) for r in conn.execute("SELECT relative_path FROM backups WHERE relative_path IS NOT NULL AND backup_status IN ('available','quarantined')")}
    missing=[]
    for r in conn.execute("SELECT * FROM backups WHERE backup_status IN ('available','quarantined')"):
        if not (BACKUPS/r["relative_path"]).is_file(): missing.append(r)
    orphans=[p for p in BACKUPS.rglob("*") if p.is_file() and str(p.resolve()) not in registered]
    print(f"missing={len(missing)} orphan={len(orphans)} apply={int(args.apply)}")
    if not args.apply: return
    now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for r in missing:
        note=(r["notes"]+" | " if r["notes"] else "")+"Arquivo físico ausente; reconciliado em 2026-08-23."
        conn.execute("UPDATE backups SET backup_status='failed',notes=? WHERE id=?",(note,r["id"]))
    for path in orphans:
        rel=path.relative_to(BACKUPS)
        parts=rel.parts
        if len(parts)>=5 and parts[0]=="equipment" and parts[1].isdigit():
            equipment_id=int(parts[1]); backup_uuid=path.stem if len(path.stem)==36 else str(uuid.uuid4())
            if not conn.execute("SELECT 1 FROM equipment WHERE id=?",(equipment_id,)).fetchone():
                destination=QUARANTINE/"unassigned"/rel
            else:
                value=digest(path)
                conn.execute("""INSERT INTO backups(uuid,equipment_id,original_filename,stored_filename,relative_path,file_size,sha256,
                              source_method,backup_reason,backup_status,received_at,notes)
                              VALUES(?,?,?,?,?,?,?,'system','unknown','quarantined',?,'Arquivo órfão preservado pela reconciliação; revise antes de disponibilizar.')""",
                             (backup_uuid,equipment_id,path.name,path.name,str(rel),path.stat().st_size,value,now))
                continue
        else: destination=QUARANTINE/"storage-reconciliation-20260823"/rel
        destination.parent.mkdir(parents=True,exist_ok=True); os.replace(path,destination)
    conn.execute("""INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address)
                    VALUES(NULL,'storage.reconciled','storage','',?,'')""",(f"missing_marked={len(missing)} orphans_preserved={len(orphans)}",))
    conn.commit(); conn.close()
    print("RECONCILE_OK")
if __name__=="__main__": main()
