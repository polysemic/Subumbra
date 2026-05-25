#!/usr/bin/env python3
"""SSH bootstrap helpers for Subumbra."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.error
import urllib.request
from typing import Any

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class SshBootstrapError(Exception):
    """Raised when SSH bootstrap operations fail."""


def operator_ssh_auth_sock() -> str:
    return "${XDG_RUNTIME_DIR}/subumbra/ssh-agent.sock"


def emit_agent_setup_instructions(public_key: str) -> None:
    socket_path = operator_ssh_auth_sock()
    print("  SSH agent socket:")
    print(f"    {socket_path}")
    print("  Add to your shell profile:")
    print(f"    export SSH_AUTH_SOCK={socket_path}")
    print("  Add to ~/.ssh/config:")
    print("    Match host <HOSTNAME>")
    print(f"        IdentityAgent {socket_path}")
    print("        IdentitiesOnly no")
    print("  If you force IdentitiesOnly yes, add a matching IdentityFile or OpenSSH")
    print("  may ignore agent-backed keys during auth.")
    print("  Full SSH setup guide:")
    print("    docs/ssh-guide.md")
    print("  Authorized public key:")
    print(f"    {public_key}")


def _normalize_secret_text(raw_secret: str) -> str:
    raw = raw_secret.strip()
    if "\\n" in raw and "-----BEGIN " in raw:
        raw = raw.replace("\\n", "\n")
    if not raw.endswith("\n"):
        raw += "\n"
    return raw


def _encode_ssh_ed25519_public_key(raw_public_bytes: bytes, key_id: str) -> str:
    key_type = b"ssh-ed25519"
    payload = (
        len(key_type).to_bytes(4, "big")
        + key_type
        + len(raw_public_bytes).to_bytes(4, "big")
        + raw_public_bytes
    )
    b64 = base64.b64encode(payload).decode("ascii")
    return f"ssh-ed25519 {b64} subumbra:{key_id}"


def _compute_ssh_policy_hash(policy_doc: dict[str, Any]) -> str:
    allow = policy_doc["allow"]
    baseline_obj = {
        "type": policy_doc["type"],
        "key_id": policy_doc["key_id"],
        "algorithm": policy_doc["algorithm"],
        "allow": {
            "adapters": sorted(allow["adapters"]),
        },
    }
    canonical = json.dumps(baseline_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_ssh_policy(*, key_id: str, adapters: list[str]) -> dict[str, Any]:
    return {
        "type": "ssh_key",
        "policy_id": f"ssh-{key_id}",
        "key_id": key_id,
        "algorithm": "ed25519",
        "allow": {
            "adapters": sorted(adapters),
        },
    }


def build_ssh_record(
    *,
    key_id: str,
    key_source: str,
    adapters: list[str],
    public_key: str,
    vault_instance: str,
    created_at: str,
) -> dict[str, Any]:
    policy = build_ssh_policy(key_id=key_id, adapters=adapters)
    return {
        "key_id": key_id,
        "type": "ssh_key",
        "provider": "ssh",
        "key_source": key_source,
        "algorithm": "ed25519",
        "public_key": public_key,
        "vault_instance": vault_instance,
        "created_at": created_at,
        "status": "active",
        "policy_id": policy["policy_id"],
        "policy": policy,
        "policy_hash": _compute_ssh_policy_hash(policy),
        "adapters": sorted(adapters),
        "label": key_id,
        "revoked": False,
    }


def encrypt_ssh_private_key_for_vault(public_key_pem: str, pkcs8_bytes: bytes) -> str:
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    except Exception as exc:  # pragma: no cover - explicit message more useful than traceback
        raise SshBootstrapError(f"Invalid vault public key PEM: {exc}") from exc

    ciphertext = public_key.encrypt(
        pkcs8_bytes,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode("ascii")


def load_unencrypted_open_ssh_private_key(raw_secret: str) -> tuple[bytes, str]:
    normalized = _normalize_secret_text(raw_secret)
    try:
        private_key = serialization.load_ssh_private_key(normalized.encode("utf-8"), password=None)
    except TypeError as exc:
        raise SshBootstrapError(
            "Encrypted SSH private keys are not supported in this round. "
            "Provide an unencrypted OpenSSH ed25519 private key."
        ) from exc
    except UnsupportedAlgorithm as exc:
        raise SshBootstrapError(
            "Encrypted or unsupported SSH private keys are not supported in this round. "
            "Provide an unencrypted OpenSSH ed25519 private key."
        ) from exc
    except ValueError as exc:
        raise SshBootstrapError(f"Invalid SSH private key: {exc}") from exc

    if not isinstance(private_key, Ed25519PrivateKey):
        raise SshBootstrapError("Only unencrypted OpenSSH ed25519 private keys are supported in this round.")

    pkcs8_bytes = private_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return pkcs8_bytes, _encode_ssh_ed25519_public_key(public_raw, "imported")


_MAX_SETUP_TOKEN_ATTEMPTS = 24
_SETUP_TOKEN_RETRY_DELAY_SEC = 5


def _call_worker_json(
    *,
    worker_url: str,
    path: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    POST a JSON payload to a setup-token-protected Worker route and parse
    the JSON response. Retries on 401/403/503 to absorb the propagation
    window after `wrangler secret put SUBUMBRA_SETUP_TOKEN` — Cloudflare
    may serve requests from a Worker instance that has not yet picked up
    the new secret. Mirrors the retry pattern in
    subumbra-bootstrap.py call_setup_keygen.
    """
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    last_http_error: urllib.error.HTTPError | None = None
    for attempt in range(1, _MAX_SETUP_TOKEN_ATTEMPTS + 1):
        req = urllib.request.Request(
            f"{worker_url.rstrip('/')}{path}",
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req) as resp:
                response_payload = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            last_http_error = exc
            if exc.code in (401, 403, 503) and attempt < _MAX_SETUP_TOKEN_ATTEMPTS:
                print(
                    f"  ·  Worker {path} returned HTTP {exc.code}; "
                    f"setup token not visible yet, retrying "
                    f"({attempt}/{_MAX_SETUP_TOKEN_ATTEMPTS})",
                    flush=True,
                )
                time.sleep(_SETUP_TOKEN_RETRY_DELAY_SEC)
                continue
            body_text = exc.read().decode("utf-8", errors="replace")
            raise SshBootstrapError(
                f"Worker call {path} failed: HTTP {exc.code}\n"
                f"--- response body ---\n{body_text}"
            ) from exc
        except Exception as exc:  # pragma: no cover - transport exceptions vary by environment
            raise SshBootstrapError(f"Worker call {path} failed: {exc}") from exc
    else:
        if last_http_error is not None:
            body_text = last_http_error.read().decode("utf-8", errors="replace")
            raise SshBootstrapError(
                f"Worker call {path} failed after retry window: HTTP {last_http_error.code}\n"
                f"--- response body ---\n{body_text}"
            )
        raise SshBootstrapError(f"Worker call {path} failed after retry window")

    if not isinstance(response_payload, dict):
        raise SshBootstrapError(f"Worker call {path} returned invalid JSON schema")
    return response_payload


def provision_generated_ssh_key(
    *,
    worker_url: str,
    headers: dict[str, str],
    key_id: str,
    adapters: list[str],
    vault_instance: str,
) -> dict[str, Any]:
    payload = _call_worker_json(
        worker_url=worker_url,
        path="/setup/ssh-keygen",
        headers=headers,
        payload={"key_id": key_id, "vault_instance": vault_instance},
    )
    public_key = payload.get("public_key")
    created_at = payload.get("created_at")
    if not isinstance(public_key, str) or not public_key:
        raise SshBootstrapError("Cloudflare SSH keygen returned an invalid public_key")
    if not isinstance(created_at, str) or not created_at:
        raise SshBootstrapError("Cloudflare SSH keygen returned an invalid created_at")
    record = build_ssh_record(
        key_id=key_id,
        key_source="generated",
        adapters=adapters,
        public_key=public_key,
        vault_instance=vault_instance,
        created_at=created_at,
    )
    emit_agent_setup_instructions(record["public_key"])
    return record


def provision_imported_ssh_key(
    *,
    worker_url: str,
    headers: dict[str, str],
    key_id: str,
    adapters: list[str],
    vault_instance: str,
    public_key_pem: str,
    raw_secret: str,
) -> dict[str, Any]:
    pkcs8_bytes, _derived_public_key = load_unencrypted_open_ssh_private_key(raw_secret)
    encrypted_private_key = encrypt_ssh_private_key_for_vault(public_key_pem, pkcs8_bytes)
    payload = _call_worker_json(
        worker_url=worker_url,
        path="/setup/ssh-import",
        headers=headers,
        payload={
            "key_id": key_id,
            "vault_instance": vault_instance,
            "encrypted_private_key": encrypted_private_key,
        },
    )
    public_key = payload.get("public_key")
    created_at = payload.get("created_at")
    if not isinstance(public_key, str) or not public_key:
        raise SshBootstrapError("Cloudflare SSH import returned an invalid public_key")
    if not isinstance(created_at, str) or not created_at:
        raise SshBootstrapError("Cloudflare SSH import returned an invalid created_at")
    record = build_ssh_record(
        key_id=key_id,
        key_source="provided",
        adapters=adapters,
        public_key=public_key,
        vault_instance=vault_instance,
        created_at=created_at,
    )
    emit_agent_setup_instructions(record["public_key"])
    return record
