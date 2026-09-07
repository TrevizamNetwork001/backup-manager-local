#!/usr/bin/env python3
"""Gera um diretório estático publicável; não realiza upload."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import re
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]


def canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def version_key(value: str) -> tuple:
    """Ordenação semântica mínima, sem depender de ordenação textual."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?", value)
    if not match:
        raise ValueError(f"versão inválida: {value}")
    pre = match.group(4)
    # stable é maior que qualquer pré-release; identificadores numéricos
    # precedem identificadores textuais conforme SemVer.
    identifiers = () if pre is None else tuple((0, int(part)) if part.isdigit() else (1, part.lower()) for part in pre.split("."))
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), 1 if pre is None else 0, identifiers)


def inspect_package(path: Path, key: Ed25519PrivateKey) -> dict:
    with tarfile.open(path, "r:gz") as archive:
        manifest_raw = archive.extractfile("manifest.json").read()
        checksums = archive.extractfile("checksums.txt").read()
        signature = archive.extractfile("signature").read()
    manifest = json.loads(manifest_raw)
    key.public_key().verify(signature, canonical(manifest) + b"\n" + checksums)
    if manifest.get("product") != "backup-manager-local" or manifest.get("channel") not in {"stable", "rc"}:
        raise ValueError(f"pacote incompatível: {path.name}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera updates.json assinado e releases estáticas")
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--private-key", required=True, help="chave Ed25519 externa ao repositório")
    parser.add_argument("--mandatory", action="append", default=[])
    parser.add_argument("--security-update", action="append", default=[])
    parser.add_argument("--deprecated", action="append", default=[])
    args = parser.parse_args()
    if not args.base_url.startswith("https://"):
        parser.error("--base-url deve usar HTTPS")
    key = serialization.load_pem_private_key(Path(args.private_key).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        parser.error("a chave deve ser Ed25519")
    releases = args.output / "releases"; releases.mkdir(parents=True, exist_ok=True)
    channels = {"stable": {"latest": "", "releases": []}, "rc": {"latest": "", "releases": []}}
    for package in sorted(args.packages.glob("*.bmu")):
        try: manifest = inspect_package(package, key)
        except (KeyError, ValueError, tarfile.TarError, InvalidSignature) as exc: raise SystemExit(f"pacote recusado: {package.name}: {type(exc).__name__}") from exc
        target = releases / package.name; shutil.copy2(package, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        (target.with_suffix(target.suffix + ".sig")).write_bytes(key.sign(digest.encode("ascii")))
        version = manifest["version"]
        item = {"version": version, "channel": manifest["channel"], "build_id": manifest["build_id"],
                "published_at": manifest["created_at"], "minimum_version": manifest["minimum_version"], "maximum_version": manifest["maximum_version"],
                "package_url": f"{args.base_url.rstrip('/')}/releases/{target.name}", "package_sha256": digest, "package_size": target.stat().st_size,
                "signature_url": f"{args.base_url.rstrip('/')}/releases/{target.name}.sig", "release_notes": str(manifest.get("release_notes", ""))[:4000],
                "mandatory": version in args.mandatory, "deprecated": version in args.deprecated, "security_update": version in args.security_update}
        channels[item["channel"]]["releases"].append(item)
    for channel in channels.values():
        channel["releases"].sort(key=lambda x: version_key(x["version"]))
        channel["latest"] = channel["releases"][-1]["version"] if channel["releases"] else ""
    index = {"product": "backup-manager-local", "generated_at": datetime.now(timezone.utc).isoformat(), "channels": channels}
    raw = canonical(index); (args.output / "updates.json").write_bytes(raw); (args.output / "updates.json.sig").write_bytes(key.sign(raw))
    print(json.dumps({"output": str(args.output), "releases": sum(len(x["releases"]) for x in channels.values())}))


if __name__ == "__main__": main()
