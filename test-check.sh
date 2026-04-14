#!/bin/bash
LITELLM_ALLOWED_KEYS="anthropic_prod,openai_prod,groq"
CONFIG_KEYS=$(grep -oE 'api_key:[ ]*"forge:[^"]*"' litellm/config.yaml | sed -n 's/.*"forge:\(.*\)".*/\1/p' | sort -u)

IFS=',' read -ra ALLOWED_ARRAY <<< "$LITELLM_ALLOWED_KEYS"
DRIFT=false

for cfg_key in $CONFIG_KEYS; do
    found=false
    for allowed in "${ALLOWED_ARRAY[@]}"; do
        if [[ "$cfg_key" == "$allowed" ]]; then
            found=true
            break
        fi
    done
    if [[ "$found" == "false" ]]; then
        echo "Missing key: $cfg_key"
        DRIFT=true
    fi
done

echo "Drift: $DRIFT"
