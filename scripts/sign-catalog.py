#!/usr/bin/env python3
"""Generate bootstrap/templates/catalog.json and catalog.sig from template files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _load_ed25519_private_key(path: Path) -> Ed25519PrivateKey:
    data = path.read_bytes()
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("--key-file must be an Ed25519 private key (PEM)")
    return key


def main() -> None:
    ap = argparse.ArgumentParser(description="Sign Subumbra bootstrap template catalog.")
    ap.add_argument("--key-file", required=True, help="Path to Ed25519 private key PEM")
    ap.add_argument("--templates-dir", required=True, help="Path to bootstrap/templates/")
    args = ap.parse_args()

    root = Path(args.templates_dir).resolve()
    key_path = Path(args.key_file).resolve()

    providers: list[dict[str, str]] = []
    for path in sorted(root.glob("*.json")):
        if path.name == "catalog.json":
            continue
        raw = path.read_bytes()
        providers.append(
            {
                "file": path.name,
                "name": path.stem,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    providers.sort(key=lambda e: e["name"])

    adapters: list[dict[str, str]] = []
    adapters_dir = root / "adapters"
    if adapters_dir.is_dir():
        for path in sorted(adapters_dir.glob("*.json")):
            raw = path.read_bytes()
            rel = f"adapters/{path.name}"
            adapters.append(
                {
                    "file": rel,
                    "name": path.stem,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        adapters.sort(key=lambda e: e["name"])

    catalog_doc = {"version": 1, "providers": providers, "adapters": adapters}
    catalog_bytes = json.dumps(catalog_doc, sort_keys=True, separators=(",", ":")).encode("utf-8")

    catalog_json = root / "catalog.json"
    catalog_sig = root / "catalog.sig"
    catalog_json.write_bytes(catalog_bytes)

    priv = _load_ed25519_private_key(key_path)
    catalog_sig.write_bytes(priv.sign(catalog_bytes))

    pub_hex = priv.public_key().public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw).hex()
    print(pub_hex)


if __name__ == "__main__":
    main()
