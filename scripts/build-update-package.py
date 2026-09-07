#!/usr/bin/env python3
"""Constrói um .bmu assinado; uso exclusivo do ambiente de release."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ("backup_manager", "migrations", "deploy/systemd", "scripts", "static", "templates")
EXCLUDED_SCRIPTS = {"install.sh", "configure-https.sh", "enable-ftp.sh"}


def canonical(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def collect() -> list[Path]:
    files: list[Path] = []
    for name in ALLOWLIST:
        base = ROOT / name
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(ROOT)
            if path.is_file() and not path.is_symlink() and "__pycache__" not in rel.parts and path.name not in EXCLUDED_SCRIPTS:
                files.append(rel)
    return files


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--minimum-version", required=True)
    parser.add_argument("--maximum-version", default="")
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--build-id", default=os.environ.get("BUILD_ID", "release"))
    parser.add_argument("--private-key", default=os.environ.get("BACKUP_MANAGER_RELEASE_KEY", ""))
    parser.add_argument("--output", type=Path, default=Path.cwd())
    parser.add_argument("--release-notes", default="")
    args = parser.parse_args()
    if not args.private_key:
        parser.error("--private-key ou BACKUP_MANAGER_RELEASE_KEY e obrigatorio")
    key_path = Path(args.private_key)
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        parser.error("a chave deve ser Ed25519")
    # O validador calcula o hash agregado em ordem lexicográfica de caminho.
    # Mantenha exatamente a mesma ordem, independentemente da ordem da allowlist.
    files = sorted(collect(), key=lambda path: path.as_posix())
    entries, aggregate = [], hashlib.sha256()
    for rel in files:
        content = (ROOT / rel).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        value = rel.as_posix()
        entries.append({"path": value, "sha256": digest, "size": len(content), "action": "replace"})
        aggregate.update(value.encode() + b"\0" + digest.encode() + b"\n")
    manifest = {
        "product": "backup-manager-local", "version": args.version,
        "minimum_version": args.minimum_version, "maximum_version": args.maximum_version,
        "channel": args.channel, "build_id": args.build_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "payload_sha256": aggregate.hexdigest(),
        "required_disk_bytes": sum(x["size"] for x in entries) * 3,
        "requires_restart": True, "services_to_restart": ["backup-manager-local.service"],
        "migrations": [], "files": entries, "pre_checks": ["integrity-check", "storage-check"],
        "post_checks": ["integrity-check", "storage-check", "version", "login", "assets"],
        "release_notes": args.release_notes,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / f"backup-manager-local-{args.version}.bmu"
    with tempfile.TemporaryDirectory(prefix="bmu-build-") as temp_name:
        temp = Path(temp_name)
        (temp / "payload").mkdir()
        checks = []
        for rel in files:
            destination = temp / "payload" / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / rel).read_bytes())
            checks.append(f"{hashlib.sha256(destination.read_bytes()).hexdigest()}  payload/{rel.as_posix()}")
        (temp / "manifest.json").write_bytes(canonical(manifest))
        checksums_raw = ("\n".join(checks) + "\n").encode("utf-8")
        (temp / "checksums.txt").write_bytes(checksums_raw)
        (temp / "signature").write_bytes(key.sign(canonical(manifest) + b"\n" + checksums_raw))
        with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(temp.rglob("*")):
                if path.is_file(): archive.add(path, arcname=path.relative_to(temp).as_posix(), recursive=False)
    report = {"package": output.name, "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "files": len(files), "version": args.version}
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__": main()
