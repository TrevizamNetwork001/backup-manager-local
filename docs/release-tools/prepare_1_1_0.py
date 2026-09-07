"""Prepare the first official release locally. Does not upload or change keys."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PUBLIC_SHA256 = "9e65a39702966069d3d68dfbb6ae14c6842173ae70084db8f5c71354e18ee857"
PAYLOAD_SHA256 = "2defdbd76acce22b1bc45c54a14e05404c29bbd58d440839bc25c27e503b3880"
VERSION = "1.1.0"
BUILD = "stable-1.1.0-20260907"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def prepare(source: Path, keys: Path, output: Path, repository: str) -> dict:
    source, keys, output = source.resolve(), keys.resolve(), output.resolve()
    if output.exists():
        raise ValueError("A pasta de saida ja existe. Escolha outra; nada foi sobrescrito.")
    if output.is_relative_to(source) or keys.is_relative_to(source):
        raise ValueError("Mantenha chaves e saida fora da pasta do codigo-fonte.")
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Repositorio invalido; use conta/nome.")
    private_path, public_path = keys / "update-private-key.pem", keys / "update-public-key.pem"
    private = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    public = serialization.load_pem_public_key(public_path.read_bytes())
    if not isinstance(private, Ed25519PrivateKey) or not isinstance(public, Ed25519PublicKey):
        raise ValueError("As chaves precisam ser Ed25519.")
    der = public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if hashlib.sha256(der).hexdigest() != PUBLIC_SHA256:
        raise ValueError("A chave publica nao corresponde a instalada no servidor.")
    public.verify(private.sign(b"backup-manager-release-check"), b"backup-manager-release-check")
    version = runpy.run_path(str(source / "backup_manager/version.py"))
    if (version["__version__"], version["__channel__"], version["__build_id__"]) != (VERSION, "stable", BUILD):
        raise ValueError("Este assistente aceita somente a release 1.1.0 validada.")
    builder = runpy.run_path(str(source / "scripts/build-update-package.py"))
    aggregate = hashlib.sha256()
    for relative in sorted(builder["collect"](), key=lambda path: path.as_posix()):
        digest = hashlib.sha256((source / relative).read_bytes()).hexdigest()
        aggregate.update(relative.as_posix().encode() + b"\0" + digest.encode() + b"\n")
    if aggregate.hexdigest() != PAYLOAD_SHA256:
        raise ValueError("O codigo difere do payload 1.1.0 validado. Use o codigo original, sem alteracoes de linhas.")

    # Imports only the standalone validators, never the database or web app.
    sys.path.insert(0, str(source))
    from backup_manager.updater import validate_package
    from backup_manager.update_repository import validate_index, select_release

    owner, name = repository.split("/")
    pages_url = f"https://{owner.lower()}.github.io/{name}"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="release-build-", dir=output.parent) as temporary:
        temp = Path(temporary)
        subprocess.run([
            sys.executable, str(source / "scripts/build-update-package.py"),
            "--version", VERSION, "--minimum-version", "1.1.0-rc9", "--channel", "stable",
            "--build-id", BUILD, "--private-key", str(private_path), "--output", str(temp / "packages"),
            "--release-notes", "1.1.0: versao estavel; correcoes Huawei FTP e caminhos rclone.",
        ], check=True, capture_output=True)
        package = temp / "packages" / f"backup-manager-local-{VERSION}.bmu"
        result = validate_package(package, public_path, installed_version="1.1.0-rc9")
        if result.manifest["payload_sha256"] != PAYLOAD_SHA256:
            raise ValueError("O payload mudou durante a preparacao.")
        subprocess.run([
            sys.executable, str(source / "scripts/publish-update-index.py"),
            "--packages", str(temp / "packages"), "--output", str(temp / "ready"),
            "--base-url", pages_url, "--private-key", str(private_path),
        ], check=True, capture_output=True)
        ready = temp / "ready"
        index = json.loads((ready / "updates.json").read_bytes())
        release = index["channels"]["stable"]["releases"][0]
        base = f"https://github.com/{repository}/releases/download/v{VERSION}"
        release["package_url"] = f"{base}/{package.name}"
        release["signature_url"] = release["package_url"] + ".sig"
        raw = canonical(index)
        signature = private.sign(raw)
        checked = validate_index(raw, signature, public_path)
        if not select_release(checked, "stable", installed_version="1.1.0-rc9"):
            raise ValueError("A release nao foi selecionada para RC9.")
        public.verify((ready / "releases" / (package.name + ".sig")).read_bytes(),
                      hashlib.sha256(package.read_bytes()).hexdigest().encode("ascii"))
        pages = ready / "pages"
        pages.mkdir()
        (ready / "updates.json").unlink()
        (ready / "updates.json.sig").unlink()
        (pages / "updates.json").write_bytes(raw)
        (pages / "updates.json.sig").write_bytes(signature)
        (pages / ".nojekyll").write_bytes(b"")
        (pages / ".gitattributes").write_text("* -text\n", encoding="utf-8")
        (pages / "index.html").write_text(
            '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
            '<title>Backup Manager - Atualizacoes</title><h1>Backup Manager Local</h1>'
            '<p>Distribuicao de atualizacoes assinadas.</p>'
            '<a href="updates.json">Indice de atualizacoes</a></html>', encoding="utf-8")
        report = {"version": VERSION, "payload_sha256": PAYLOAD_SHA256,
                  "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                  "public_key_sha256_der": PUBLIC_SHA256, "repository": repository,
                  "repository_url": pages_url, "signed_and_validated": True, "uploaded": False}
        (ready / "validation.json").write_bytes(canonical(report))
        ready.rename(output)
    return report


def main():
    parser = argparse.ArgumentParser(description="Prepara a 1.1.0 assinada no Windows; nao faz upload.")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--keys", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path.cwd() / "publicacao-1.1.0")
    parser.add_argument("--repository", default="TrevizamNetwork001/backup-manager-updates")
    args = parser.parse_args()
    try:
        prepare(args.source, args.keys, args.output, args.repository)
    except Exception as error:
        # Do not include key contents or subprocess logs in the output.
        print("FALHA:", str(error) if isinstance(error, ValueError) else type(error).__name__)
        return 1
    print("OK: pacote e indice assinados e validados. Nenhum arquivo foi enviado.")
    print("Saida:", args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
