#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

# Sync with bootstrap/subumbra-bootstrap.py:199 IMPORT_PROVIDER_WHITELIST.
IMPORT_PROVIDER_WHITELIST: dict[str, str] = {
    "ANTHROPIC_KEY": "anthropic",
    "OPENAI_KEY": "openai",
    "GROQ_KEY": "groq",
    "DEEPSEEK_KEY": "deepseek",
    "CEREBRAS_API_KEY": "cerebras",
    "GEMINI_API_KEY": "gemini",
    "MISTRAL_API_KEY": "mistral",
    "OPENROUTER_API_KEY": "openrouter",
    "TOGETHER_AI_API_KEY": "together",
    "XAI_API_KEY": "xai",
    "GITHUB_KEY": "github",
    "SLACK_KEY": "slack",
    "SENDGRID_KEY": "sendgrid",
    "ANTHROPIC_API_KEY": "anthropic",
    "OPENAI_API_KEY": "openai",
    "GROQ_API_KEY": "groq",
    "DEEPSEEK_API_KEY": "deepseek",
    "TOGETHER_API_KEY": "together",
    "GITHUB_TOKEN": "github",
    "SLACK_BOT_TOKEN": "slack",
    "SENDGRID_API_KEY": "sendgrid",
    "GOOGLE_KEY": "gemini",
    "GOOGLE_API_KEY": "gemini",
}

# Sync with bootstrap/subumbra-bootstrap.py:228 IMPORT_EXCLUSION_LIST.
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

PROVIDER_OUTPUT_SPECS: list[tuple[str, str, str]] = [
    ("anthropic", "ANTHROPIC_KEY", "ANTHROPIC_KEY_ID"),
    ("openai", "OPENAI_KEY", "OPENAI_KEY_ID"),
    ("groq", "GROQ_KEY", "GROQ_KEY_ID"),
    ("deepseek", "DEEPSEEK_KEY", "DEEPSEEK_KEY_ID"),
    ("cerebras", "CEREBRAS_API_KEY", "CEREBRAS_KEY_ID"),
    ("gemini", "GEMINI_API_KEY", "GEMINI_KEY_ID"),
    ("mistral", "MISTRAL_API_KEY", "MISTRAL_KEY_ID"),
    ("openrouter", "OPENROUTER_API_KEY", "OPENROUTER_KEY_ID"),
    ("together", "TOGETHER_AI_API_KEY", "TOGETHER_AI_KEY_ID"),
    ("xai", "XAI_API_KEY", "XAI_KEY_ID"),
    ("github", "GITHUB_KEY", "GITHUB_KEY_ID"),
    ("slack", "SLACK_KEY", "SLACK_KEY_ID"),
    ("sendgrid", "SENDGRID_KEY", "SENDGRID_KEY_ID"),
]
PROVIDER_CANONICAL_ENV = {provider: env_var for provider, env_var, _ in PROVIDER_OUTPUT_SPECS}
PROVIDER_KEY_ID_ENV = {provider: key_id_var for provider, _, key_id_var in PROVIDER_OUTPUT_SPECS}

ADAPTER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,61}[a-z0-9]$")
RUNNER_PREFIX_RE = re.compile(r"^RUNNER_")


def normalize_adapter_id(adapter_id: str) -> str:
    return adapter_id.upper().replace("-", "_")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a reviewable .env.bootstrap.proposed file from one or more app .env files. "
            "Supports multiple same-provider secrets, app-aware generated key_ids, and duplicate-"
            "secret reuse prompts across app inputs."
        )
    )
    parser.add_argument("--source", action="append", default=[], help="Path to an app .env file")
    parser.add_argument("--app", action="append", default=[], help="Adapter/app id for the paired source")
    parser.add_argument("--output", required=True, help="Path to write the generated artifact")
    parser.add_argument("--force", action="store_true", help="Overwrite the output file if it exists")
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
    return False


def parse_env_file(path: Path) -> OrderedDict[str, tuple[str, str]]:
    results: OrderedDict[str, tuple[str, str]] = OrderedDict()
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
                if not value or is_excluded_key(key):
                    continue
                provider = IMPORT_PROVIDER_WHITELIST.get(key)
                if provider is not None:
                    results[key] = (provider, value)
    except OSError as exc:
        raise RuntimeError(f"unable to read source file '{path}': {exc}") from exc
    return results


def collect_sources(source_paths: list[str], app_ids: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    ordered_apps: list[str] = []
    apps_seen: set[str] = set()
    candidates: list[dict[str, str]] = []

    for raw_app_id, raw_source in zip(app_ids, source_paths):
        if not ADAPTER_ID_RE.fullmatch(raw_app_id):
            raise RuntimeError(f"invalid app id '{raw_app_id}'")
        source_path = Path(raw_source)
        if not source_path.is_file():
            raise RuntimeError(f"source file '{raw_source}' does not exist or is not readable")
        if raw_app_id not in apps_seen:
            ordered_apps.append(raw_app_id)
            apps_seen.add(raw_app_id)

        entries = parse_env_file(source_path)
        for env_var, (provider, secret_value) in entries.items():
            candidates.append(
                {
                    "app_id": raw_app_id,
                    "provider": provider,
                    "secret_value": secret_value,
                    "env_var": env_var,
                    "source_path": raw_source,
                }
            )

    return ordered_apps, candidates


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
    env_var = candidate["env_var"]
    source_path = candidate["source_path"]
    existing_key_id = str(existing_record["key_id"])
    existing_apps = ",".join(existing_record["apps"])
    message = (
        f"duplicate secret detected for provider '{provider}' from app '{app_id}' "
        f"({source_path}:{env_var}); existing record '{existing_key_id}' currently maps to app(s): "
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
        existing_same_secret = None
        for record in planned_records:
            if record["provider"] == candidate["provider"] and record["secret_value"] == candidate["secret_value"]:
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
                        "env_var": candidate["env_var"],
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
                "secret_value": candidate["secret_value"],
                "apps": [app_id],
                "origins": [
                    {
                        "app_id": app_id,
                        "env_var": candidate["env_var"],
                        "source_path": candidate["source_path"],
                    }
                ],
                "duplicate_reused": False,
                "duplicate_created": duplicate_created,
            }
        )

    return planned_records


def build_artifact(
    ordered_apps: list[str],
    planned_records: list[dict[str, object]],
) -> str:
    app_allowed_keys: OrderedDict[str, list[str]] = OrderedDict((app_id, []) for app_id in ordered_apps)
    union_allowed_keys: list[str] = []

    lines: list[str] = [
        "# Generated by scripts/subumbra-env-ingest.py",
        "# WARNING: This file contains real API keys. Review, use for bootstrap, then shred/delete it.",
        "",
        "# Cloudflare credentials required before bootstrap",
        "CF_API_TOKEN=REPLACE_ME",
        "CF_ACCOUNT_ID=REPLACE_ME",
        "CF_WORKER_NAME=subumbra-proxy",
        "TOKEN_TTL_DAYS=90",
        "",
        "# Provider secrets and optional key_id overrides",
    ]

    provider_records: dict[str, list[dict[str, object]]] = OrderedDict()
    for record in planned_records:
        provider_records.setdefault(str(record["provider"]), []).append(record)

    for provider, env_var, key_id_var in PROVIDER_OUTPUT_SPECS:
        if provider not in provider_records:
            continue
        for slot_idx, record in enumerate(provider_records[provider], start=1):
            slot_env_var = env_var if slot_idx == 1 else f"{env_var}_{slot_idx}"
            slot_key_id_var = key_id_var if slot_idx == 1 else f"{key_id_var}_{slot_idx}"
            key_id = str(record["key_id"])
            lines.extend(
                [
                    f"{slot_env_var}={record['secret_value']}",
                    f"{slot_key_id_var}={key_id}",
                    "",
                ]
            )
            if key_id not in union_allowed_keys:
                union_allowed_keys.append(key_id)
            for app_id in record["apps"]:
                if key_id not in app_allowed_keys[app_id]:
                    app_allowed_keys[app_id].append(key_id)

    lines.extend(
        [
            "# Adapter allowlists",
            f"ADAPTER_IDS={','.join(ordered_apps)}",
        ]
    )
    for app_id in ordered_apps:
        lines.append(f"{normalize_adapter_id(app_id)}_ALLOWED_KEYS={','.join(app_allowed_keys[app_id])}")
    lines.append(f"PROXY_ALLOWED_KEYS={','.join(union_allowed_keys)}")
    lines.extend(
        [
            "",
            "# SUBUMBRA_TOKEN_<APP> values are generated by bootstrap.",
            "# They are not copied into .env until round 43-6-2.",
            "",
        ]
    )
    return "\n".join(lines)


def write_output(output_path: Path, content: str, force: bool) -> None:
    if output_path.exists() and not force:
        raise RuntimeError(f"output file '{output_path}' already exists; rerun with --force to overwrite")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(output_path.parent), delete=False, prefix=output_path.name + ".tmp."
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, output_path)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        ordered_apps, candidates = collect_sources(args.source, args.app)
        if not candidates:
            raise RuntimeError("no supported provider secrets were detected across the provided source files")
        planned_records = resolve_provider_values(candidates)
        artifact = build_artifact(ordered_apps, planned_records)
        write_output(Path(args.output), artifact, args.force)
        print(f"Processed {len(args.source)} source file(s).")
        print(f"Detected {len(candidates)} provider secret mapping(s) across app inputs.")
        print(f"Emitted {len(planned_records)} key record{'s' if len(planned_records) != 1 else ''}.")
        for record in planned_records:
            apps = ",".join(record["apps"])
            duplicate_note = []
            if record["duplicate_reused"]:
                duplicate_note.append("reused duplicate")
            if record["duplicate_created"]:
                duplicate_note.append("created from duplicate")
            note = f" ({'; '.join(duplicate_note)})" if duplicate_note else ""
            print(f"  - {record['provider']} {record['key_id']} apps={apps}{note}")
        print(f"Wrote reviewable bootstrap artifact: {args.output}")
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
