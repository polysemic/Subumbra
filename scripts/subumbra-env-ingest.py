#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

IMPORT_EXCLUSION_LIST: frozenset[str] = frozenset(
    {
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
    }
)

CONSUMER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,61}[a-z0-9]$")
RUNNER_PREFIX_RE = re.compile(r"^RUNNER_")
SECRET_NAME_RE = re.compile(r"(?:^|_)(?:API_)?(?:KEY|TOKEN|PAT|SECRET)$")
NON_PROVIDER_PREFIXES = ("CF_", "SUBUMBRA_", "TUNNEL_", "WRANGLER_", "CLOUDFLARE_")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate reviewable manifest-era bootstrap drafts from one or more app .env files. "
            "Outputs manifest.yaml.proposed plus a secret-only .env.bootstrap.proposed."
        )
    )
    parser.add_argument("--source", action="append", default=[], help="Path to an app .env file")
    parser.add_argument("--app", action="append", default=[], help="Adapter/app id for the paired source")
    parser.add_argument(
        "--output",
        required=True,
        help="Directory path where manifest.yaml.proposed and .env.bootstrap.proposed will be written",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing proposed files")
    return parser


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if len(args.source) != len(args.app):
        parser.error("--source and --app must be provided the same number of times")
    if not args.source:
        parser.error("at least one --source/--app pair is required")
    return args


def is_excluded_key(key: str) -> bool:
    if key in IMPORT_EXCLUSION_LIST:
        return True
    if key in {"GITHUB_ACTIONS", "GITHUB_REF", "GITHUB_WORKSPACE", "CI"}:
        return True
    if RUNNER_PREFIX_RE.match(key):
        return True
    if key.startswith(NON_PROVIDER_PREFIXES):
        return True
    return False


def is_candidate_secret_key(key: str) -> bool:
    if is_excluded_key(key):
        return False
    if not SECRET_NAME_RE.search(key):
        return False
    # Treat common provider-ish names as candidates, but keep the script generic:
    # the generated manifest remains a draft the operator must review.
    return True


def parse_env_file(path: Path) -> OrderedDict[str, str]:
    results: OrderedDict[str, str] = OrderedDict()
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key.startswith("export "):
                    key = key[len("export ") :].strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                    value = value[1:-1]
                if not value or not is_candidate_secret_key(key):
                    continue
                results[key] = value
    except OSError as exc:
        raise RuntimeError(f"unable to read source file '{path}': {exc}") from exc
    return results


def collect_sources(source_paths: list[str], app_ids: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    ordered_apps: list[str] = []
    apps_seen: set[str] = set()
    candidates: list[dict[str, str]] = []

    for raw_app_id, raw_source in zip(app_ids, source_paths):
        if not CONSUMER_ID_RE.fullmatch(raw_app_id):
            raise RuntimeError(f"invalid app id '{raw_app_id}'")
        source_path = Path(raw_source)
        if not source_path.is_file():
            raise RuntimeError(f"source file '{raw_source}' does not exist or is not readable")
        if raw_app_id not in apps_seen:
            ordered_apps.append(raw_app_id)
            apps_seen.add(raw_app_id)

        entries = parse_env_file(source_path)
        for env_var, secret_value in entries.items():
            candidates.append(
                {
                    "app_id": raw_app_id,
                    "secret_ref": env_var,
                    "secret_value": secret_value,
                    "source_path": raw_source,
                }
            )

    return ordered_apps, candidates


def derive_provider_label(secret_ref: str) -> str:
    base = secret_ref.strip().lower()
    for suffix in ("_api_key", "_key", "_token", "_pat", "_secret"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return base or "provider"


def generate_key_id(provider: str, app_id: str, ordinal: int) -> str:
    return f"{provider}_{app_id}_{ordinal}"


def _next_key_id(
    provider: str,
    app_id: str,
    ordinals: dict[tuple[str, str], int],
    used_key_ids: set[str],
) -> str:
    key = (provider, app_id)
    ordinal = ordinals.get(key, 0) + 1
    candidate = generate_key_id(provider, app_id, ordinal)
    while candidate in used_key_ids:
        ordinal += 1
        candidate = generate_key_id(provider, app_id, ordinal)
    ordinals[key] = ordinal
    used_key_ids.add(candidate)
    return candidate


def _duplicate_prompt(
    candidate: dict[str, str],
    existing_record: dict[str, object],
    *,
    interactive: bool,
) -> bool:
    provider = candidate["provider"]
    app_id = candidate["app_id"]
    secret_ref = candidate["secret_ref"]
    source_path = candidate["source_path"]
    existing_key_id = str(existing_record["key_id"])
    existing_apps = ",".join(existing_record["apps"])
    message = (
        f"duplicate secret detected for provider '{provider}' from app '{app_id}' "
        f"({source_path}:{secret_ref}); existing record '{existing_key_id}' currently maps to app(s): "
        f"{existing_apps}"
    )
    if not interactive:
        print(f"WARNING: {message}; reusing existing record in non-interactive mode.", file=sys.stderr)
        return False
    print(f"WARNING: {message}")
    while True:
        choice = input("Reuse existing record? [Y/n]: ").strip().lower()
        if choice in {"", "y", "yes"}:
            return False
        if choice in {"n", "no"}:
            return True
        print("Please answer 'y' to reuse the existing record or 'n' to create a new record.")


def resolve_provider_values(candidates: list[dict[str, str]]) -> list[dict[str, object]]:
    planned_records: list[dict[str, object]] = []
    used_key_ids: set[str] = set()
    ordinals: dict[tuple[str, str], int] = {}
    interactive = sys.stdin.isatty()

    for candidate in candidates:
        candidate["provider"] = derive_provider_label(candidate["secret_ref"])
        existing_same_secret = None
        for record in planned_records:
            if (
                record["provider"] == candidate["provider"]
                and record["secret_value"] == candidate["secret_value"]
            ):
                existing_same_secret = record
                break

        if existing_same_secret is not None:
            create_new = _duplicate_prompt(candidate, existing_same_secret, interactive=interactive)
            if not create_new:
                if candidate["app_id"] not in existing_same_secret["apps"]:
                    existing_same_secret["apps"].append(candidate["app_id"])
                existing_same_secret["origins"].append(
                    {
                        "app_id": candidate["app_id"],
                        "secret_ref": candidate["secret_ref"],
                        "source_path": candidate["source_path"],
                    }
                )
                existing_same_secret["duplicate_reused"] = True
                continue
            duplicate_created = True
        else:
            duplicate_created = False

        app_id = candidate["app_id"]
        provider = candidate["provider"]
        key_id = _next_key_id(provider, app_id, ordinals, used_key_ids)
        planned_records.append(
            {
                "provider": provider,
                "key_id": key_id,
                "secret_ref": candidate["secret_ref"],
                "secret_value": candidate["secret_value"],
                "apps": [app_id],
                "origins": [
                    {
                        "app_id": app_id,
                        "secret_ref": candidate["secret_ref"],
                        "source_path": candidate["source_path"],
                    }
                ],
                "duplicate_reused": False,
                "duplicate_created": duplicate_created,
            }
        )

    return planned_records


def build_policy(key_id: str, app_ids: list[str]) -> dict[str, object]:
    return {
        "key_id": key_id,
        "policy_id": f"draft-{key_id}",
        "protocol": "http_rest",
        "capability_class": "custom_rest",
        "source": "env",
        "target": {
            "host": "replace-me.invalid",
            "base_path": "/v1",
        },
        "auth": {
            "scheme": "bearer",
        },
        "allow": {
            "adapters": app_ids,
            "methods": ["GET", "POST"],
            "path_prefixes": ["/v1"],
            "content_types": ["application/json"],
            "max_body_bytes": 1048576,
        },
    }


def build_manifest(planned_records: list[dict[str, object]]) -> dict[str, object]:
    keys: list[dict[str, object]] = []
    for record in planned_records:
        app_ids = sorted(str(app_id) for app_id in record["apps"])
        key_id = str(record["key_id"])
        keys.append(
            {
                "key_id": key_id,
                "provider": str(record["provider"]),
                "secret_ref": str(record["secret_ref"]),
                "adapters": app_ids,
                "unique_vault": False,
                "policy": build_policy(key_id, app_ids),
            }
        )
    return {"keys": keys}


def build_bootstrap_env(planned_records: list[dict[str, object]]) -> str:
    lines: list[str] = [
        "# Generated by scripts/subumbra-env-ingest.py",
        "# Review this file, use it for bootstrap, then shred/delete it.",
        "",
        "# Cloudflare bootstrap credentials",
        "CF_API_TOKEN=REPLACE_ME",
        "CF_ACCOUNT_ID=REPLACE_ME",
        "CF_WORKER_NAME=subumbra-proxy",
        "TOKEN_TTL_DAYS=90",
        "",
        "# Provider secrets referenced by manifest.yaml.proposed secret_ref values",
    ]

    emitted: set[str] = set()
    for record in planned_records:
        secret_ref = str(record["secret_ref"])
        if secret_ref in emitted:
            continue
        emitted.add(secret_ref)
        lines.append(f"{secret_ref}={record['secret_value']}")

    lines.extend(
        [
            "",
            "# No structural bootstrap variables are emitted here.",
            "# Review and edit manifest.yaml.proposed for target.host, auth, protocol, and allow rules.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_text_output(path: Path, content: str, force: bool) -> None:
    if path.exists() and not force:
        raise RuntimeError(f"output file '{path}' already exists; rerun with --force to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False, prefix=path.name + ".tmp."
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def write_json_output(path: Path, payload: dict[str, object], force: bool) -> None:
    write_text_output(path, json.dumps(payload, indent=2) + "\n", force)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        ordered_apps, candidates = collect_sources(args.source, args.app)
        if not candidates:
            raise RuntimeError("no candidate provider secret variables were detected across the provided source files")
        planned_records = resolve_provider_values(candidates)
        manifest = build_manifest(planned_records)
        bootstrap_env = build_bootstrap_env(planned_records)

        output_dir = Path(args.output)
        manifest_path = output_dir / "manifest.yaml.proposed"
        env_path = output_dir / ".env.bootstrap.proposed"

        write_json_output(manifest_path, manifest, args.force)
        write_text_output(env_path, bootstrap_env, args.force)

        print(f"Processed {len(args.source)} source file(s).")
        print(f"Detected {len(candidates)} candidate secret mapping(s) across app inputs.")
        print(f"Wrote {manifest_path}")
        print(f"Wrote {env_path}")
        print("Review the proposed manifest carefully before bootstrap:")
        print("  - replace target.host and auth settings")
        print("  - tighten protocol/capability_class/path_prefixes as needed")
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
