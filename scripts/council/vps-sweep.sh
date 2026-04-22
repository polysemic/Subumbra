#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  scripts/council/vps-sweep.sh
  scripts/council/vps-sweep.sh --purge

Inspect or purge likely leftover council verification artifacts on a VPS.

Default mode prints:
  - one-off staging directories such as ~/subumbra-stage and ~/subumbra-r41test*
  - clean-run temp workspaces under /tmp/subumbra-clean-run-*
  - Docker containers, networks, and volumes tied to known staging/clean-run projects

Purge mode removes only the scoped leftovers listed above.
It does NOT touch /opt/subumbra or the normal long-lived stack unless those
resources were launched under a staging/clean-run compose project label.
EOF
}

mode="list"
if [[ $# -gt 1 ]]; then
    usage >&2
    exit 1
fi
if [[ $# -eq 1 ]]; then
    case "$1" in
        --purge)
            mode="purge"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 1
            ;;
    esac
fi

projects=(subumbra-clean-run subumbra-stage subumbra-r41test)
staging_dirs=(
    "$HOME/subumbra-stage"
)

list_matches() {
    local pattern="$1"
    find "${pattern%/*}" -maxdepth 1 -mindepth 1 -name "${pattern##*/}" 2>/dev/null | sort || true
}

docker_ps_by_project() {
    local project="$1"
    docker ps -a \
        --filter "label=com.docker.compose.project=${project}" \
        --format '{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Label "com.docker.compose.service"}}' \
        2>/dev/null || true
}

docker_networks_by_project() {
    local project="$1"
    docker network ls \
        --filter "label=com.docker.compose.project=${project}" \
        --format '{{.ID}}\t{{.Name}}' \
        2>/dev/null || true
}

docker_volumes_by_project() {
    local project="$1"
    docker volume ls \
        --filter "label=com.docker.compose.project=${project}" \
        --format '{{.Name}}' \
        2>/dev/null || true
}

print_section() {
    printf '\n[%s]\n' "$1"
}

print_list_mode() {
    local found=0
    print_section "staging directories"
    for dir in "${staging_dirs[@]}"; do
        if [[ -d "$dir" ]]; then
            printf '%s\n' "$dir"
            found=1
        fi
    done
    list_matches "$HOME/subumbra-r41test*" && found=1

    print_section "clean-run temp workspaces"
    if list_matches "/tmp/subumbra-clean-run-*"; then
        found=1
    fi

    for project in "${projects[@]}"; do
        print_section "docker containers: ${project}"
        docker_ps_by_project "$project"
        print_section "docker networks: ${project}"
        docker_networks_by_project "$project"
        print_section "docker volumes: ${project}"
        docker_volumes_by_project "$project"
    done

    cat <<'EOF'

Review the results above.
Run `scripts/council/vps-sweep.sh --purge` to remove only these scoped leftovers.
EOF
}

purge_dirs() {
    local dir
    for dir in "${staging_dirs[@]}"; do
        if [[ -d "$dir" ]]; then
            rm -rf "$dir"
            printf 'removed directory %s\n' "$dir"
        fi
    done
    while IFS= read -r path; do
        [[ -n "$path" ]] || continue
        rm -rf "$path"
        printf 'removed directory %s\n' "$path"
    done < <(list_matches "$HOME/subumbra-r41test*")

    while IFS= read -r path; do
        [[ -n "$path" ]] || continue
        rm -rf "$path"
        printf 'removed temp workspace %s\n' "$path"
    done < <(list_matches "/tmp/subumbra-clean-run-*")
}

purge_docker() {
    local project line id
    for project in "${projects[@]}"; do
        while IFS= read -r line; do
            [[ -n "$line" ]] || continue
            id="${line%%$'\t'*}"
            docker rm -f "$id" >/dev/null
            printf 'removed container %s (%s)\n' "$id" "$project"
        done < <(docker_ps_by_project "$project")

        while IFS= read -r line; do
            [[ -n "$line" ]] || continue
            id="${line%%$'\t'*}"
            docker network rm "$id" >/dev/null 2>&1 || true
            printf 'removed network %s (%s)\n' "$id" "$project"
        done < <(docker_networks_by_project "$project")

        while IFS= read -r line; do
            [[ -n "$line" ]] || continue
            docker volume rm "$line" >/dev/null 2>&1 || true
            printf 'removed volume %s (%s)\n' "$line" "$project"
        done < <(docker_volumes_by_project "$project")
    done
}

if [[ "$mode" == "list" ]]; then
    print_list_mode
    exit 0
fi

echo "Purging scoped staging leftovers..."
purge_docker
purge_dirs
echo "Scoped purge complete."
