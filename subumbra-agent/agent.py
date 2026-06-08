#!/usr/bin/env python3
"""Minimal OpenSSH agent bridge for Subumbra-managed SSH keys."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import socket
import socketserver
import stat
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature


SSH_AGENT_FAILURE = 5
SSH_AGENTC_REQUEST_IDENTITIES = 11
SSH_AGENT_IDENTITIES_ANSWER = 12
SSH_AGENTC_SIGN_REQUEST = 13
SSH_AGENT_SIGN_RESPONSE = 14
SSH_AGENTC_EXTENSION = 27
SSH_SESSION_BIND_EXTENSION = b"session-bind@openssh.com"
SSH_HOSTBOUND_METHOD = b"publickey-hostbound-v00@openssh.com"

SOCKET_PATH = Path(os.environ.get("SUBUMBRA_AGENT_SOCKET_PATH", "/run/subumbra/ssh-agent.sock"))
ENDPOINT_JSON_PATH = Path(os.environ.get("SUBUMBRA_AGENT_KEYS_PATH", "/app/data/endpoint.json"))
PROXY_URL = os.environ.get("SUBUMBRA_AGENT_PROXY_URL", "http://subumbra-proxy:8090").rstrip("/")
CONSUMER_ID = os.environ.get("SUBUMBRA_AGENT_CONSUMER_ID", os.environ.get("SUBUMBRA_AGENT_CONSUMER_ID", "sshtest"))
CONSUMER_TOKEN = os.environ.get("SUBUMBRA_AGENT_CONSUMER_TOKEN", os.environ.get("SUBUMBRA_AGENT_CONSUMER_TOKEN", ""))
AGENT_UID = int(os.environ.get("SUBUMBRA_AGENT_UID", "1000"))
AGENT_GID = int(os.environ.get("SUBUMBRA_AGENT_GID", "1000"))
try:
    SIGN_TIMEOUT = float(os.environ.get("SUBUMBRA_SIGN_TIMEOUT", "30") or "30")
except ValueError:
    SIGN_TIMEOUT = 30.0
if SIGN_TIMEOUT <= 0:
    SIGN_TIMEOUT = 30.0

if not CONSUMER_TOKEN:
    print("ERROR: SUBUMBRA_AGENT_CONSUMER_TOKEN is required", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
LOG = logging.getLogger("subumbra-agent")


@dataclass(frozen=True)
class IdentityRecord:
    key_id: str
    key_blob: bytes
    comment: bytes
    allowed_host_fingerprints: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedBinding:
    host_key_blob: bytes
    host_fingerprint: str
    is_forwarding: bool


class AgentError(RuntimeError):
    """Raised for agent-local protocol or fixture errors."""


def ssh_string(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


def read_ssh_string(payload: bytes, offset: int) -> tuple[bytes, int]:
    if len(payload) < offset + 4:
        raise AgentError("truncated ssh string length")
    size = struct.unpack(">I", payload[offset : offset + 4])[0]
    offset += 4
    if len(payload) < offset + size:
        raise AgentError("truncated ssh string body")
    return payload[offset : offset + size], offset + size


def read_ssh_bool(payload: bytes, offset: int) -> tuple[bool, int]:
    if len(payload) < offset + 1:
        raise AgentError("truncated ssh boolean")
    return payload[offset] != 0, offset + 1


def ssh_key_type_from_blob(key_blob: bytes) -> str:
    key_type, offset = read_ssh_string(key_blob, 0)
    if offset != len(key_blob):
        # SSH public-key wire blobs include nested fields after the type name.
        pass
    try:
        return key_type.decode("ascii")
    except UnicodeDecodeError as exc:
        raise AgentError("invalid ssh key type") from exc


def ssh_public_key_line_from_blob(key_blob: bytes) -> bytes:
    return f"{ssh_key_type_from_blob(key_blob)} {base64.b64encode(key_blob).decode('ascii')}".encode("ascii")


def ssh_public_key_fingerprint(key_blob: bytes) -> str:
    digest = base64.b64encode(hashlib.sha256(key_blob).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{digest}"


def _validate_allowed_host_fingerprints(raw_hosts: Any, *, key_id: str) -> tuple[str, ...]:
    if raw_hosts is None:
        return ()
    if not isinstance(raw_hosts, list):
        raise AgentError(f"invalid allow.hosts for key_id={key_id}")
    parsed: list[str] = []
    seen: set[str] = set()
    for entry in raw_hosts:
        if not isinstance(entry, str) or not entry:
            raise AgentError(f"invalid allow.hosts entry for key_id={key_id}")
        if entry in seen:
            continue
        seen.add(entry)
        parsed.append(entry)
    return tuple(parsed)


def read_ssh_mpint(payload: bytes, offset: int) -> tuple[int, int]:
    raw, offset = read_ssh_string(payload, offset)
    if not raw:
        return 0, offset
    return int.from_bytes(raw, "big", signed=False), offset


def verify_hostkey_signature(*, host_key_blob: bytes, session_id: bytes, signature_blob: bytes) -> None:
    algorithm_raw, offset = read_ssh_string(signature_blob, 0)
    signature_body, offset = read_ssh_string(signature_blob, offset)
    if offset != len(signature_blob):
        raise AgentError("trailing hostkey signature bytes")
    algorithm = algorithm_raw.decode("ascii")
    public_key = serialization.load_ssh_public_key(ssh_public_key_line_from_blob(host_key_blob))

    if isinstance(public_key, Ed25519PublicKey):
        if algorithm != "ssh-ed25519":
            raise AgentError(f"unexpected ed25519 hostkey signature algorithm={algorithm}")
        public_key.verify(signature_body, session_id)
        return

    if isinstance(public_key, rsa.RSAPublicKey):
        if algorithm == "ssh-rsa":
            digest = hashes.SHA1()
        elif algorithm == "rsa-sha2-256":
            digest = hashes.SHA256()
        elif algorithm == "rsa-sha2-512":
            digest = hashes.SHA512()
        else:
            raise AgentError(f"unsupported rsa hostkey signature algorithm={algorithm}")
        public_key.verify(signature_body, session_id, padding.PKCS1v15(), digest)
        return

    if isinstance(public_key, ec.EllipticCurvePublicKey):
        inner_offset = 0
        r_value, inner_offset = read_ssh_mpint(signature_body, inner_offset)
        s_value, inner_offset = read_ssh_mpint(signature_body, inner_offset)
        if inner_offset != len(signature_body):
            raise AgentError("trailing ecdsa signature bytes")
        if algorithm == "ecdsa-sha2-nistp256":
            digest = hashes.SHA256()
        elif algorithm == "ecdsa-sha2-nistp384":
            digest = hashes.SHA384()
        elif algorithm == "ecdsa-sha2-nistp521":
            digest = hashes.SHA512()
        else:
            raise AgentError(f"unsupported ecdsa hostkey signature algorithm={algorithm}")
        public_key.verify(encode_dss_signature(r_value, s_value), session_id, ec.ECDSA(digest))
        return

    raise AgentError(f"unsupported hostkey type={type(public_key).__name__}")


def parse_public_key_line(public_key: str) -> tuple[bytes, bytes]:
    parts = public_key.strip().split(None, 2)
    if len(parts) < 2:
        raise AgentError("invalid public_key line")
    if parts[0] != "ssh-ed25519":
        raise AgentError("unsupported public key type")
    try:
        key_blob = base64.b64decode(parts[1], validate=True)
    except Exception as exc:  # pragma: no cover - exact exception varies
        raise AgentError(f"invalid public key blob: {exc}") from exc
    comment = parts[2].encode("utf-8") if len(parts) > 2 else b""
    return key_blob, comment


def load_identities(endpoint_json_path: Path, consumer_id: str) -> list[IdentityRecord]:
    with endpoint_json_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise AgentError("endpoint.json must contain an object")

    identities: list[IdentityRecord] = []
    for key_id, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "ssh_key":
            continue
        if entry.get("status", "active") != "active":
            continue
        adapters = entry.get("adapters")
        if not isinstance(adapters, list) or consumer_id not in adapters:
            continue
        public_key = entry.get("public_key")
        if not isinstance(public_key, str) or not public_key:
            continue
        try:
            key_blob, comment = parse_public_key_line(public_key)
            policy = entry.get("policy")
            allow = policy.get("allow") if isinstance(policy, dict) else {}
            allowed_host_fingerprints = _validate_allowed_host_fingerprints(
                allow.get("hosts") if isinstance(allow, dict) else None,
                key_id=key_id,
            )
        except AgentError as exc:
            LOG.warning("skip identity key_id=%s reason=%s", key_id, exc)
            continue
        identities.append(
            IdentityRecord(
                key_id=key_id,
                key_blob=key_blob,
                comment=comment,
                allowed_host_fingerprints=allowed_host_fingerprints,
            )
        )

    identities.sort(key=lambda record: record.key_id)
    return identities


def build_identities_answer(identities: list[IdentityRecord]) -> bytes:
    payload = bytearray([SSH_AGENT_IDENTITIES_ANSWER])
    payload.extend(struct.pack(">I", len(identities)))
    for identity in identities:
        payload.extend(ssh_string(identity.key_blob))
        payload.extend(ssh_string(identity.comment))
    return bytes(payload)


def wrap_ssh_ed25519_signature(raw_sig: bytes) -> bytes:
    return ssh_string(b"ssh-ed25519") + ssh_string(raw_sig)


def forward_sign_request(
    *,
    proxy_base_url: str,
    consumer_token: str,
    key_id: str,
    challenge_b64: str,
    verified_host_fingerprint: str | None,
) -> tuple[int, bytes]:
    payload: dict[str, str] = {"challenge": challenge_b64}
    if verified_host_fingerprint:
        payload["verified_host_fingerprint"] = verified_host_fingerprint
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = request.Request(
        f"{proxy_base_url}/t/{key_id}/ssh/sign",
        method="POST",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {consumer_token}",
        },
    )
    try:
        with request.urlopen(req, timeout=SIGN_TIMEOUT) as resp:
            return resp.status, resp.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()


def parse_sign_request(message: bytes) -> tuple[bytes, bytes, int]:
    offset = 1
    key_blob, offset = read_ssh_string(message, offset)
    data, offset = read_ssh_string(message, offset)
    if len(message) < offset + 4:
        raise AgentError("truncated sign request flags")
    flags = struct.unpack(">I", message[offset : offset + 4])[0]
    return key_blob, data, flags


def parse_session_bind_extension(message: bytes) -> VerifiedBinding:
    offset = 1
    extension_name, offset = read_ssh_string(message, offset)
    if extension_name != SSH_SESSION_BIND_EXTENSION:
        raise AgentError(f"unsupported extension={extension_name!r}")
    host_key_blob, offset = read_ssh_string(message, offset)
    session_id, offset = read_ssh_string(message, offset)
    signature_blob, offset = read_ssh_string(message, offset)
    if len(message) < offset + 1:
        raise AgentError("truncated session-bind forwarding flag")
    is_forwarding = message[offset] != 0
    offset += 1
    if offset != len(message):
        raise AgentError("trailing session-bind bytes")
    verify_hostkey_signature(
        host_key_blob=host_key_blob,
        session_id=session_id,
        signature_blob=signature_blob,
    )
    return VerifiedBinding(
        host_key_blob=host_key_blob,
        host_fingerprint=ssh_public_key_fingerprint(host_key_blob),
        is_forwarding=is_forwarding,
    )


def extract_hostbound_host_fingerprint(to_sign: bytes) -> str | None:
    if not to_sign:
        raise AgentError("empty sign payload")
    offset = 0
    # OpenSSH host-bound sign payloads start with the SSH session id as an
    # ssh-string, followed by SSH_MSG_USERAUTH_REQUEST (50).
    _session_id, offset = read_ssh_string(to_sign, offset)
    if len(to_sign) < offset + 1:
        raise AgentError("truncated host-bound userauth message type")
    if to_sign[offset] != 50:
        raise AgentError(f"unexpected host-bound message type={to_sign[offset]}")
    offset += 1
    _username, offset = read_ssh_string(to_sign, offset)
    _service, offset = read_ssh_string(to_sign, offset)
    method_name, offset = read_ssh_string(to_sign, offset)
    if method_name != SSH_HOSTBOUND_METHOD:
        return None
    _has_signature, offset = read_ssh_bool(to_sign, offset)
    _algorithm, offset = read_ssh_string(to_sign, offset)
    _user_public_key, offset = read_ssh_string(to_sign, offset)
    server_host_key, offset = read_ssh_string(to_sign, offset)
    if offset > len(to_sign):
        raise AgentError("truncated host-bound sign payload")
    return ssh_public_key_fingerprint(server_host_key)


def build_failure() -> bytes:
    return bytes([SSH_AGENT_FAILURE])


def build_sign_response(signature_blob: bytes) -> bytes:
    payload = bytearray([SSH_AGENT_SIGN_RESPONSE])
    payload.extend(ssh_string(signature_blob))
    return bytes(payload)


class ThreadedUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class AgentRequestHandler(socketserver.StreamRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.effective_binding: VerifiedBinding | None = None

    def handle(self) -> None:
        try:
            while True:
                raw_length = self.rfile.read(4)
                if not raw_length:
                    return
                if len(raw_length) != 4:
                    LOG.warning("protocol reject reason=truncated_frame_length")
                    return
                size = struct.unpack(">I", raw_length)[0]
                message = self.rfile.read(size)
                if len(message) != size:
                    LOG.warning("protocol reject reason=truncated_frame_body size=%s", size)
                    return
                response = self.dispatch(message)
                self.wfile.write(struct.pack(">I", len(response)))
                self.wfile.write(response)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError) as exc:
            LOG.debug("client disconnected socket=%s error=%s", self.client_address, exc.__class__.__name__)

    def dispatch(self, message: bytes) -> bytes:
        if not message:
            LOG.warning("protocol reject reason=empty_message")
            return build_failure()
        msg_type = message[0]
        if msg_type == SSH_AGENTC_REQUEST_IDENTITIES:
            return self.handle_request_identities()
        if msg_type == SSH_AGENTC_EXTENSION:
            return self.handle_extension(message)
        if msg_type == SSH_AGENTC_SIGN_REQUEST:
            return self.handle_sign_request(message)
        LOG.debug("unsupported message_type=%s", msg_type)
        return build_failure()

    def handle_request_identities(self) -> bytes:
        try:
            identities = load_identities(ENDPOINT_JSON_PATH, CONSUMER_ID)
        except Exception as exc:
            LOG.error("identities failure error=%s", exc)
            return build_failure()
        LOG.info("identities served count=%s consumer=%s", len(identities), CONSUMER_ID)
        return build_identities_answer(identities)

    def handle_extension(self, message: bytes) -> bytes:
        try:
            binding = parse_session_bind_extension(message)
        except AgentError as exc:
            LOG.debug("extension ignore reason=%s", exc)
            return build_failure()
        except Exception as exc:
            LOG.warning("extension reject reason=%s", exc)
            return build_failure()

        LOG.info(
            "session-bind accepted fingerprint=%s forwarding=%s",
            binding.host_fingerprint,
            int(binding.is_forwarding),
        )
        if not binding.is_forwarding:
            self.effective_binding = binding
        return build_failure()

    def handle_sign_request(self, message: bytes) -> bytes:
        try:
            key_blob, data, flags = parse_sign_request(message)
            identities = load_identities(ENDPOINT_JSON_PATH, CONSUMER_ID)
        except Exception as exc:
            LOG.warning("sign reject reason=%s", exc)
            return build_failure()

        matched = next((identity for identity in identities if identity.key_blob == key_blob), None)
        if matched is None:
            LOG.warning("sign reject reason=unknown_key_blob")
            return build_failure()

        restricted = bool(matched.allowed_host_fingerprints)
        verified_host_fingerprint = self.effective_binding.host_fingerprint if self.effective_binding else None
        if restricted:
            if verified_host_fingerprint is None:
                LOG.warning("sign deny key_id=%s reason=host_required", matched.key_id)
                return build_failure()
            try:
                hostbound_host_fingerprint = extract_hostbound_host_fingerprint(data)
            except AgentError as exc:
                LOG.warning("sign deny key_id=%s reason=host_binding_mismatch error=%s", matched.key_id, exc)
                return build_failure()
            if hostbound_host_fingerprint != verified_host_fingerprint:
                LOG.warning("sign deny key_id=%s reason=host_binding_mismatch", matched.key_id)
                return build_failure()
            if verified_host_fingerprint not in matched.allowed_host_fingerprints:
                LOG.warning(
                    "sign deny key_id=%s reason=host_not_allowed fingerprint=%s",
                    matched.key_id,
                    verified_host_fingerprint,
                )
                return build_failure()

        LOG.info("sign request key_id=%s flags=%s", matched.key_id, flags)
        challenge_b64 = base64.b64encode(data).decode("ascii")
        status_code, response_body = forward_sign_request(
            proxy_base_url=PROXY_URL,
            consumer_token=CONSUMER_TOKEN,
            key_id=matched.key_id,
            challenge_b64=challenge_b64,
            verified_host_fingerprint=verified_host_fingerprint,
        )
        if status_code != 200:
            LOG.warning("sign failure key_id=%s status=%s", matched.key_id, status_code)
            return build_failure()

        try:
            payload = json.loads(response_body)
            raw_sig_b64 = payload["signature"]
            raw_sig = base64.b64decode(raw_sig_b64, validate=True)
        except Exception as exc:
            LOG.error("sign failure key_id=%s reason=invalid_response error=%s", matched.key_id, exc)
            return build_failure()

        if len(raw_sig) != 64:
            LOG.error("sign failure key_id=%s reason=invalid_signature_len len=%s", matched.key_id, len(raw_sig))
            return build_failure()

        signature_blob = wrap_ssh_ed25519_signature(raw_sig)
        LOG.info("sign success key_id=%s status=%s", matched.key_id, status_code)
        return build_sign_response(signature_blob)


def serve() -> None:
    try:
        if SOCKET_PATH.exists() or SOCKET_PATH.is_socket():
            SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass
    with ThreadedUnixServer(str(SOCKET_PATH), AgentRequestHandler) as server:
        os.chmod(SOCKET_PATH, stat.S_IRUSR | stat.S_IWUSR)
        LOG.info(
            "agent ready socket=%s consumer=%s endpoint_json=%s uid=%s gid=%s",
            SOCKET_PATH,
            CONSUMER_ID,
            ENDPOINT_JSON_PATH,
            os.getuid(),
            os.getgid(),
        )
        server.serve_forever()


if __name__ == "__main__":
    serve()
