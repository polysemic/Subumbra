RUNTIME="SUBUMBRA_TOKEN_PROXY=p1
SUBUMBRA_TOKEN_OPENWEBUI=p2
SUBUMBRA_TOKEN_BIFROST=p3"

custom_token_keys=()
while IFS= read -r runtime_line; do
    echo "Processing line: [$runtime_line]"
    [[ "$runtime_line" == SUBUMBRA_TOKEN_*=* ]] || continue
    runtime_key="${runtime_line%%=*}"
    runtime_value="${runtime_line#*=}"
    case "$runtime_key" in
        SUBUMBRA_TOKEN_PROXY|SUBUMBRA_TOKEN_UI|SUBUMBRA_TOKEN_PROBE|SUBUMBRA_TOKEN_LITELLM)
            continue
            ;;
    esac
    echo "  Match: $runtime_key"
    custom_token_keys+=("$runtime_key")
done <<< "$RUNTIME"
echo "Found keys: ${custom_token_keys[@]}"
