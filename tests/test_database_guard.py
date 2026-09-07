import os, sqlite3, subprocess, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class DatabaseGuardTest(unittest.TestCase):
    def test_backup_and_recover_preserve_corrupt_evidence(self):
        with tempfile.TemporaryDirectory() as raw:
            db=Path(raw)/"backup_manager.sqlite3"
            conn=sqlite3.connect(db); conn.execute("CREATE TABLE sample(value TEXT)"); conn.execute("INSERT INTO sample VALUES('ok')"); conn.commit(); conn.close()
            env={**os.environ,"BACKUP_MANAGER_DB":str(db),"BACKUP_MANAGER_GUARD_NO_SYSTEMD":"1"}
            made=subprocess.run(["python3",str(ROOT/"scripts/database-guard.py")],env=env,text=True,capture_output=True)
            self.assertEqual(made.returncode,0,made.stderr); self.assertIn("BACKUP_OK",made.stdout)
            db.write_bytes(b"corrupt")
            restored=subprocess.run(["python3",str(ROOT/"scripts/database-guard.py"),"--recover"],env=env,text=True,capture_output=True)
            self.assertEqual(restored.returncode,0,restored.stderr); self.assertIn("RECOVERY_OK",restored.stdout)
            conn=sqlite3.connect(db); self.assertEqual(conn.execute("SELECT value FROM sample").fetchone()[0],"ok"); conn.close()
            self.assertTrue(list(Path(raw).glob("*.corrupt-*")))
