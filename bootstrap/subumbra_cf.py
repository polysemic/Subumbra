#!/usr/bin/env python3
"""Cloudflare bootstrap operations for Subumbra."""

from __future__ import annotations

import base64
from _hash_utils import hash_ui_password

from cryptography.hazmat.primitives.asymmetric import ec

from subumbra_core import *
from subumbra_core import (
    _delete_file_if_present,
    _has_cf_credentials,
    _is_revoked_record,
    _prompt_hidden_line,
    _read_env_file_value,
    _read_runtime_credential_value,
    _require_fat_record_fields,
    _resolved_cf_worker_name_from_operator_context,
    _sync_host_env_file,
    _verify_embedded_policy_hash,
    _write_system_integrity,
)

def _kv_put_text_value(
    cf_creds: dict[str, str],
    namespace_id: str,
    key_name: str,
    value: str,
    *,
    expiration_ttl: int,
) -> None:
    url = _kv_value_url(cf_creds, namespace_id, key_name)
    if expiration_ttl > 0:
        url += f"?expiration_ttl={expiration_ttl}"
    request = urllib.request.Request(
        url,
        data=value.encode("utf-8"),
        method="PUT",
        headers={"Authorization": f"Bearer {cf_creds['CF_API_TOKEN']}"},
    )
    try:
        with urllib.request.urlopen(request):
            return
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        die(
            f"Failed to write structured KV key {key_name!r}: HTTP {exc.code}\n"
            f"--- response body ---\n{body_text}"
        )
    except Exception as exc:
        die(f"Failed to write structured KV key {key_name!r}: {exc}")


def _cf_api_request(
    method: str,
    path: str,
    cf_api_token: str,
    *,
    account_id: str | None = None,
    zone_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if bool(account_id) == bool(zone_id):
        raise BootstrapFlowError("Cloudflare API request requires exactly one of account_id or zone_id")
    if not path.startswith("/"):
        raise BootstrapFlowError(f"Cloudflare API path must start with '/': {path!r}")
    if account_id:
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}{path}"
    else:
        url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}{path}"
    data = None
    headers = {"Authorization": f"Bearer {cf_api_token}", "Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise BootstrapFlowError(
            f"Cloudflare API {method} {path} failed: HTTP {exc.code}\n"
            f"--- response body ---\n{body}"
        ) from exc
    except Exception as exc:
        raise BootstrapFlowError(f"Cloudflare API {method} {path} failed: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BootstrapFlowError(
            f"Cloudflare API {method} {path} returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise BootstrapFlowError(f"Cloudflare API {method} {path} returned invalid payload")
    if not parsed.get("success"):
        errors = parsed.get("errors")
        raise BootstrapFlowError(
            f"Cloudflare API {method} {path} reported failure\n"
            f"--- errors ---\n{json.dumps(errors, indent=2)}"
        )
    return parsed


def _load_cf_resources() -> dict[str, Any]:
    if not CF_RESOURCES_FILE.exists() or not CF_RESOURCES_FILE.is_file():
        return {}
    try:
        parsed = json.loads(CF_RESOURCES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise BootstrapFlowError(f"Failed to read {CF_RESOURCES_FILE}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise BootstrapFlowError(f"{CF_RESOURCES_FILE} must contain a JSON object")
    return parsed


def _write_cf_resources(payload: dict[str, Any]) -> None:
    try:
        fd = os.open(str(CF_RESOURCES_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
    except OSError as exc:
        raise BootstrapFlowError(f"Failed to write {CF_RESOURCES_FILE}: {exc}") from exc
    ok(f"Cloudflare resource manifest written via {CF_RESOURCES_FILE}")


def _clear_cf_resources() -> None:
    try:
        if CF_RESOURCES_FILE.exists():
            CF_RESOURCES_FILE.unlink()
    except OSError as exc:
        raise BootstrapFlowError(f"Failed to remove {CF_RESOURCES_FILE}: {exc}") from exc


def _cf_create_tunnel(cf_api_token: str, account_id: str, tunnel_name: str) -> tuple[str, str]:
    parsed = _cf_api_request(
        "POST",
        "/cfd_tunnel",
        cf_api_token,
        account_id=account_id,
        payload={"name": tunnel_name, "config_src": "cloudflare"},
    )
    result = parsed.get("result") or {}
    tunnel_id = str(result.get("id", "")).strip()
    tunnel_token = str(result.get("token", "")).strip()
    if not tunnel_id or not tunnel_token:
        raise BootstrapFlowError("Cloudflare Tunnel create response missing id or token")
    return tunnel_id, tunnel_token


def _cf_find_tunnel_by_name(cf_api_token: str, account_id: str, tunnel_name: str) -> str | None:
    parsed = _cf_api_request("GET", "/cfd_tunnel", cf_api_token, account_id=account_id)
    result = parsed.get("result") or []
    if not isinstance(result, list):
        raise BootstrapFlowError("Cloudflare Tunnel list returned invalid payload")
    for entry in result:
        if isinstance(entry, dict) and str(entry.get("name", "")).strip() == tunnel_name:
            tunnel_id = str(entry.get("id", "")).strip()
            if tunnel_id:
                return tunnel_id
    return None


def _cf_create_dns_cname(cf_api_token: str, zone_id: str, hostname: str, tunnel_id: str) -> str:
    parsed = _cf_api_request(
        "POST",
        "/dns_records",
        cf_api_token,
        zone_id=zone_id,
        payload={
            "type": "CNAME",
            "name": hostname,
            "content": f"{tunnel_id}.cfargotunnel.com",
            "proxied": True,
        },
    )
    result = parsed.get("result") or {}
    dns_record_id = str(result.get("id", "")).strip()
    if not dns_record_id:
        raise BootstrapFlowError("Cloudflare DNS create response missing id")
    return dns_record_id


def _cf_create_access_app(cf_api_token: str, account_id: str, app_name: str, hostname: str) -> str:
    parsed = _cf_api_request(
        "POST",
        "/access/apps",
        cf_api_token,
        account_id=account_id,
        payload={"name": app_name, "domain": hostname, "type": "self_hosted"},
    )
    result = parsed.get("result") or {}
    access_app_id = str(result.get("id", "")).strip()
    if not access_app_id:
        raise BootstrapFlowError("Cloudflare Access app create response missing id")
    return access_app_id


def _cf_create_access_policy(
    cf_api_token: str,
    account_id: str,
    access_app_id: str,
    policy_name: str,
    *,
    decision: str = "non_identity",
    include: list[dict[str, Any]] | None = None,
) -> str:
    parsed = _cf_api_request(
        "POST",
        f"/access/apps/{access_app_id}/policies",
        cf_api_token,
        account_id=account_id,
        payload={
            "name": policy_name,
            "decision": decision,
            "include": include if include is not None else [{"any_valid_service_token": {}}],
            "session_duration": "24h",
        },
    )
    result = parsed.get("result") or {}
    access_policy_id = str(result.get("id", "")).strip()
    if not access_policy_id:
        raise BootstrapFlowError("Cloudflare Access policy create response missing id")
    return access_policy_id


def _cf_create_service_token(
    cf_api_token: str,
    account_id: str,
    token_name: str,
) -> tuple[str, str, str]:
    parsed = _cf_api_request(
        "POST",
        "/access/service_tokens",
        cf_api_token,
        account_id=account_id,
        payload={"name": token_name},
    )
    result = parsed.get("result") or {}
    service_token_id = str(result.get("id", "")).strip()
    client_id = str(result.get("client_id", "")).strip()
    client_secret = str(result.get("client_secret", "")).strip()
    if not service_token_id or not client_id or not client_secret:
        raise BootstrapFlowError("Cloudflare Access service token create response missing required fields")
    return service_token_id, client_id, client_secret


def _b64url_no_pad(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _generate_janus_vapid_material() -> tuple[str, str]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    numbers = private_key.private_numbers()
    public_numbers = numbers.public_numbers
    x = public_numbers.x.to_bytes(32, "big")
    y = public_numbers.y.to_bytes(32, "big")
    d = numbers.private_value.to_bytes(32, "big")
    public_key = b"\x04" + x + y
    jwk = {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64url_no_pad(x),
        "y": _b64url_no_pad(y),
        "d": _b64url_no_pad(d),
    }
    return json.dumps(jwk, separators=(",", ":")), _b64url_no_pad(public_key)


def _ensure_janus_access_bypass(cf_creds: dict[str, str], worker_host: str) -> None:
    manifest = _load_cf_resources()
    manifest_changed = False
    action_specs = [
        ("approve", "/janus/approve/*"),
        ("deny", "/janus/deny/*"),
    ]
    for action, path in action_specs:
        app_id_key = f"janus_{action}_access_app_id"
        policy_id_key = f"janus_{action}_access_policy_id"
        app_name = _default_cf_janus_access_app_name(cf_creds["CF_WORKER_NAME"], action)
        domain = f"{worker_host}{path}"
        access_app_id = str(manifest.get(app_id_key, "")).strip()
        if not access_app_id or not _cf_object_exists(
            f"/access/apps/{access_app_id}",
            cf_creds["CF_API_TOKEN"],
            account_id=cf_creds["CF_ACCOUNT_ID"],
        ):
            access_app_id = _cf_create_access_app(
                cf_creds["CF_API_TOKEN"],
                cf_creds["CF_ACCOUNT_ID"],
                app_name,
                domain,
            )
            ok(f"Created Cloudflare Access janus app {access_app_id} for {domain}")
            manifest[app_id_key] = access_app_id
            manifest_changed = True
        else:
            info(f"Reusing tracked Cloudflare Access janus app {access_app_id} for {domain}")

        access_policy_id = str(manifest.get(policy_id_key, "")).strip()
        if not access_policy_id or not _cf_object_exists(
            f"/access/apps/{access_app_id}/policies/{access_policy_id}",
            cf_creds["CF_API_TOKEN"],
            account_id=cf_creds["CF_ACCOUNT_ID"],
        ):
            access_policy_id = _cf_create_access_policy(
                cf_creds["CF_API_TOKEN"],
                cf_creds["CF_ACCOUNT_ID"],
                access_app_id,
                f"{app_name}-bypass",
                decision="bypass",
                include=[{"everyone": {}}],
            )
            ok(f"Created Cloudflare Access janus bypass policy {access_policy_id} for {domain}")
            manifest[policy_id_key] = access_policy_id
            manifest_changed = True
        else:
            info(f"Reusing tracked Cloudflare Access janus policy {access_policy_id} for {domain}")

    if manifest_changed:
        _write_cf_resources(manifest)


def _cf_delete_tunnel(cf_api_token: str, account_id: str, tunnel_id: str) -> None:
    try:
        _cf_api_request("DELETE", f"/cfd_tunnel/{tunnel_id}", cf_api_token, account_id=account_id)
    except BootstrapFlowError as exc:
        if "HTTP 404" in str(exc):
            return
        raise


def _cf_delete_dns_record(cf_api_token: str, zone_id: str, dns_record_id: str) -> None:
    try:
        _cf_api_request("DELETE", f"/dns_records/{dns_record_id}", cf_api_token, zone_id=zone_id)
    except BootstrapFlowError as exc:
        if "HTTP 404" in str(exc):
            return
        raise


def _cf_delete_access_app(cf_api_token: str, account_id: str, access_app_id: str) -> None:
    try:
        _cf_api_request("DELETE", f"/access/apps/{access_app_id}", cf_api_token, account_id=account_id)
    except BootstrapFlowError as exc:
        if "HTTP 404" in str(exc):
            return
        raise


def _cf_delete_access_policy(cf_api_token: str, account_id: str, access_policy_id: str) -> None:
    resources = _load_cf_resources()
    access_app_id = str(resources.get("access_app_id", "")).strip()
    if not access_app_id:
        raise BootstrapFlowError("Cloudflare resource manifest missing access_app_id for policy delete")
    try:
        _cf_api_request(
            "DELETE",
            f"/access/apps/{access_app_id}/policies/{access_policy_id}",
            cf_api_token,
            account_id=account_id,
        )
    except BootstrapFlowError as exc:
        if "HTTP 404" in str(exc):
            return
        raise


def _cf_delete_service_token(cf_api_token: str, account_id: str, service_token_id: str) -> None:
    try:
        _cf_api_request(
            "DELETE",
            f"/access/service_tokens/{service_token_id}",
            cf_api_token,
            account_id=account_id,
        )
    except BootstrapFlowError as exc:
        if "HTTP 404" in str(exc):
            return
        raise


def _stop_cloudflared_if_running() -> None:
    docker_path = shutil.which("docker")
    if not docker_path:
        info("docker not available in bootstrap runtime; assuming host wrapper already stopped cloudflared")
        return
    result = subprocess.run(
        [docker_path, "compose", "stop", "cloudflared"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        info("Stopped cloudflared before Tunnel delete")
        return
    combined = f"{result.stdout}\n{result.stderr}".strip()
    if "No such service" in combined or "not running" in combined:
        info("cloudflared not running; nothing to stop before Tunnel delete")
        return
    raise BootstrapFlowError(
        "Failed to stop cloudflared before Tunnel delete\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )


def _worker_control_headers(setup_token: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {setup_token}",
        "Content-Type": "application/json",
        "User-Agent": "curl/8.5.0",
    }
    access_client_id = _read_runtime_credential_value("CF_ACCESS_CLIENT_ID")
    access_client_secret = _read_runtime_credential_value("CF_ACCESS_CLIENT_SECRET")
    if access_client_id and access_client_secret:
        headers["CF-Access-Client-Id"] = access_client_id
        headers["CF-Access-Client-Secret"] = access_client_secret
    return headers


def _sanitize_cf_name_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9-]+", "-", value.strip()).strip("-")
    return cleaned or "subumbra"


def _default_cf_tunnel_name(cf_worker_name: str) -> str:
    return f"{_sanitize_cf_name_component(cf_worker_name)}-tunnel"


def _default_cf_access_app_name(cf_worker_name: str) -> str:
    return f"{_sanitize_cf_name_component(cf_worker_name)}-worker-access"


def _default_cf_service_token_name(cf_worker_name: str) -> str:
    return f"{_sanitize_cf_name_component(cf_worker_name)}-service-token"


def _default_cf_janus_access_app_name(cf_worker_name: str, action: str) -> str:
    return f"{_sanitize_cf_name_component(cf_worker_name)}-gate-{action}"


def _load_cf_autoprovision_from_sources(
    *,
    runtime_creds: dict[str, str],
    cf_worker_name: str,
) -> dict[str, str]:
    zone_id = os.environ.get("CF_ZONE_ID", "").strip()
    tunnel_hostname = os.environ.get("CF_TUNNEL_HOSTNAME", "").strip()
    tunnel_name = os.environ.get("CF_TUNNEL_NAME", "").strip()
    access_app_name = os.environ.get("CF_ACCESS_APP_NAME", "").strip()
    service_token_name = os.environ.get("CF_SERVICE_TOKEN_NAME", "").strip()

    tunnel_requested = bool(tunnel_hostname and not runtime_creds.get("TUNNEL_TOKEN", "").strip())
    access_requested = bool(
        not (
            runtime_creds.get("CF_ACCESS_CLIENT_ID", "").strip()
            and runtime_creds.get("CF_ACCESS_CLIENT_SECRET", "").strip()
        )
        and (access_app_name or service_token_name or tunnel_requested)
    )

    if not tunnel_requested and not access_requested:
        return {}

    if (tunnel_requested or access_requested) and not zone_id:
        die(
            "CF_ZONE_ID is required when Cloudflare auto-provisioning is requested.\n"
            "  Provide CF_ZONE_ID in .env.bootstrap or interactive input, or supply BYOC runtime secrets instead."
        )

    if tunnel_requested and not tunnel_hostname:
        die(
            "CF_TUNNEL_HOSTNAME is required when Cloudflare Tunnel auto-provisioning is requested.\n"
            "  Provide CF_TUNNEL_HOSTNAME or supply TUNNEL_TOKEN manually."
        )

    return {
        "CF_ZONE_ID": zone_id,
        "CF_TUNNEL_HOSTNAME": tunnel_hostname,
        "CF_TUNNEL_NAME": tunnel_name or _default_cf_tunnel_name(cf_worker_name),
        "CF_ACCESS_APP_NAME": access_app_name or _default_cf_access_app_name(cf_worker_name),
        "CF_SERVICE_TOKEN_NAME": service_token_name or _default_cf_service_token_name(cf_worker_name),
        "AUTO_PROVISION_TUNNEL": "1" if tunnel_requested else "0",
        "AUTO_PROVISION_ACCESS": "1" if access_requested else "0",
    }


def _cf_object_exists(
    path: str,
    cf_api_token: str,
    *,
    account_id: str | None = None,
    zone_id: str | None = None,
) -> bool:
    try:
        _cf_api_request(
            "GET",
            path,
            cf_api_token,
            account_id=account_id,
            zone_id=zone_id,
        )
        return True
    except BootstrapFlowError as exc:
        if "HTTP 404" in str(exc):
            return False
        raise


def _clear_cloudflare_runtime_creds() -> None:
    _sync_host_env_file(
        {
            "TUNNEL_TOKEN": "",
            "CF_ACCESS_CLIENT_ID": "",
            "CF_ACCESS_CLIENT_SECRET": "",
        }
    )


def _provision_cloudflare_resources(
    cf_creds: dict[str, str],
    cf_autoprovision: dict[str, str],
    cf_runtime_creds: dict[str, str],
) -> tuple[dict[str, str], dict[str, Any]]:
    if not cf_autoprovision:
        return cf_runtime_creds, _load_cf_resources()

    cf_api_token = cf_creds["CF_API_TOKEN"]
    account_id = cf_creds["CF_ACCOUNT_ID"]
    zone_id = cf_autoprovision["CF_ZONE_ID"]
    tunnel_name = cf_autoprovision["CF_TUNNEL_NAME"]
    tunnel_hostname = cf_autoprovision["CF_TUNNEL_HOSTNAME"]
    access_app_name = cf_autoprovision["CF_ACCESS_APP_NAME"]
    service_token_name = cf_autoprovision["CF_SERVICE_TOKEN_NAME"]
    auto_tunnel = cf_autoprovision.get("AUTO_PROVISION_TUNNEL") == "1"
    auto_access = cf_autoprovision.get("AUTO_PROVISION_ACCESS") == "1"
    worker_url = _read_env_file_value(HOST_ENV_FILE, "CF_WORKER_URL").strip()
    worker_host = urllib.parse.urlparse(worker_url).hostname or ""
    if auto_access and not worker_host:
        raise BootstrapFlowError(
            "Cannot auto-provision Cloudflare Access without CF_WORKER_URL in host .env"
        )

    manifest = _load_cf_resources()
    manifest_changed = False
    tunnel_created = False
    dns_created = False
    access_app_created = False
    access_policy_created = False
    service_token_created = False
    wrote_tunnel_token = False
    wrote_access_creds = False

    tunnel_id = str(manifest.get("tunnel_id", "")).strip()
    dns_record_id = str(manifest.get("dns_record_id", "")).strip()
    access_app_id = str(manifest.get("access_app_id", "")).strip()
    access_policy_id = str(manifest.get("access_policy_id", "")).strip()
    service_token_id = str(manifest.get("service_token_id", "")).strip()

    if auto_tunnel:
        if tunnel_id and _cf_object_exists(f"/cfd_tunnel/{tunnel_id}", cf_api_token, account_id=account_id):
            info(f"Reusing tracked Cloudflare Tunnel {tunnel_id}")
            if not _read_runtime_credential_value("TUNNEL_TOKEN"):
                raise BootstrapFlowError(
                    "Cloudflare Tunnel manifest entry exists but TUNNEL_TOKEN is absent from .env.\n"
                    "  Supply TUNNEL_TOKEN manually or run ./bootstrap.sh --nuke-cloudflare."
                )
        else:
            tunnel_id = ""
            found_tunnel_id = _cf_find_tunnel_by_name(cf_api_token, account_id, tunnel_name)
            if found_tunnel_id:
                if not _read_runtime_credential_value("TUNNEL_TOKEN"):
                    raise BootstrapFlowError(
                        "A Cloudflare Tunnel with the requested name already exists, but its token cannot be recovered.\n"
                        "  Supply TUNNEL_TOKEN manually or run ./bootstrap.sh --nuke-cloudflare."
                    )
                tunnel_id = found_tunnel_id
                manifest_changed = True
                info(f"Adopted existing Cloudflare Tunnel by name: {tunnel_id}")
            else:
                tunnel_id, tunnel_token = _cf_create_tunnel(cf_api_token, account_id, tunnel_name)
                ok(f"Created Cloudflare Tunnel {tunnel_id}")
                try:
                    _sync_host_env_file({"TUNNEL_TOKEN": tunnel_token})
                except SystemExit as exc:
                    raise BootstrapFlowError(
                        "Failed to persist generated TUNNEL_TOKEN to host .env.\n"
                        f"  Orphaned tunnel_id={tunnel_id}\n"
                        "  Cloudflare does not re-display Tunnel tokens after creation."
                    ) from exc
                cf_runtime_creds["TUNNEL_TOKEN"] = tunnel_token
                tunnel_created = True
                wrote_tunnel_token = True
                manifest_changed = True
            manifest["tunnel_id"] = tunnel_id
            manifest["zone_id"] = zone_id
            manifest["tunnel_name"] = tunnel_name
            manifest["tunnel_hostname"] = tunnel_hostname

        if dns_record_id and _cf_object_exists(f"/dns_records/{dns_record_id}", cf_api_token, zone_id=zone_id):
            info(f"Reusing tracked Cloudflare DNS record {dns_record_id}")
        elif tunnel_id:
            try:
                dns_record_id = _cf_create_dns_cname(cf_api_token, zone_id, tunnel_hostname, tunnel_id)
            except BootstrapFlowError:
                if tunnel_created:
                    _cf_delete_tunnel(cf_api_token, account_id, tunnel_id)
                    if wrote_tunnel_token:
                        _sync_host_env_file({"TUNNEL_TOKEN": ""})
                    cf_runtime_creds.pop("TUNNEL_TOKEN", None)
                raise
            ok(f"Created Cloudflare DNS record {dns_record_id}")
            dns_created = True
            manifest_changed = True
            manifest["dns_record_id"] = dns_record_id

    if auto_access:
        if access_app_id and _cf_object_exists(f"/access/apps/{access_app_id}", cf_api_token, account_id=account_id):
            info(f"Reusing tracked Cloudflare Access app {access_app_id}")
        else:
            try:
                access_app_id = _cf_create_access_app(cf_api_token, account_id, access_app_name, worker_host)
            except BootstrapFlowError:
                if dns_created:
                    _cf_delete_dns_record(cf_api_token, zone_id, dns_record_id)
                if tunnel_created:
                    _cf_delete_tunnel(cf_api_token, account_id, tunnel_id)
                    _sync_host_env_file({"TUNNEL_TOKEN": ""})
                    cf_runtime_creds.pop("TUNNEL_TOKEN", None)
                raise
            ok(f"Created Cloudflare Access app {access_app_id}")
            access_app_created = True
            manifest_changed = True
            manifest["access_app_id"] = access_app_id
            manifest["access_app_name"] = access_app_name

        if access_policy_id and _cf_object_exists(
            f"/access/apps/{access_app_id}/policies/{access_policy_id}",
            cf_api_token,
            account_id=account_id,
        ):
            info(f"Reusing tracked Cloudflare Access policy {access_policy_id}")
        else:
            try:
                access_policy_id = _cf_create_access_policy(
                    cf_api_token,
                    account_id,
                    access_app_id,
                    f"{access_app_name}-service-auth",
                )
            except BootstrapFlowError:
                if access_app_created:
                    _cf_delete_access_app(cf_api_token, account_id, access_app_id)
                if dns_created:
                    _cf_delete_dns_record(cf_api_token, zone_id, dns_record_id)
                if tunnel_created:
                    _cf_delete_tunnel(cf_api_token, account_id, tunnel_id)
                clear_map: dict[str, str] = {}
                if wrote_tunnel_token:
                    clear_map["TUNNEL_TOKEN"] = ""
                    cf_runtime_creds.pop("TUNNEL_TOKEN", None)
                if wrote_access_creds:
                    clear_map["CF_ACCESS_CLIENT_ID"] = ""
                    clear_map["CF_ACCESS_CLIENT_SECRET"] = ""
                    cf_runtime_creds.pop("CF_ACCESS_CLIENT_ID", None)
                    cf_runtime_creds.pop("CF_ACCESS_CLIENT_SECRET", None)
                if clear_map:
                    _sync_host_env_file(clear_map)
                raise
            ok(f"Created Cloudflare Access service_auth policy {access_policy_id}")
            access_policy_created = True
            manifest_changed = True
            manifest["access_policy_id"] = access_policy_id
            _write_cf_resources(manifest)

        if service_token_id and _cf_object_exists(
            f"/access/service_tokens/{service_token_id}",
            cf_api_token,
            account_id=account_id,
        ):
            info(f"Reusing tracked Cloudflare Access service token {service_token_id}")
            existing_secret = _read_runtime_credential_value("CF_ACCESS_CLIENT_SECRET")
            if not existing_secret:
                raise BootstrapFlowError(
                    "Cloudflare Access service token exists in manifest, but CF_ACCESS_CLIENT_SECRET is absent from .env.\n"
                    "  Recreate the token or run ./bootstrap.sh --nuke-cloudflare."
                )
            cf_runtime_creds["CF_ACCESS_CLIENT_ID"] = _read_runtime_credential_value("CF_ACCESS_CLIENT_ID")
            cf_runtime_creds["CF_ACCESS_CLIENT_SECRET"] = existing_secret
        else:
            try:
                service_token_id, client_id, client_secret = _cf_create_service_token(
                    cf_api_token,
                    account_id,
                    service_token_name,
                )
            except BootstrapFlowError:
                if access_policy_created:
                    manifest["access_app_id"] = access_app_id
                    _cf_delete_access_policy(cf_api_token, account_id, access_policy_id)
                if access_app_created:
                    _cf_delete_access_app(cf_api_token, account_id, access_app_id)
                if dns_created:
                    _cf_delete_dns_record(cf_api_token, zone_id, dns_record_id)
                if tunnel_created:
                    _cf_delete_tunnel(cf_api_token, account_id, tunnel_id)
                clear_map: dict[str, str] = {}
                if wrote_tunnel_token:
                    clear_map["TUNNEL_TOKEN"] = ""
                    cf_runtime_creds.pop("TUNNEL_TOKEN", None)
                if wrote_access_creds:
                    clear_map["CF_ACCESS_CLIENT_ID"] = ""
                    clear_map["CF_ACCESS_CLIENT_SECRET"] = ""
                    cf_runtime_creds.pop("CF_ACCESS_CLIENT_ID", None)
                    cf_runtime_creds.pop("CF_ACCESS_CLIENT_SECRET", None)
                if clear_map:
                    _sync_host_env_file(clear_map)
                raise
            ok(f"Created Cloudflare Access service token {service_token_id}")
            try:
                _sync_host_env_file(
                    {
                        "CF_ACCESS_CLIENT_ID": client_id,
                        "CF_ACCESS_CLIENT_SECRET": client_secret,
                    }
                )
            except SystemExit as exc:
                raise BootstrapFlowError(
                    "Failed to persist generated CF Access service token to host .env.\n"
                    f"  Orphaned access_app_id={access_app_id}\n"
                    f"  Orphaned access_policy_id={access_policy_id}\n"
                    f"  Orphaned service_token_id={service_token_id}\n"
                    "  Cloudflare does not re-display Access service token secrets after creation."
                ) from exc
            cf_runtime_creds["CF_ACCESS_CLIENT_ID"] = client_id
            cf_runtime_creds["CF_ACCESS_CLIENT_SECRET"] = client_secret
            service_token_created = True
            wrote_access_creds = True
            manifest_changed = True
            manifest["service_token_id"] = service_token_id
            manifest["service_token_name"] = service_token_name

    if manifest_changed:
        manifest["zone_id"] = zone_id
        _write_cf_resources(manifest)
    return cf_runtime_creds, manifest


def run_nuke_cloudflare() -> None:
    print(BANNER, flush=True)
    step("Nuke Cloudflare-managed Tunnel / DNS / Access resources")
    manifest = _load_cf_resources()
    if not manifest:
        die(f"No Cloudflare resource manifest found at {CF_RESOURCES_FILE}; refusing to act.")

    cf_creds = _get_push_registry_cf_creds()
    zone_id = str(manifest.get("zone_id", "")).strip()
    if not zone_id:
        die(f"Cloudflare resource manifest {CF_RESOURCES_FILE} is missing zone_id")

    _stop_cloudflared_if_running()

    service_token_id = str(manifest.get("service_token_id", "")).strip()
    access_policy_id = str(manifest.get("access_policy_id", "")).strip()
    access_app_id = str(manifest.get("access_app_id", "")).strip()
    dns_record_id = str(manifest.get("dns_record_id", "")).strip()
    tunnel_id = str(manifest.get("tunnel_id", "")).strip()

    if service_token_id:
        _cf_delete_service_token(cf_creds["CF_API_TOKEN"], cf_creds["CF_ACCOUNT_ID"], service_token_id)
        ok(f"Deleted Cloudflare Access service token {service_token_id}")
    if access_policy_id:
        manifest["access_app_id"] = access_app_id
        _write_cf_resources(manifest)
        _cf_delete_access_policy(cf_creds["CF_API_TOKEN"], cf_creds["CF_ACCOUNT_ID"], access_policy_id)
        ok(f"Deleted Cloudflare Access policy {access_policy_id}")
    if access_app_id:
        _cf_delete_access_app(cf_creds["CF_API_TOKEN"], cf_creds["CF_ACCOUNT_ID"], access_app_id)
        ok(f"Deleted Cloudflare Access app {access_app_id}")
    if dns_record_id:
        _cf_delete_dns_record(cf_creds["CF_API_TOKEN"], zone_id, dns_record_id)
        ok(f"Deleted Cloudflare DNS record {dns_record_id}")
    if tunnel_id:
        last_error: BootstrapFlowError | None = None
        for attempt in range(5):
            try:
                _cf_delete_tunnel(cf_creds["CF_API_TOKEN"], cf_creds["CF_ACCOUNT_ID"], tunnel_id)
            except BootstrapFlowError as exc:
                last_error = exc
                if "active connection" in str(exc).lower() or "1022" in str(exc):
                    time.sleep(2)
                    continue
                raise
            else:
                ok(f"Deleted Cloudflare Tunnel {tunnel_id}")
                last_error = None
                break
        if last_error is not None:
            raise last_error

    _clear_cloudflare_runtime_creds()
    _clear_cf_resources()
    ok("Cleared Cloudflare runtime secrets from host .env")
    ok(f"Removed {CF_RESOURCES_FILE}")


# Maps both Subumbra canonical env var names AND common standalone-app aliases
# to their provider_id. Both sides must be supported so that migration from a
# standard LiteLLM .env (which uses ANTHROPIC_API_KEY) and the CI path (which
# uses ANTHROPIC_KEY) both work.
IMPORT_PROVIDER_WHITELIST: dict[str, str] = {
    # Subumbra canonical secret refs retained for legacy import discovery
    "ANTHROPIC_KEY":        "anthropic",
    "OPENAI_KEY":           "openai",
    "GROQ_KEY":             "groq",
    "DEEPSEEK_KEY":         "deepseek",
    "CEREBRAS_API_KEY":     "cerebras",
    "GEMINI_API_KEY":       "gemini",
    "GOOGLE_API_KEY":       "gemini",
    "MISTRAL_API_KEY":      "mistral",
    "OPENROUTER_API_KEY":   "openrouter",
    "TOGETHER_AI_API_KEY":  "together",
    "XAI_API_KEY":          "xai",
    "GITHUB_KEY":           "github",
    "SLACK_KEY":            "slack",
    "SENDGRID_KEY":         "sendgrid",
    # Common standalone-app aliases (LiteLLM .env, OpenWebUI, etc.)
    # 7 providers have mismatched names vs. Subumbra canonical
    "ANTHROPIC_API_KEY":    "anthropic",
    "OPENAI_API_KEY":       "openai",
    "GROQ_API_KEY":         "groq",
    "DEEPSEEK_API_KEY":     "deepseek",
    "TOGETHER_API_KEY":     "together",
    "GITHUB_TOKEN":         "github",
    "GITHUB_REST_KEY":      "github_rest",
    "STRIPE_TEST_KEY":      "stripe_test",
    "SLACK_BOT_TOKEN":      "slack",
    "SENDGRID_API_KEY":     "sendgrid",
}

# Vars to explicitly skip — app-internal secrets that must never be imported
# as provider keys. If detected, skip silently (do not warn or shred).
IMPORT_EXCLUSION_LIST: frozenset[str] = frozenset({
    "LITELLM_MASTER_KEY",
    "LITELLM_SALT_KEY",
    "WEBUI_SECRET_KEY",
    "N8N_ENCRYPTION_KEY",
    "DATABASE_URL",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "REDIS_URL",
    "SECRET_KEY",
    "JWT_SECRET",
})

def _cf_api_json(path: str, cf_creds: dict[str, str]) -> Any:
    request = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4{path}",
        headers={
            "Authorization": f"Bearer {cf_creds['CF_API_TOKEN']}",
            "Content-Type": "application/json",
        },
    )
    last_http_error: urllib.error.HTTPError | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_http_error = exc
            if exc.code in (401, 403, 429, 500, 502, 503, 504) and attempt < 3:
                info(f"Cloudflare API transient HTTP {exc.code}; retrying ({attempt}/3)")
                time.sleep(2)
                continue
            body_text = exc.read().decode("utf-8", errors="replace")
            die(
                f"Cloudflare API request failed with HTTP {exc.code}\n"
                f"--- response body ---\n{body_text}"
            )
        except urllib.error.URLError as exc:
            die(f"Cloudflare API request failed: {exc.reason}")
    if last_http_error is not None:
        body_text = last_http_error.read().decode("utf-8", errors="replace")
        die(
            f"Cloudflare API request failed after retries with HTTP {last_http_error.code}\n"
            f"--- response body ---\n{body_text}"
        )
    die("Cloudflare API request failed after retries")


def _cf_api_bytes(path: str, cf_creds: dict[str, str]) -> tuple[bytes, str]:
    request = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4{path}",
        headers={"Authorization": f"Bearer {cf_creds['CF_API_TOKEN']}"},
    )
    last_http_error: urllib.error.HTTPError | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            last_http_error = exc
            if exc.code in (401, 403, 429, 500, 502, 503, 504) and attempt < 3:
                info(f"Cloudflare content fetch transient HTTP {exc.code}; retrying ({attempt}/3)")
                time.sleep(2)
                continue
            body_text = exc.read().decode("utf-8", errors="replace")
            die(
                f"Cloudflare content fetch failed with HTTP {exc.code}\n"
                f"--- response body ---\n{body_text}"
            )
        except urllib.error.URLError as exc:
            die(f"Cloudflare content fetch failed: {exc.reason}")
    if last_http_error is not None:
        body_text = last_http_error.read().decode("utf-8", errors="replace")
        die(
            f"Cloudflare content fetch failed after retries with HTTP {last_http_error.code}\n"
            f"--- response body ---\n{body_text}"
        )
    die("Cloudflare content fetch failed after retries")


def _latest_worker_version_id(cf_creds: dict[str, str], worker_name: str) -> str:
    payload = _cf_api_json(
        f"/accounts/{cf_creds['CF_ACCOUNT_ID']}/workers/scripts/{worker_name}/deployments",
        cf_creds,
    )
    deployments = payload.get("result", payload).get("deployments")
    if not isinstance(deployments, list) or not deployments:
        die("no live worker deployment found after deploy")
    latest = deployments[0]
    versions = latest.get("versions")
    if isinstance(versions, list) and versions:
        version_id = versions[0].get("version_id")
        if isinstance(version_id, str) and version_id:
            return version_id
    version_id = latest.get("version_id")
    if isinstance(version_id, str) and version_id:
        return version_id
    die("unable to resolve live worker version after deploy")


def _extract_worker_hash_bytes(body: bytes, content_type: str) -> bytes:
    if "multipart/form-data" not in content_type.lower():
        return body
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    )
    parts = list(message.iter_parts())
    if not parts:
        return body
    entrypoint = message.get("cf-entrypoint")
    if entrypoint:
        for part in parts:
            filename = part.get_filename()
            if filename == entrypoint:
                payload = part.get_payload(decode=True)
                if payload is not None:
                    return payload
    for part in parts:
        payload = part.get_payload(decode=True)
        if payload is not None:
            return payload
    return body


def _fetch_live_worker_bundle_hash(cf_creds: dict[str, str], worker_name: str) -> str:
    version_id = _latest_worker_version_id(cf_creds, worker_name)
    body, content_type = _cf_api_bytes(
        f"/accounts/{cf_creds['CF_ACCOUNT_ID']}/workers/scripts/{worker_name}/content/v2?version={version_id}",
        cf_creds,
    )
    return hashlib.sha256(_extract_worker_hash_bytes(body, content_type)).hexdigest()



def _build_structured_kv_entries(
    keys_payload: dict[str, dict[str, Any]],
    existing_live_key_entries: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    published_policy_ids: set[str] = set()

    for key_id, record in sorted(keys_payload.items()):
        if _is_revoked_record(record):
            info(f"Skipping revoked record during structured publish: {key_id}")
            continue
        policy, consumers = _require_fat_record_fields(record, key_id)
        _verify_embedded_policy_hash(record, key_id)
        if record.get("type") == "ssh_key":
            key_entry = {
                "key_id": key_id,
                "type": "ssh_key",
                "key_source": record["key_source"],
                "algorithm": record["algorithm"],
                "public_key": record["public_key"],
                "vault_instance": record["vault_instance"],
                "policy_id": record["policy_id"],
                "policy_hash": record["policy_hash"],
                "policy": policy,
                "consumers": consumers,
                "created_at": record["created_at"],
                "status": record.get("status", "active"),
                "label": record["label"],
            }
            existing_live_entry = (existing_live_key_entries or {}).get(key_id)
            if isinstance(existing_live_entry, dict) and existing_live_entry.get("paused") is True:
                key_entry["paused"] = True
                info(f"Preserving paused flag during structured publish: {key_id}")
            entries.append({"key": f"key:{key_id}", "value": json.dumps(key_entry, separators=(",", ":"))})

            policy_id = policy["policy_id"]
            if policy_id not in published_policy_ids:
                entries.append(
                    {
                        "key": f"policy:{policy_id}",
                        "value": json.dumps(policy, separators=(",", ":")),
                    }
                )
                published_policy_ids.add(policy_id)
            continue

        provider_id = record["provider"]

        key_entry = {
            "key_id": key_id,
            "enc_version": record["enc_version"],
            "pub_key_fp": record["pub_key_fp"],
            "wrapped_dek": record["wrapped_dek"],
            "ciphertext": record["ciphertext"],
            "provider": provider_id,
            "target_host": record["target_host"],
            "policy_id": record["policy_id"],
            "policy_hash": record["policy_hash"],
            "created_at": record["created_at"],
            "label": record["label"],
        }
        if record.get("type") == "npm_token":
            key_entry["type"] = "npm_token"
        existing_live_entry = (existing_live_key_entries or {}).get(key_id)
        if isinstance(existing_live_entry, dict) and existing_live_entry.get("paused") is True:
            key_entry["paused"] = True
            info(f"Preserving paused flag during structured publish: {key_id}")
        entries.append({"key": f"key:{key_id}", "value": json.dumps(key_entry, separators=(",", ":"))})

        policy_id = policy["policy_id"]
        if policy_id not in published_policy_ids:
            entries.append(
                {
                    "key": f"policy:{policy_id}",
                    "value": json.dumps(policy, separators=(",", ":")),
                }
            )
            published_policy_ids.add(policy_id)

    return entries


def _kv_value_url(cf_creds: dict[str, str], namespace_id: str, key_name: str) -> str:
    quoted_key = urllib.parse.quote(key_name, safe="")
    return (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{cf_creds['CF_ACCOUNT_ID']}/storage/kv/namespaces/{namespace_id}/values/{quoted_key}"
    )


def _kv_auth_headers(cf_creds: dict[str, str]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cf_creds['CF_API_TOKEN']}",
        "Content-Type": "application/json",
    }


def _kv_get_json_value(cf_creds: dict[str, str], namespace_id: str, key_name: str) -> dict[str, Any] | None:
    request = urllib.request.Request(_kv_value_url(cf_creds, namespace_id, key_name), headers=_kv_auth_headers(cf_creds))
    last_http_error: urllib.error.HTTPError | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request) as resp:
                payload = resp.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            last_http_error = exc
            if exc.code in (401, 403, 429, 500, 502, 503, 504) and attempt < 3:
                info(f"Structured KV read transient HTTP {exc.code} for {key_name!r}; retrying ({attempt}/3)")
                time.sleep(2)
                continue
            body_text = exc.read().decode("utf-8", errors="replace")
            die(
                f"Failed to read structured KV key {key_name!r}: HTTP {exc.code}\n"
                f"--- response body ---\n{body_text}"
            )
        except Exception as exc:
            die(f"Failed to read structured KV key {key_name!r}: {exc}")
    else:
        if last_http_error is not None:
            body_text = last_http_error.read().decode("utf-8", errors="replace")
            die(
                f"Failed to read structured KV key {key_name!r} after retries: HTTP {last_http_error.code}\n"
                f"--- response body ---\n{body_text}"
            )
        die(f"Failed to read structured KV key {key_name!r} after retries")

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        die(f"Structured KV key {key_name!r} returned invalid JSON: {exc}")
    if not isinstance(parsed, dict):
        die(f"Structured KV key {key_name!r} returned invalid schema")
    return parsed


def _kv_wait_for_json_value(
    cf_creds: dict[str, str],
    namespace_id: str,
    key_name: str,
    *,
    max_attempts: int = 18,
    delay_seconds: int = 5,
) -> dict[str, Any]:
    info(
        f"Checking Cloudflare KV propagation for {key_name!r} "
        f"(eventual consistency; may take up to {max_attempts} attempts)"
    )
    for attempt in range(1, max_attempts + 1):
        parsed = _kv_get_json_value(cf_creds, namespace_id, key_name)
        if parsed is not None:
            return parsed
        if attempt < max_attempts:
            info(
                f"Cloudflare KV propagation delay for {key_name!r} is still normal; "
                f"rechecking consistency ({attempt}/{max_attempts})"
            )
            time.sleep(delay_seconds)
    die(
        f"Cloudflare KV key {key_name!r} did not become readable after publication.\n"
        f"  Consistency check exhausted {max_attempts} attempts."
    )


def _kv_put_value(cf_creds: dict[str, str], namespace_id: str, key_name: str, value: str) -> None:
    """Write a value to CF KV via the management API. Immediately consistent."""
    request = urllib.request.Request(
        _kv_value_url(cf_creds, namespace_id, key_name),
        method="PUT",
        data=value.encode("utf-8"),
        headers={**_kv_auth_headers(cf_creds), "Content-Type": "text/plain"},
    )
    last_http_error: urllib.error.HTTPError | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request) as resp:
                body = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            last_http_error = exc
            if exc.code in (401, 403, 429, 500, 502, 503, 504) and attempt < 3:
                info(f"Structured KV write transient HTTP {exc.code} for {key_name!r}; retrying ({attempt}/3)")
                time.sleep(2)
                continue
            body_text = exc.read().decode("utf-8", errors="replace")
            die(
                f"Failed to write structured KV key {key_name!r}: HTTP {exc.code}\n"
                f"--- response body ---\n{body_text}"
            )
        except Exception as exc:
            die(f"Failed to write structured KV key {key_name!r}: {exc}")
    else:
        if last_http_error is not None:
            body_text = last_http_error.read().decode("utf-8", errors="replace")
            die(
                f"Failed to write structured KV key {key_name!r} after retries: HTTP {last_http_error.code}\n"
                f"--- response body ---\n{body_text}"
            )
        die(f"Failed to write structured KV key {key_name!r} after retries")
    if not body.get("success"):
        die(f"Failed to write structured KV key {key_name!r}: {body}")


def _kv_delete_key(cf_creds: dict[str, str], namespace_id: str, key_name: str) -> None:
    request = urllib.request.Request(
        _kv_value_url(cf_creds, namespace_id, key_name),
        method="DELETE",
        headers=_kv_auth_headers(cf_creds),
    )
    try:
        with urllib.request.urlopen(request) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return
        body_text = exc.read().decode("utf-8", errors="replace")
        die(
            f"Failed to delete structured KV key {key_name!r}: HTTP {exc.code}\n"
            f"--- response body ---\n{body_text}"
        )
    except Exception as exc:
        die(f"Failed to delete structured KV key {key_name!r}: {exc}")
    if not body.get("success"):
        die(f"Failed to delete structured KV key {key_name!r}")



def _get_push_registry_cf_creds() -> dict[str, str]:
    if _has_cf_credentials():
        return {
            "CF_API_TOKEN": os.environ["CF_API_TOKEN"].strip(),
            "CF_ACCOUNT_ID": os.environ["CF_ACCOUNT_ID"].strip(),
            "CF_WORKER_NAME": _resolved_cf_worker_name_from_operator_context(),
        }

    if not sys.stdin.isatty():
        die(
            "Missing Cloudflare credentials for day-2 management.\n"
            "  Set CF_API_TOKEN and CF_ACCOUNT_ID in the environment (or use interactive ./bootstrap.sh).\n"
            "  Set CF_WORKER_NAME in the repo .env (host mount), or set CF_WORKER_URL to a *.workers.dev URL "
            "so the worker name can be inferred — day-2 commands do not prompt for the worker name.\n"
            "  For non-interactive CI, inject CF_API_TOKEN, CF_ACCOUNT_ID, and CF_WORKER_NAME into the container."
        )

    cf_token = os.environ.get("CF_API_TOKEN", "").strip()
    if not cf_token:
        while True:
            cf_token = _prompt_hidden_line("Cloudflare API token")
            if cf_token:
                break
            print("  ✗  API token cannot be empty.\n")
    cf_account_id = os.environ.get("CF_ACCOUNT_ID", "").strip()
    if not cf_account_id:
        while True:
            cf_account_id = _prompt_hidden_line("Cloudflare account ID")
            if cf_account_id:
                break
            print("  ✗  Account ID cannot be empty.\n")
    cf_worker_name = _resolved_cf_worker_name_from_operator_context()
    if not cf_worker_name:
        die(
            "CF_WORKER_NAME could not be resolved from the environment, "
            f"{HOST_ENV_FILE}, or CF_WORKER_URL.\n"
            "  Add e.g. CF_WORKER_NAME=subumbra-proxy (or your deployed name) to the repo .env and retry."
        )
    info(f"Using Cloudflare Worker name {cf_worker_name!r} from .env / environment (not prompted).")
    return {
        "CF_API_TOKEN":   cf_token,
        "CF_ACCOUNT_ID":  cf_account_id,
        "CF_WORKER_NAME": cf_worker_name,
    }


def _load_kv_namespace_id() -> str:
    try:
        with KV_CONFIG_FILE.open() as fh:
            namespace_id = json.load(fh)["namespace_id"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        die(f"Provider registry KV not initialized at {KV_CONFIG_FILE}: {exc}")
    if not isinstance(namespace_id, str) or not namespace_id.strip():
        die(f"Provider registry KV not initialized at {KV_CONFIG_FILE}: invalid namespace_id")
    return namespace_id


# ─────────────────────────────────────────────────────────────────────────────
# Automation fallback (CI / headless mode)
# ─────────────────────────────────────────────────────────────────────────────


def _wizard_collect_cf_access(cf_runtime_creds: dict[str, str]) -> None:
    """Collect CF Access client ID and secret interactively; skip if blank."""
    client_id = input("  CF Access client ID (leave blank to skip): ").strip()
    if not client_id:
        info("No CF Access client ID provided — skipping Access runtime credential")
        return
    client_secret = _prompt_hidden_line("CF Access client secret")
    if not client_secret:
        info("CF Access client secret blank — skipping Access runtime credential")
        return
    cf_runtime_creds["CF_ACCESS_CLIENT_ID"] = client_id
    cf_runtime_creds["CF_ACCESS_CLIENT_SECRET"] = client_secret
    ok("CF Access credentials captured (values not printed)")


# ─────────────────────────────────────────────────────────────────────────────
# CF Worker deployment
# ─────────────────────────────────────────────────────────────────────────────

def _run(cmd: list[str], *, cwd: Path, env: dict, input_text: str | None = None) -> str:
    """Run a subprocess, die with clear error on failure. Returns stdout."""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die(
            f"Command failed: {' '.join(cmd)}\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
    return result.stdout.strip()


def _persist_kv_namespace_config(namespace_id: str, title: str) -> str:
    with KV_CONFIG_FILE.open("w") as fh:
        json.dump({"namespace_id": namespace_id, "title": title}, fh, indent=2)
        fh.write("\n")
    return namespace_id


def _list_kv_namespaces(base_url: str, auth_headers: dict[str, str]) -> list[dict[str, Any]]:
    namespaces: list[dict[str, Any]] = []
    page = 1
    per_page = 1000

    while True:
        query = urllib.parse.urlencode({
            "page": page,
            "per_page": per_page,
            "order": "title",
            "direction": "asc",
        })
        list_req = urllib.request.Request(f"{base_url}?{query}", headers=auth_headers)
        try:
            with urllib.request.urlopen(list_req) as resp:
                list_result = json.loads(resp.read())
        except Exception as exc:
            die(f"Failed to list KV namespaces: {exc}")

        batch = list_result.get("result") or []
        if not isinstance(batch, list):
            die("Cloudflare KV list returned an invalid response payload")
        namespaces.extend(batch)

        result_info = list_result.get("result_info") or {}
        total_count = result_info.get("total_count")
        if isinstance(total_count, int):
            if len(namespaces) >= total_count:
                break
        elif len(batch) < per_page:
            break
        page += 1

    return namespaces


def _find_kv_namespace_by_title(
    base_url: str,
    auth_headers: dict[str, str],
    title: str,
) -> dict[str, Any] | None:
    for entry in _list_kv_namespaces(base_url, auth_headers):
        if entry.get("title") == title:
            return entry
    return None


def _create_or_reuse_kv_namespace(cf_creds: dict[str, str]) -> str:
    title = f"{cf_creds['CF_WORKER_NAME']}-PROVIDER_REGISTRY_KV"
    base_url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{cf_creds['CF_ACCOUNT_ID']}/storage/kv/namespaces"
    )
    auth_headers = {
        "Authorization": f"Bearer {cf_creds['CF_API_TOKEN']}",
        "Content-Type": "application/json",
    }

    # List existing namespaces and reuse if a matching title is found.
    existing = _list_kv_namespaces(base_url, auth_headers)
    saved_namespace_id = None
    if KV_CONFIG_FILE.exists():
        saved_namespace_id = _load_kv_namespace_id()
    if saved_namespace_id is not None:
        for entry in existing:
            if entry.get("id") == saved_namespace_id:
                return _persist_kv_namespace_config(saved_namespace_id, entry.get("title", title))
        warn(
            "Saved KV namespace ID missing from active Cloudflare account; falling back to title scan."
        )
    for entry in existing:
        if entry.get("title") == title:
            namespace_id = entry["id"]
            info(f"Reusing existing KV namespace: {title}")
            return _persist_kv_namespace_config(namespace_id, title)

    # No match found — create a new namespace.
    payload = json.dumps({"title": title}).encode()
    create_req = urllib.request.Request(
        base_url,
        data=payload,
        method="POST",
        headers=auth_headers,
    )

    try:
        with urllib.request.urlopen(create_req) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 400:
            existing_entry = _find_kv_namespace_by_title(base_url, auth_headers, title)
            if existing_entry is not None:
                namespace_id = existing_entry["id"]
                info(f"Reusing existing KV namespace after create conflict: {title}")
                return _persist_kv_namespace_config(namespace_id, title)
        die(
            f"Failed to create provider-registry KV namespace: HTTP {exc.code}\n"
            f"--- response body ---\n{body}"
        )
    except Exception as exc:
        die(f"Failed to create provider-registry KV namespace: {exc}")

    if not result.get("success") or "result" not in result or "id" not in result["result"]:
        die("Failed to create provider-registry KV namespace")

    namespace_id = result["result"]["id"]
    return _persist_kv_namespace_config(namespace_id, title)


def _append_provider_registry_kv_binding(wrangler_toml: Path, namespace_id: str) -> None:
    with wrangler_toml.open("a") as fh:
        fh.write(
            "\n[[kv_namespaces]]\n"
            'binding = "PROVIDER_REGISTRY_KV"\n'
            f'id = "{namespace_id}"\n'
        )


def _wrangler_env(cf_creds: dict[str, str]) -> dict[str, str]:
    return {
        **os.environ,
        "CLOUDFLARE_API_TOKEN": cf_creds["CF_API_TOKEN"],
        "CLOUDFLARE_ACCOUNT_ID": cf_creds["CF_ACCOUNT_ID"],
        "CI": "true",
    }


def _build_worker_url(worker_name: str, deploy_out: str | None = None) -> str:
    worker_url = f"https://{worker_name}.workers.dev"
    if not deploy_out:
        return worker_url
    for line in deploy_out.splitlines():
        for token in line.split():
            if token.startswith("https://") and "workers.dev" in token:
                return token.rstrip(".,")
    return worker_url


def _delete_worker_secret(cf_creds: dict[str, str], secret_name: str, *, quiet_missing: bool = False) -> None:
    if not WORKER_SRC.exists():
        die(
            f"Worker source not found at {WORKER_SRC}.\n"
            "  Ensure ./worker is mounted into the bootstrap container\n"
            "  (check volumes in docker-compose.yml)."
        )

    worker_name = cf_creds["CF_WORKER_NAME"]
    env = _wrangler_env(cf_creds)

    with tempfile.TemporaryDirectory(prefix="subumbra-worker-") as tmp:
        tmp_dir = Path(tmp)
        shutil.copytree(WORKER_SRC, tmp_dir / "worker")
        work_dir = tmp_dir / "worker"
        namespace_id = _create_or_reuse_kv_namespace(cf_creds)
        _append_provider_registry_kv_binding(work_dir / "wrangler.toml", namespace_id)

        result = subprocess.run(
            ["wrangler", "secret", "delete", secret_name, "--name", worker_name],
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            ok(f"Deleted {secret_name} secret")
            return
        if quiet_missing:
            info(f"{secret_name} not present — already clean")
            return
        die(
            f"Command failed: wrangler secret delete {secret_name} --name {worker_name}\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )


def _put_worker_secret(cf_creds: dict[str, str], secret_name: str, secret_value: str) -> None:
    if not WORKER_SRC.exists():
        die(
            f"Worker source not found at {WORKER_SRC}.\n"
            "  Ensure ./worker is mounted into the bootstrap container\n"
            "  (check volumes in docker-compose.yml)."
        )

    worker_name = cf_creds["CF_WORKER_NAME"]
    env = _wrangler_env(cf_creds)

    with tempfile.TemporaryDirectory(prefix="subumbra-worker-") as tmp:
        tmp_dir = Path(tmp)
        shutil.copytree(WORKER_SRC, tmp_dir / "worker")
        work_dir = tmp_dir / "worker"
        namespace_id = _create_or_reuse_kv_namespace(cf_creds)
        _append_provider_registry_kv_binding(work_dir / "wrangler.toml", namespace_id)

        _run(
            ["wrangler", "secret", "put", secret_name, "--name", worker_name],
            cwd=work_dir,
            env=env,
            input_text=secret_value + "\n",
        )
        ok(f"{secret_name} pushed")
    ok(f"{secret_name} pushed")


def call_setup_keygen(worker_url: str, setup_token: str, vault_instance: str) -> tuple[str, str, str]:
    last_http_error: urllib.error.HTTPError | None = None
    body = json.dumps({"vault_instance": vault_instance}, separators=(",", ":")).encode("utf-8")
    _MAX_KEYGEN_ATTEMPTS = 24
    for attempt in range(1, _MAX_KEYGEN_ATTEMPTS + 1):
        req = urllib.request.Request(
            f"{worker_url.rstrip('/')}/setup/keygen",
            data=body,
            method="POST",
            headers=_worker_control_headers(setup_token),
        )
        try:
            with urllib.request.urlopen(req) as resp:
                payload = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            last_http_error = exc
            if exc.code in (401, 403, 503) and attempt < _MAX_KEYGEN_ATTEMPTS:
                info(
                    "Cloudflare setup token not visible yet; "
                    f"retrying /setup/keygen ({attempt}/{_MAX_KEYGEN_ATTEMPTS})"
                )
                time.sleep(5)
                continue
            body = exc.read().decode("utf-8", errors="replace")
            raise BootstrapFlowError(
                f"Cloudflare setup keygen failed: HTTP {exc.code}\n"
                f"--- response body ---\n{body}"
            )
        except Exception as exc:
            raise BootstrapFlowError(f"Cloudflare setup keygen failed: {exc}") from exc
    else:
        if last_http_error is not None:
            body = last_http_error.read().decode("utf-8", errors="replace")
            raise BootstrapFlowError(
                f"Cloudflare setup keygen failed after retry window: HTTP {last_http_error.code}\n"
                f"--- response body ---\n{body}"
            )
        raise BootstrapFlowError("Cloudflare setup keygen failed after retry window")

    public_key_pem = payload.get("public_key_pem")
    pub_key_fp = payload.get("pub_key_fp")
    created_at = payload.get("created_at")
    if not all(isinstance(value, str) and value for value in (public_key_pem, pub_key_fp, created_at)):
        raise BootstrapFlowError("Cloudflare setup keygen returned an invalid response payload")
    return public_key_pem, pub_key_fp, created_at


def _call_internal_vault_status(worker_url: str, setup_token: str, vault_instance: str) -> bool:
    body = json.dumps({"vault_instance": vault_instance}, separators=(",", ":")).encode("utf-8")
    last_http_error: urllib.error.HTTPError | None = None
    max_attempts = 24
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            f"{worker_url.rstrip('/')}/internal/vault-status",
            data=body,
            method="POST",
            headers=_worker_control_headers(setup_token),
        )
        try:
            with urllib.request.urlopen(req) as resp:
                payload = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            last_http_error = exc
            if exc.code in (401, 403, 503) and attempt < max_attempts:
                info(
                    "Cloudflare status token not visible yet; "
                    f"retrying /internal/vault-status ({attempt}/{max_attempts})"
                )
                time.sleep(5)
                continue
            body_text = exc.read().decode("utf-8", errors="replace")
            raise BootstrapFlowError(
                f"Cloudflare vault status failed: HTTP {exc.code}\n"
                f"--- response body ---\n{body_text}"
            ) from exc
        except Exception as exc:
            raise BootstrapFlowError(f"Cloudflare vault status failed: {exc}") from exc
    else:
        if last_http_error is not None:
            body_text = last_http_error.read().decode("utf-8", errors="replace")
            raise BootstrapFlowError(
                f"Cloudflare vault status failed after retry window: HTTP {last_http_error.code}\n"
                f"--- response body ---\n{body_text}"
            )
        raise BootstrapFlowError("Cloudflare vault status failed after retry window")
    initialized = payload.get("initialized")
    if not isinstance(initialized, bool):
        raise BootstrapFlowError("Cloudflare vault status returned an invalid response payload")
    return initialized


def _call_internal_vault_reset(worker_url: str, setup_token: str, vault_instance: str) -> None:
    body = json.dumps({"vault_instance": vault_instance}, separators=(",", ":")).encode("utf-8")
    last_http_error: urllib.error.HTTPError | None = None
    max_attempts = 24
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            f"{worker_url.rstrip('/')}/internal/vault-reset",
            data=body,
            method="POST",
            headers=_worker_control_headers(setup_token),
        )
        try:
            with urllib.request.urlopen(req) as resp:
                payload = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            last_http_error = exc
            if exc.code in (401, 403, 503) and attempt < max_attempts:
                info(
                    "Cloudflare reset token not visible yet; "
                    f"retrying /internal/vault-reset ({attempt}/{max_attempts})"
                )
                time.sleep(5)
                continue
            body_text = exc.read().decode("utf-8", errors="replace")
            raise BootstrapFlowError(
                f"Cloudflare vault reset failed: HTTP {exc.code}\n"
                f"--- response body ---\n{body_text}"
            ) from exc
        except Exception as exc:
            raise BootstrapFlowError(f"Cloudflare vault reset failed: {exc}") from exc
    else:
        if last_http_error is not None:
            body_text = last_http_error.read().decode("utf-8", errors="replace")
            raise BootstrapFlowError(
                f"Cloudflare vault reset failed after retry window: HTTP {last_http_error.code}\n"
                f"--- response body ---\n{body_text}"
            )
        raise BootstrapFlowError("Cloudflare vault reset failed after retry window")
    if payload.get("status") != "ok":
        raise BootstrapFlowError("Cloudflare vault reset returned an invalid response payload")


def _delete_kv_namespace_if_present(cf_creds: dict[str, str]) -> None:
    if not KV_CONFIG_FILE.exists():
        return
    try:
        payload = json.loads(KV_CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        payload = {}
    namespace_id = str(payload.get("namespace_id", "")).strip() if isinstance(payload, dict) else ""
    if not namespace_id:
        return

    base_url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{cf_creds['CF_ACCOUNT_ID']}/storage/kv/namespaces/{namespace_id}"
    )
    auth_headers = {
        "Authorization": f"Bearer {cf_creds['CF_API_TOKEN']}",
        "Content-Type": "application/json",
    }
    delete_req = urllib.request.Request(base_url, method="DELETE", headers=auth_headers)
    try:
        with urllib.request.urlopen(delete_req) as resp:
            result = json.loads(resp.read())
        if not result.get("success"):
            die("Failed to delete provider-registry KV namespace")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            body_text = exc.read().decode("utf-8", errors="replace")
            die(
                f"Failed to delete provider-registry KV namespace: HTTP {exc.code}\n"
                f"--- response body ---\n{body_text}"
            )
    except Exception as exc:
        die(f"Failed to delete provider-registry KV namespace: {exc}")

    _delete_file_if_present(KV_CONFIG_FILE)


def _publish_structured_kv(
    cf_creds: dict[str, str],
    keys_payload: dict[str, dict[str, Any]],
) -> None:
    namespace_id = _create_or_reuse_kv_namespace(cf_creds)
    env = _wrangler_env(cf_creds)
    existing_live_key_entries: dict[str, dict[str, Any]] = {}
    for key_id, record in sorted(keys_payload.items()):
        if _is_revoked_record(record):
            continue
        live_entry = _kv_get_json_value(cf_creds, namespace_id, f"key:{key_id}")
        if isinstance(live_entry, dict):
            existing_live_key_entries[key_id] = live_entry
    entries = _build_structured_kv_entries(keys_payload, existing_live_key_entries)
    if not entries:
        die("No structured KV entries compiled for publication")

    with tempfile.TemporaryDirectory(prefix="subumbra-structured-kv-") as tmp:
        tmp_dir = Path(tmp)
        shutil.copytree(WORKER_SRC, tmp_dir / "worker")
        work_dir = tmp_dir / "worker"
        _append_provider_registry_kv_binding(work_dir / "wrangler.toml", namespace_id)

        payload_path = work_dir / "structured-kv.json"
        payload_path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")

        _run(
            [
                "wrangler", "kv", "bulk", "put",
                str(payload_path),
                "--namespace-id", namespace_id,
                "--remote",
            ],
            cwd=work_dir,
            env=env,
        )

        # Newly-added entries (key_id absent from existing_live_key_entries) may not
        # propajanus immediately after wrangler bulk put. Write them directly via the
        # management API (immediately consistent) to guarantee the Worker can see them.
        new_key_ids = {
            key_id
            for key_id, record in keys_payload.items()
            if not _is_revoked_record(record) and key_id not in existing_live_key_entries
        }
        if new_key_ids:
            new_policy_ids = {
                keys_payload[k].get("policy_id")
                for k in new_key_ids
                if k in keys_payload and keys_payload[k].get("policy_id")
            }
            new_kv_entries = {
                e["key"]: e["value"]
                for e in entries
                if (e["key"].startswith("key:") and e["key"][len("key:"):] in new_key_ids)
                or (e["key"].startswith("policy:") and e["key"][len("policy:"):] in new_policy_ids)
            }
            for kv_key, kv_value in new_kv_entries.items():
                print(f"  → direct KV write (new entry): {kv_key}")
                _kv_put_value(cf_creds, namespace_id, kv_key, kv_value)

        # Verify propagation: check newly-added key entries first; fall back to any key entry.
        check_key = next((f"key:{k}" for k in new_key_ids), None) or next((e["key"] for e in entries if e["key"].startswith("key:")), None)
        check_policy = next((f"policy:{keys_payload[k].get('policy_id', '')}" for k in new_key_ids if keys_payload.get(k, {}).get("policy_id")), None) or next((e["key"] for e in entries if e["key"].startswith("policy:")), None)

        if check_key:
            _kv_wait_for_json_value(cf_creds, namespace_id, check_key)
        if check_policy:
            _kv_wait_for_json_value(cf_creds, namespace_id, check_policy)

        _run(
            [
                "wrangler", "kv", "key", "put",
                "registry_version",
                STRUCTURED_KV_SCHEMA_VERSION,
                "--namespace-id", namespace_id,
                "--remote",
            ],
            cwd=work_dir,
            env=env,
        )


def deploy_worker(
    cf_creds: dict[str, str],
    consumer_tokens: dict[str, str],
    subumbra_hmac_key: str,
    management_token: str,
    setup_token: str,
    provider_id_filter: "set[str] | None" = None,
) -> str:
    """
    Deploy the CF Worker and push runtime/setup secrets. Returns the worker URL.

    Steps:
      1. Copy worker source to a temp dir (source mount is :ro)
      2. wrangler deploy --name <name>
      3. wrangler secret delete MASTER_DECRYPTION_KEY (V1 cleanup, best-effort)
      4. wrangler secret delete WORKER_PRIVATE_KEY (legacy cleanup, best-effort)
      5. wrangler secret delete WORKER_KEY_FINGERPRINT (legacy cleanup, best-effort)
      6. wrangler secret put SUBUMBRA_CONSUMER_TOKENS
      7. wrangler secret put SUBUMBRA_HMAC_KEY
      8. wrangler secret put SUBUMBRA_MANAGEMENT_TOKEN
      9. wrangler secret put SUBUMBRA_SETUP_TOKEN
    """
    if not WORKER_SRC.exists():
        die(
            f"Worker source not found at {WORKER_SRC}.\n"
            "  Ensure ./worker is mounted into the bootstrap container\n"
            "  (check volumes in docker-compose.yml)."
        )

    worker_name = cf_creds["CF_WORKER_NAME"]

    # Wrangler reads CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID from env
    env = _wrangler_env(cf_creds)

    with tempfile.TemporaryDirectory(prefix="subumbra-worker-") as tmp:
        tmp_dir = Path(tmp)
        shutil.copytree(WORKER_SRC, tmp_dir / "worker")
        work_dir = tmp_dir / "worker"
        namespace_id = _create_or_reuse_kv_namespace(cf_creds)
        _append_provider_registry_kv_binding(work_dir / "wrangler.toml", namespace_id)
        # ── deploy ────────────────────────────────────────────────────────────
        step(f"Deploying CF Worker '{worker_name}'")
        deploy_out = _run(
            ["wrangler", "deploy", "--name", worker_name],
            cwd=work_dir,
            env=env,
        )
        ok("Deployed")
        for line in deploy_out.splitlines():
            info(line)

        # ── delete stale V1 secret (best-effort) ─────────────────────────────
        step("Cleaning up stale MASTER_DECRYPTION_KEY (V1)")
        del_result = subprocess.run(
            ["wrangler", "secret", "delete", "MASTER_DECRYPTION_KEY",
             "--name", worker_name],
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        if del_result.returncode == 0:
            ok("Deleted stale MASTER_DECRYPTION_KEY secret")
        else:
            info("MASTER_DECRYPTION_KEY not present — already clean")

        # ── delete legacy custody secrets (best-effort) ──────────────────────
        for secret_name in ("WORKER_PRIVATE_KEY", "WORKER_KEY_FINGERPRINT"):
            step(f"Removing legacy {secret_name} secret")
            del_result = subprocess.run(
                ["wrangler", "secret", "delete", secret_name, "--name", worker_name],
                cwd=work_dir,
                env=env,
                capture_output=True,
                text=True,
            )
            if del_result.returncode == 0:
                ok(f"Deleted stale {secret_name} secret")
            else:
                info(f"{secret_name} not present — already clean")

        # ── push SUBUMBRA_CONSUMER_TOKENS ─────────────────────────────────────
        step("Pushing SUBUMBRA_CONSUMER_TOKENS to CF Secrets")
        consumer_tokens_json = json.dumps(
            [{"id": k, "token": v} for k, v in consumer_tokens.items()],
            separators=(",", ":"),
        )
        _run(
            ["wrangler", "secret", "put", "SUBUMBRA_CONSUMER_TOKENS",
             "--name", worker_name],
            cwd=work_dir,
            env=env,
            input_text=consumer_tokens_json + "\n",
        )
        ok("SUBUMBRA_CONSUMER_TOKENS pushed")

        # ── push SUBUMBRA_HMAC_KEY ────────────────────────────────────────────
        step("Pushing SUBUMBRA_HMAC_KEY to CF Secrets")
        _run(
            ["wrangler", "secret", "put", "SUBUMBRA_HMAC_KEY",
             "--name", worker_name],
            cwd=work_dir,
            env=env,
            input_text=subumbra_hmac_key + "\n",
        )
        ok("SUBUMBRA_HMAC_KEY pushed")

        step("Pushing SUBUMBRA_MANAGEMENT_TOKEN to CF Secrets")
        _run(
            ["wrangler", "secret", "put", "SUBUMBRA_MANAGEMENT_TOKEN",
             "--name", worker_name],
            cwd=work_dir,
            env=env,
            input_text=management_token + "\n",
        )
        ok("SUBUMBRA_MANAGEMENT_TOKEN pushed")

        # ── push transient SUBUMBRA_SETUP_TOKEN ──────────────────────────────
        step("Pushing transient SUBUMBRA_SETUP_TOKEN to CF Secrets")
        _run(
            ["wrangler", "secret", "put", "SUBUMBRA_SETUP_TOKEN",
             "--name", worker_name],
            cwd=work_dir,
            env=env,
            input_text=setup_token + "\n",
        )
        ok("SUBUMBRA_SETUP_TOKEN pushed")


        worker_url = _build_worker_url(worker_name, deploy_out)
        bundle_sha256 = _fetch_live_worker_bundle_hash(cf_creds, worker_name)
        _write_system_integrity(worker_name, worker_url, bundle_sha256)

    return worker_url


def run_push_registry() -> None:
    cf_creds = _get_push_registry_cf_creds()
    if not KEYS_FILE.exists():
        die("endpoint.json not found — cannot publish structured KV")

    try:
        keys_payload = json.loads(KEYS_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        die(f"Cannot read endpoint.json: {exc}")
    for key_id, record in keys_payload.items():
        _require_fat_record_fields(record, key_id)
        _verify_embedded_policy_hash(record, key_id)
        if record.get("type") == "ssh_key":
            continue
        target_host = record.get("target_host")
        if not isinstance(target_host, str) or not target_host:
            die(f"endpoint.json record {key_id!r} missing target_host")

    step("Publishing structured KV entries to Cloudflare KV")
    try:
        _publish_structured_kv(cf_creds, keys_payload)
    except SystemExit:
        die("structured publish aborted before registry_version update")
    ok("Structured KV publication complete")



def run_deploy_worker() -> None:
    """
    Redeploy the Cloudflare Worker code with the live PROVIDER_REGISTRY_KV
    binding injected from data/kv-config.json. Existing Worker secrets
    (SUBUMBRA_CONSUMER_TOKENS, SUBUMBRA_HMAC_KEY, SUBUMBRA_MANAGEMENT_TOKEN,
    SUBUMBRA_VAULT) and SQLite DO state are preserved — only the script
    bundle and its KV binding are updated. Use this after pulling a round
    that changes worker/src/worker.js (./bootstrap.sh --upgrade rebuilds
    local Docker images but does not push code to Cloudflare).
    """
    if not WORKER_SRC.exists():
        die(
            f"Worker source not found at {WORKER_SRC}.\n"
            "  Ensure ./worker is mounted into the bootstrap container\n"
            "  (check volumes in docker-compose.yml)."
        )

    cf_creds = _get_push_registry_cf_creds()
    worker_name = cf_creds["CF_WORKER_NAME"]
    env = _wrangler_env(cf_creds)

    with tempfile.TemporaryDirectory(prefix="subumbra-worker-") as tmp:
        tmp_dir = Path(tmp)
        shutil.copytree(WORKER_SRC, tmp_dir / "worker")
        work_dir = tmp_dir / "worker"
        namespace_id = _create_or_reuse_kv_namespace(cf_creds)
        _append_provider_registry_kv_binding(work_dir / "wrangler.toml", namespace_id)

        step(f"Deploying CF Worker '{worker_name}' (code + KV binding only)")
        deploy_out = _run(
            ["wrangler", "deploy", "--name", worker_name],
            cwd=work_dir,
            env=env,
        )
        ok("Deployed")
        for line in deploy_out.splitlines():
            info(line)

    info("Existing Worker secrets and Durable Object state were preserved.")
    info("If a round changed Worker secrets, run a full ./bootstrap.sh instead.")



def run_update_tunnel() -> None:
    """Day-2: update TUNNEL_TOKEN in the host .env without re-running bootstrap."""
    print(BANNER, flush=True)
    step("Update Cloudflare Tunnel token")
    existing = _read_env_file_value(HOST_ENV_FILE, "TUNNEL_TOKEN").strip()
    if existing:
        info("Existing TUNNEL_TOKEN detected in host .env")
    token = _prompt_hidden_line("new Cloudflare Tunnel token (leave blank to clear)")
    _sync_host_env_file({"TUNNEL_TOKEN": token})
    if token:
        ok("TUNNEL_TOKEN updated in host .env (value not printed)")
    else:
        ok("TUNNEL_TOKEN cleared from host .env")
    info("Restart the cloudflared container to pick up the new token: docker compose restart cloudflared")


def run_update_access() -> None:
    """Day-2: update CF Access service token in the host .env without re-running bootstrap."""
    print(BANNER, flush=True)
    step("Update Cloudflare Access service token")
    existing_id = _read_env_file_value(HOST_ENV_FILE, "CF_ACCESS_CLIENT_ID").strip()
    if existing_id:
        info("Existing CF_ACCESS_CLIENT_ID detected in host .env")
    client_id = input("  CF Access client ID (leave blank to clear both Access vars): ").strip()
    if not client_id:
        _sync_host_env_file({"CF_ACCESS_CLIENT_ID": "", "CF_ACCESS_CLIENT_SECRET": ""})
        ok("CF Access credentials cleared from host .env")
        return
    client_secret = _prompt_hidden_line("CF Access client secret")
    if not client_secret:
        die("CF Access client secret cannot be blank when client ID is provided")
    _sync_host_env_file({"CF_ACCESS_CLIENT_ID": client_id, "CF_ACCESS_CLIENT_SECRET": client_secret})
    ok("CF Access credentials updated in host .env (secret value not printed)")
    info("Restart affected containers to pick up new credentials: docker compose up -d --force-recreate")


def run_update_ui_auth() -> None:
    """Day-2: update UI auth state in the host .env without re-running bootstrap."""
    print(BANNER, flush=True)
    step("Update UI authentication")
    env_mode = os.environ.get("UI_AUTH_MODE", "").strip().lower()
    env_username = os.environ.get("UI_USERNAME", "").strip()
    env_password = os.environ.get("UI_PASSWORD", "").strip()

    if env_mode:
        if env_mode not in UI_AUTH_MODES:
            die("UI_AUTH_MODE must be one of: basic, cf_access, both")
        mode = env_mode
    else:
        print("  Auth mode:")
        print("    (1) Username / password")
        print("    (2) Cloudflare Access")
        print("    (3) Both")
        choice = input("  Choice [1]: ").strip() or "1"
        mode_map = {"1": "basic", "2": "cf_access", "3": "both"}
        if choice not in mode_map:
            die("Invalid UI auth choice. Expected 1, 2, or 3.")
        mode = mode_map[choice]

    updates = {
        "UI_USERNAME": "",
        "UI_PASSWORD_HASH": "",
        "CF_ACCESS_PROTECTED": "true" if mode in {"cf_access", "both"} else "false",
    }
    if mode in {"basic", "both"}:
        username = env_username or input("  UI username: ").strip()
        if not username:
            die("UI username cannot be blank when auth mode is basic or both")
        password = env_password or _prompt_hidden_line("UI password")
        if not password:
            die("UI password cannot be blank when auth mode is basic or both")
        updates["UI_USERNAME"] = username
        updates["UI_PASSWORD_HASH"] = hash_ui_password(password)

    _sync_host_env_file(updates)
    ok("UI auth settings updated in host .env (secret value not printed)")
    info("Restart the UI container to pick up the new credentials: docker compose --profile ui up -d subumbra-ui")


def run_update_gate() -> None:
    """Day-2: ensure Janus DO secrets, VAPID public key, and narrow Access bypass apps."""
    print(BANNER, flush=True)
    step("Ensure Janus runtime secrets and Access bypass")
    cf_creds = _get_push_registry_cf_creds()
    worker_url = _read_env_file_value(HOST_ENV_FILE, "CF_WORKER_URL").strip()
    worker_host = urllib.parse.urlparse(worker_url).hostname or ""
    if not worker_host:
        die(
            "Cannot configure janus runtime without CF_WORKER_URL in host .env.\n"
            "  Set CF_WORKER_URL first, then rerun ./bootstrap.sh --update-janus."
        )

    manifest = _load_cf_resources()
    janus_public_key = _read_env_file_value(HOST_ENV_FILE, "SUBUMBRA_JANUS_VAPID_PUBLIC_KEY").strip()
    if not manifest.get("janus_secrets_initialized") or not janus_public_key:
        janus_hmac_key = secrets.token_urlsafe(32)
        janus_private_jwk, janus_public_key = _generate_janus_vapid_material()
        step("Pushing SUBUMBRA_JANUS_HMAC_KEY to CF Secrets")
        _put_worker_secret(cf_creds, "SUBUMBRA_JANUS_HMAC_KEY", janus_hmac_key)
        step("Pushing SUBUMBRA_JANUS_VAPID_PRIVATE_JWK to CF Secrets")
        _put_worker_secret(cf_creds, "SUBUMBRA_JANUS_VAPID_PRIVATE_JWK", janus_private_jwk)
        _sync_host_env_file({"SUBUMBRA_JANUS_VAPID_PUBLIC_KEY": janus_public_key})
        manifest["janus_secrets_initialized"] = True
        _write_cf_resources(manifest)
        ok("Janus secrets initialized and public key written to host .env")
    else:
        info("Janus secrets already initialized; reusing existing host .env public key")

    _ensure_janus_access_bypass(cf_creds, worker_host)
    ok("Janus Access bypass paths ensured")
