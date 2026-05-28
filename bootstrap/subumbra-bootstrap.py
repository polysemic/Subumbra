#!/usr/bin/env python3
"""Thin CLI entrypoint for Subumbra bootstrap operations."""

from __future__ import annotations

import sys

from subumbra_adapters import (
    print_adapters,
    print_help,
    print_key_ids,
    print_show_adapter,
    run_add_adapter,
    run_publish_policy,
    run_revoke_adapter,
)
from subumbra_cf import (
    run_deploy_worker,
    run_nuke_cloudflare,
    run_push_registry,
    run_update_access,
    run_update_gate,
    run_update_tunnel,
    run_update_ui_auth,
)
from subumbra_core import die
from subumbra_keys import (
    run_add_ssh_key,
    run_bootstrap,
    run_provision_key,
    run_revoke_key,
    run_revoke_ssh_key,
    run_rotate_ssh_key,
    run_rotate_wizard,
    run_status,
)
from subumbra_session import (
    _session_args,
    run_session_end,
    run_session_list,
    run_session_start,
    run_session_status,
)

if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print_help()
        sys.exit(0)
    elif "--list-key-ids" in sys.argv:
        print_key_ids()
        sys.exit(0)
    elif "--list-adapters" in sys.argv:
        print_adapters()
        sys.exit(0)
    elif "--show" in sys.argv:
        idx = sys.argv.index("--show")
        if idx + 1 >= len(sys.argv):
            die("--show requires an adapter_id argument, e.g. --show anythingllm")
        print_show_adapter(sys.argv[idx + 1])
        sys.exit(0)
    elif "--status" in sys.argv:
        run_status()
        sys.exit(0)

    if "--offline" in sys.argv and "--revoke-key" not in sys.argv:
        die("--offline is only supported together with --revoke-key")
    if "--rotate-policy" in sys.argv:
        die("--rotate-policy has been removed. Re-run full bootstrap for policy, routing, or adapter-binding changes.")
    mode_flags = (
        "--push-registry",
        "--deploy-worker",
        "--session",
        "--rotate",
        "--add-ssh-key",
        "--rotate-ssh-key",
        "--revoke-ssh-key",
        "--provision",
        "--revoke-key",
        "--add-adapter",
        "--revoke-adapter",
        "--publish-policy",
        "--update-tunnel",
        "--update-access",
        "--update-ui-auth",
        "--update-gate",
        "--nuke-cloudflare",
        "--status",
    )
    selected_modes = sum(flag in sys.argv for flag in mode_flags)
    if selected_modes > 1:
        die(", ".join(mode_flags) + " are mutually exclusive")
    if "--nuke" in sys.argv and selected_modes > 0:
        die("--nuke is supported only for full bootstrap")
    if "--push-registry" in sys.argv:
        run_push_registry()
    elif "--deploy-worker" in sys.argv:
        run_deploy_worker()
    elif "--session" in sys.argv:
        args = _session_args("--session")
        if not args:
            die("--session requires one of: start, end, status, list")
        subcommand = args[0]
        if subcommand == "start":
            run_session_start()
        elif subcommand == "end":
            run_session_end()
        elif subcommand == "status":
            run_session_status()
        elif subcommand == "list":
            run_session_list()
        else:
            die("--session requires one of: start, end, status, list")
    elif "--revoke-key" in sys.argv:
        try:
            target_key_id = sys.argv[sys.argv.index("--revoke-key") + 1]
        except IndexError:
            die("--revoke-key requires <key_id>")
        run_revoke_key(target_key_id)
    elif "--add-ssh-key" in sys.argv:
        try:
            idx = sys.argv.index("--add-ssh-key")
            target_key_id = sys.argv[idx + 1]
        except IndexError:
            die("--add-ssh-key requires <key_id>")
        if "--adapters" not in sys.argv:
            die("--add-ssh-key requires --adapters <csv>")
        try:
            adapters_csv = sys.argv[sys.argv.index("--adapters") + 1]
        except IndexError:
            die("--adapters requires <csv>")
        allow_hosts_csv = None
        if "--allow-hosts" in sys.argv:
            try:
                allow_hosts_csv = sys.argv[sys.argv.index("--allow-hosts") + 1]
            except IndexError:
                die("--allow-hosts requires <csv>")
        run_add_ssh_key(target_key_id, adapters_csv, allow_hosts_csv)
    elif "--rotate-ssh-key" in sys.argv:
        try:
            target_key_id = sys.argv[sys.argv.index("--rotate-ssh-key") + 1]
        except IndexError:
            die("--rotate-ssh-key requires <key_id>")
        allow_hosts_csv = None
        if "--allow-hosts" in sys.argv:
            try:
                allow_hosts_csv = sys.argv[sys.argv.index("--allow-hosts") + 1]
            except IndexError:
                die("--allow-hosts requires <csv>")
        run_rotate_ssh_key(target_key_id, allow_hosts_csv)
    elif "--revoke-ssh-key" in sys.argv:
        try:
            target_key_id = sys.argv[sys.argv.index("--revoke-ssh-key") + 1]
        except IndexError:
            die("--revoke-ssh-key requires <key_id>")
        run_revoke_ssh_key(target_key_id)
    elif "--add-adapter" in sys.argv:
        try:
            idx = sys.argv.index("--add-adapter")
            target_key_id = sys.argv[idx + 1]
            adapter_id = sys.argv[idx + 2]
        except IndexError:
            die("--add-adapter requires <key_id> <adapter_id>")
        run_add_adapter(target_key_id, adapter_id)
    elif "--revoke-adapter" in sys.argv:
        try:
            idx = sys.argv.index("--revoke-adapter")
            target_key_id = sys.argv[idx + 1]
            adapter_id = sys.argv[idx + 2]
        except IndexError:
            die("--revoke-adapter requires <key_id> <adapter_id>")
        run_revoke_adapter(target_key_id, adapter_id)
    elif "--publish-policy" in sys.argv:
        try:
            target_key_id = sys.argv[sys.argv.index("--publish-policy") + 1]
        except IndexError:
            die("--publish-policy requires <key_id>")
        run_publish_policy(target_key_id)
    elif "--provision" in sys.argv:
        try:
            target_key_id = sys.argv[sys.argv.index("--provision") + 1]
        except IndexError:
            die("--provision requires <key_id>")
        run_provision_key(target_key_id)
    elif "--rotate" in sys.argv:
        run_rotate_wizard()
    elif "--update-tunnel" in sys.argv:
        run_update_tunnel()
    elif "--update-access" in sys.argv:
        run_update_access()
    elif "--update-ui-auth" in sys.argv:
        run_update_ui_auth()
    elif "--update-gate" in sys.argv:
        run_update_gate()
    elif "--nuke-cloudflare" in sys.argv:
        run_nuke_cloudflare()
    else:
        run_bootstrap()
