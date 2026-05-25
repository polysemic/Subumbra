#!/usr/bin/env python3
"""Minimal OpenSSH agent bridge for Subumbra-managed SSH keys."""

from __future__ import annotations

import base64
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


SSH_AGENT_FAILURE = 5
SSH_AGENTC_REQUEST_IDENTITIES = 11
SSH_AGENT_IDENTITIES_ANSWER = 12
SSH_AGENTC_SIGN_REQUEST = 13
SSH_AGENT_SIGN_RESPONSE = 14

SOCKET_PATH = Path(os.environ.get("SUBUMBRA_AGENT_SOCKET_PATH", "/run/subumbra/ssh-agent.sock"))
KEYS_JSON_PATH = Path(os.environ.get("SUBUMBRA_AGENT_KEYS_PATH", "/app/keys.json"))
PROXY_URL = os.environ.get("SUBUMBRA_AGENT_PROXY_URL", "http://subumbra-proxy:8090").rstrip("/")
ADAPTER_ID = os.environ.get("SUBUMBRA_AGENT_ADAPTER_ID", "sshtest")
ADAPTER_TOKEN = os.environ.get("SUBUMBRA_AGENT_ADAPTER_TOKEN", "")
AGENT_UID = int(os.environ.get("SUBUMBRA_AGENT_UID", "1000"))
AGENT_GID = int(os.environ.get("SUBUMBRA_AGENT_GID", "1000"))

if not ADAPTER_TOKEN:
    print("ERROR: SUBUMBRA_AGENT_ADAPTER_TOKEN is required", file=sys.stderr)
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


def load_identities(keys_json_path: Path, adapter_id: str) -> list[IdentityRecord]:
    with keys_json_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise AgentError("keys.json must contain an object")

    identities: list[IdentityRecord] = []
    for key_id, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "ssh_key":
            continue
        if entry.get("status", "active") != "active":
            continue
        adapters = entry.get("adapters")
        if not isinstance(adapters, list) or adapter_id not in adapters:
            continue
        public_key = entry.get("public_key")
        if not isinstance(public_key, str) or not public_key:
            continue
        try:
            key_blob, comment = parse_public_key_line(public_key)
        except AgentError as exc:
            LOG.warning("skip identity key_id=%s reason=%s", key_id, exc)
            continue
        identities.append(IdentityRecord(key_id=key_id, key_blob=key_blob, comment=comment))

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


def forward_sign_request(*, proxy_base_url: str, adapter_token: str, key_id: str, challenge_b64: str) -> tuple[int, bytes]:
    body = json.dumps({"challenge": challenge_b64}, separators=(",", ":")).encode("utf-8")
    req = request.Request(
        f"{proxy_base_url}/t/{key_id}/ssh/sign",
        method="POST",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {adapter_token}",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
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
        if msg_type == SSH_AGENTC_SIGN_REQUEST:
            return self.handle_sign_request(message)
        LOG.debug("unsupported message_type=%s", msg_type)
        return build_failure()

    def handle_request_identities(self) -> bytes:
        try:
            identities = load_identities(KEYS_JSON_PATH, ADAPTER_ID)
        except Exception as exc:
            LOG.error("identities failure error=%s", exc)
            return build_failure()
        LOG.info("identities served count=%s adapter=%s", len(identities), ADAPTER_ID)
        return build_identities_answer(identities)

    def handle_sign_request(self, message: bytes) -> bytes:
        try:
            key_blob, data, flags = parse_sign_request(message)
            identities = load_identities(KEYS_JSON_PATH, ADAPTER_ID)
        except Exception as exc:
            LOG.warning("sign reject reason=%s", exc)
            return build_failure()

        matched = next((identity for identity in identities if identity.key_blob == key_blob), None)
        if matched is None:
            LOG.warning("sign reject reason=unknown_key_blob")
            return build_failure()

        LOG.info("sign request key_id=%s flags=%s", matched.key_id, flags)
        challenge_b64 = base64.b64encode(data).decode("ascii")
        status_code, response_body = forward_sign_request(
            proxy_base_url=PROXY_URL,
            adapter_token=ADAPTER_TOKEN,
            key_id=matched.key_id,
            challenge_b64=challenge_b64,
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
            "agent ready socket=%s adapter=%s keys_json=%s uid=%s gid=%s",
            SOCKET_PATH,
            ADAPTER_ID,
            KEYS_JSON_PATH,
            os.getuid(),
            os.getgid(),
        )
        server.serve_forever()


if __name__ == "__main__":
    serve()
