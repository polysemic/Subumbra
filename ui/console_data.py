"""
Mock console dataset.

Used when SUBUMBRA_UI_DEMO=1 or when subumbra-keys is unreachable so the
console renders cleanly during install, dev, and demos. The shape mirrors
the live merged dataset built in app.build_console_data() — any field
added here should also be filled in by the live merge path.
"""

NAV = [
    {"id": "overview",      "label": "Overview",      "icon": "◐", "href": "/overview"},
    {"id": "vault",         "label": "Vault",         "icon": "▣", "href": "/vault"},
    {"id": "sessions",      "label": "Sessions",      "icon": "◷", "href": "/sessions"},
    {"id": "adapters",      "label": "Adapters",      "icon": "⌖", "href": "/adapters"},
    {"id": "policies",      "label": "Policies",      "icon": "≡", "href": "/policies"},
    {"id": "audit",         "label": "Audit",         "icon": "▤", "href": "/audit"},
    {"id": "observability", "label": "Observability", "icon": "◉", "href": "/observability"},
    {"id": "cloudflare",    "label": "Cloudflare",    "icon": "☁", "href": "/cloudflare"},
    {"id": "upcoming",      "label": "Upcoming",      "icon": "✧", "href": "/upcoming"},
    {"id": "settings",      "label": "Settings",      "icon": "✿", "href": "/settings"},
]

NAV_SECTIONS = [
    ("Operate",  ["overview", "vault", "sessions", "adapters"]),
    ("Govern",   ["policies", "audit", "observability"]),
    ("Platform", ["cloudflare", "upcoming", "settings"]),
]

ORG = {
    "name":     "Polysemic",
    "instance": "subumbra-prod-01",
    "user":     "eric",
    "role":     "Operator",
}

CONSOLE_DATA = {

    "org": ORG,

    "health": {
        "keysService":   True,
        "proxy":         True,
        "workerAuth":    "ok",
        "agent":         True,
        "verifyAge":     "3d 4h",
        "workerSha":     "a34c3d5",
        "integrityPinned": True,
    },

    "sessions": {
        "lockdown_enabled": True,
        "active": [
            {
                "id":           "sess_8febbac",
                "name":         "morning workday",
                "adapters":     ["litellm", "openwebui"],
                "keys":         ["openai_prod", "anthropic_prod", "groq_prod"],
                "ttl_seconds":  4 * 3600 + 12 * 60,
                "ttl_label":    "4h 12m",
                "queries_used": 41,
                "queries_max":  None,
                "opened_at":    "2026-05-25T08:14:00Z",
            },
            {
                "id":           "sess_a34c3d5",
                "name":         "ssh deploy",
                "adapters":     ["sshtest"],
                "keys":         ["github_vps_test"],
                "ttl_seconds":  1 * 3600 + 45 * 60,
                "ttl_label":    "1h 45m",
                "queries_used": 3,
                "queries_max":  100,
                "opened_at":    "2026-05-25T11:02:00Z",
            },
        ],
    },

    "keys": [
        {"id": "openai_prod",      "type": "api", "provider": "openai",    "capability": "llm-chat",
         "vault": "shared",   "lastUsed": "2m ago",  "requests": 3214, "status": "active",
         "target": "api.openai.com",     "policyHash": "a3f2…b801", "policyId": "openai-prod",
         "rpm": 240, "adapters": ["litellm","openwebui"], "created": "2026-04-04"},
        {"id": "openai_dev",       "type": "api", "provider": "openai",    "capability": "llm-chat",
         "vault": "isolated", "lastUsed": "1h ago",  "requests": 218,  "status": "active",
         "target": "api.openai.com",     "policyHash": "7e1c…4f29", "policyId": "openai-dev",
         "rpm": 60,  "adapters": ["litellm"], "created": "2026-04-19"},
        {"id": "anthropic_prod",   "type": "api", "provider": "anthropic", "capability": "llm-chat",
         "vault": "shared",   "lastUsed": "4m ago",  "requests": 1842, "status": "active",
         "target": "api.anthropic.com",  "policyHash": "d4b9…21cc", "policyId": "anthropic-prod",
         "rpm": 180, "adapters": ["litellm","openwebui","librechat"], "created": "2026-04-04"},
        {"id": "anthropic_canary", "type": "api", "provider": "anthropic", "capability": "llm-chat",
         "vault": "shared",   "lastUsed": "32m ago", "requests": 47,   "status": "paused",
         "target": "api.anthropic.com",  "policyHash": "9c2a…f701", "policyId": "anthropic-prod",
         "rpm": 60,  "adapters": ["librechat"], "created": "2026-05-12"},
        {"id": "groq_prod",        "type": "api", "provider": "groq",      "capability": "llm-chat",
         "vault": "shared",   "lastUsed": "8m ago",  "requests": 612,  "status": "active",
         "target": "api.groq.com",       "policyHash": "2b88…ee14", "policyId": "groq-prod",
         "rpm": 120, "adapters": ["litellm"], "created": "2026-04-11"},
        {"id": "deepseek_prod",    "type": "api", "provider": "deepseek",  "capability": "llm-chat",
         "vault": "shared",   "lastUsed": "3h ago",  "requests": 89,   "status": "active",
         "target": "api.deepseek.com",   "policyHash": "60d1…aa9f", "policyId": "deepseek-prod",
         "rpm": 60,  "adapters": ["litellm"], "created": "2026-05-01"},
    ],

    "ssh_keys": [
        {"id": "github_vps_test",   "provider": "github",  "alg": "ed25519",
         "fpr": "SHA256:fa20a7b1d4c8e2f5891d", "hosts": ["github.com"],
         "lastUsed": "12m ago", "signs": 23,  "status": "active", "adapter": "sshtest",
         "pub": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBmZ7p2Q4nXKvLqcW3jH8eRtY9ZxPmK1aOdJiUhBV6XF subumbra/github_vps_test"},
        {"id": "verify_vps_key",    "provider": "generic", "alg": "ed25519",
         "fpr": "SHA256:11c43e00b8d9f4ac4f12", "hosts": ["vps-1.subumbra.example"],
         "lastUsed": "6h ago",  "signs": 4,   "status": "active", "adapter": "sshtest",
         "pub": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE2yPq8aB1mLkR4nV8w0CzXjY7HxNg6FtUiOpEdMkLrA subumbra/verify_vps_key"},
        {"id": "deploy_bot_staging","provider": "github",  "alg": "ed25519",
         "fpr": "SHA256:88af1c9d6e72ba03e1a7", "hosts": ["github.com"],
         "lastUsed": "1d ago",  "signs": 42,  "status": "active", "adapter": "sshtest",
         "pub": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH3xRqK9wYbN2vMcLpJ8aHfTzX4ZeOdBgUkInPm5VyDQ subumbra/deploy_bot_staging"},
        {"id": "ci_runner_rsync",   "provider": "generic", "alg": "ed25519",
         "fpr": "SHA256:2d4f8a7c1e9b3d5ef9c0", "hosts": ["vps-2.subumbra.example"],
         "lastUsed": "2h ago",  "signs": 117, "status": "active", "adapter": "sshtest",
         "pub": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK4yMnP6aV3lXcGwR9kJhB1tFqXeO2pZdNbUiYHmK7sE subumbra/ci_runner_rsync"},
        {"id": "backup_cron_b2",    "provider": "generic", "alg": "ed25519",
         "fpr": "SHA256:9e1b4a3c7d8f2a6e6c11", "hosts": ["backup.subumbra.example"],
         "lastUsed": "never",   "signs": 0,   "status": "paused", "adapter": "sshtest",
         "pub": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIQ5zNoX7bW8mZuKvLrJ4gHdBaCkF1pNyMqRiTvEwAOPp subumbra/backup_cron_b2"},
    ],

    "adapters": [
        {"id":"litellm","name":"LiteLLM","logo":"LL","token":"sk-litellm-3fbe4c3f","tokenMasked":"sk-lit…4c3f","tokenAge":"47d ago","status":"active","statusLabel":"active","caps":["llm-chat","list-all-keys","read-stats"],"keys":["openai_prod","openai_dev","anthropic_prod"],"lastSeen":"2m ago","expiresAt":"2026-07-01T00:00:00Z","proxy_urls":[{"key_id":"openai_prod","url":"https://subumbra-proxy.polysemic.workers.dev/t/openai_prod"},{"key_id":"anthropic_prod","url":"https://subumbra-proxy.polysemic.workers.dev/t/anthropic_prod"}],"config_blocks":[{"label":"openai_prod","target":"config.yaml","copy":"SUBUMBRA_TOKEN_LITELLM=sk-litellm-3fbe4c3f\nmodel_list[].litellm_params.api_key: sk-litellm-3fbe4c3f\nmodel_list[].litellm_params.api_base: https://subumbra-proxy.polysemic.workers.dev/t/openai_prod"}],"docsPath":"docs/apps/litellm/"},
        {"id":"openwebui","name":"Open WebUI","logo":"OW","token":"sk-openwebui-19d1262d","tokenMasked":"sk-ope…1262d","tokenAge":"47d ago","status":"active","statusLabel":"active","caps":["llm-chat","list-keys"],"keys":["openai_prod","anthropic_prod"],"lastSeen":"4m ago","expiresAt":"2026-07-01T00:00:00Z","proxy_urls":[{"key_id":"openai_prod","url":"https://subumbra-proxy.polysemic.workers.dev/t/openai_prod/v1"}],"config_blocks":[{"label":"openai_prod","target":".env","copy":"SUBUMBRA_TOKEN_OPENWEBUI=sk-openwebui-19d1262d\nOPENAI_API_KEY=sk-openwebui-19d1262d\nOPENAI_API_BASE_URL=https://subumbra-proxy.polysemic.workers.dev/t/openai_prod/v1"}],"docsPath":"docs/apps/openwebui/"},
        {"id":"sshtest","name":"SSH bridge","logo":"SH","token":"sk-sshtest-fa20a7b1","tokenMasked":"sk-ssh…a7b1","tokenAge":"2d ago","status":"active","statusLabel":"active","caps":["ssh-sign","write-audit"],"keys":["github_vps_test","verify_vps_key"],"lastSeen":"12m ago","expiresAt":"2026-07-01T00:00:00Z","proxy_urls":[{"key_id":"github_vps_test","url":"https://subumbra-proxy.polysemic.workers.dev/t/github_vps_test"}],"config_blocks":[],"docsPath":"docs/operator-guide.md"},
    ],

    "policies": [
        {"id":"anthropic-prod","name":"anthropic policy","hash":"d4b9…21cc","usedBy":2,"provider":"anthropic","target_host":"api.anthropic.com","base_path":"/v1","capability_class":"llm-chat","auth_scheme":"header","auth_header":"x-api-key","auth_prefix":"—","allow_methods":["POST"],"allow_path_prefixes":["/messages","/messages/count_tokens"],"allow_adapters":["litellm","openwebui"],"key_ids":["anthropic_prod","anthropic_canary"]},
        {"id":"openai-prod","name":"openai policy","hash":"a3f2…b801","usedBy":1,"provider":"openai","target_host":"api.openai.com","base_path":"/v1","capability_class":"llm-chat","auth_scheme":"header","auth_header":"authorization","auth_prefix":"Bearer","allow_methods":["POST"],"allow_path_prefixes":["/chat/completions","/embeddings"],"allow_adapters":["litellm","openwebui"],"key_ids":["openai_prod"]},
    ],

    "audit": [
        {"ts":"12:40:18","date":"May 25","adapter":"litellm",  "endpoint":"/v1/chat/completions","keyId":"openai_prod",     "provider":"openai",   "remote":"10.0.1.41","verdict":"allow","reason":"ok",                  "method":"POST"},
        {"ts":"12:39:55","date":"May 25","adapter":"openwebui","endpoint":"/v1/messages",        "keyId":"anthropic_prod",  "provider":"anthropic","remote":"10.0.1.42","verdict":"allow","reason":"ok",                  "method":"POST"},
        {"ts":"12:39:12","date":"May 25","adapter":"litellm",  "endpoint":"/v1/chat/completions","keyId":"openai_prod",     "provider":"openai",   "remote":"10.0.1.41","verdict":"allow","reason":"ok",                  "method":"POST"},
        {"ts":"12:38:44","date":"May 25","adapter":"sshtest",  "endpoint":"/ssh/sign",           "keyId":"github_vps_test", "provider":"github",   "remote":"127.0.0.1","verdict":"allow","reason":"ok",                  "method":"POST"},
        {"ts":"12:37:09","date":"May 25","adapter":"librechat","endpoint":"/v1/messages",        "keyId":"anthropic_canary","provider":"anthropic","remote":"10.0.1.50","verdict":"deny", "reason":"key_paused",          "method":"POST"},
        {"ts":"12:36:42","date":"May 25","adapter":"litellm",  "endpoint":"/v1/chat/completions","keyId":"groq_prod",       "provider":"groq",     "remote":"10.0.1.41","verdict":"allow","reason":"ok",                  "method":"POST"},
        {"ts":"12:36:01","date":"May 25","adapter":"n8n-unknown","endpoint":"/v1/chat/completions","keyId":"openai_prod",   "provider":"openai",   "remote":"10.0.2.17","verdict":"deny", "reason":"adapter_not_allowed", "method":"POST"},
        {"ts":"12:34:55","date":"May 25","adapter":"litellm",  "endpoint":"/v1/chat/completions","keyId":"openai_prod",     "provider":"openai",   "remote":"10.0.1.41","verdict":"allow","reason":"ok",                  "method":"POST"},
        {"ts":"12:34:02","date":"May 25","adapter":"sshtest",  "endpoint":"/ssh/sign",           "keyId":"github_vps_test", "provider":"github",   "remote":"127.0.0.1","verdict":"allow","reason":"ok",                  "method":"POST"},
        {"ts":"12:33:18","date":"May 25","adapter":"openwebui","endpoint":"/v1/messages",        "keyId":"anthropic_prod",  "provider":"anthropic","remote":"10.0.1.42","verdict":"allow","reason":"ok",                  "method":"POST"},
        {"ts":"12:32:50","date":"May 25","adapter":"litellm",  "endpoint":"/v1/embeddings",      "keyId":"openai_prod",     "provider":"openai",   "remote":"10.0.1.41","verdict":"deny", "reason":"path_not_allowed",    "method":"POST"},
        {"ts":"12:31:44","date":"May 25","adapter":"litellm",  "endpoint":"/v1/chat/completions","keyId":"groq_prod",       "provider":"groq",     "remote":"10.0.1.41","verdict":"allow","reason":"ok",                  "method":"POST"},
    ],

    "attention": [
        {"sev":"warn", "title":"Cloudflare Tunnel token rotated 89 days ago", "body":"Rotation recommended every 90 days. One day until soft warn.", "cta":"Rotate token", "href":"/cloudflare"},
        {"sev":"info", "title":"1 key paused (anthropic_canary)",             "body":"Paused 14 hours ago by eric. Will not serve requests until resumed.", "cta":"Resume", "href":"/vault"},
        {"sev":"info", "title":"subumbra-verify ran 3d 4h ago",               "body":"Last drift check clean. Recommended cadence: weekly.",        "cta":"Run now", "href":"/settings"},
    ],

    "upcoming": [
        {"id":"mgmt-api",     "title":"Hardened management API",       "eta":"Q3 2026", "note":"Replaces CLI handoffs for pause / resume / rotate / delete; UI write paths gated behind it."},
        {"id":"oauth-broker", "title":"OAuth client-secret broker",     "eta":"Q4 2026", "note":"Same split-trust envelope for OAuth client_secret + refresh tokens."},
        {"id":"model-allow",  "title":"Per-key model allowlist",        "eta":"Q3 2026", "note":"policy.allow.models lets a key serve only specific upstream models."},
        {"id":"intent-trust", "title":"Three-level intent attestation", "eta":"Q4 2026", "note":"Existence / initiator / content-source guardrails for prompt-injection abuse."},
        {"id":"ssh-confirm",  "title":"Confirm-each-sign for SSH",      "eta":"Q3 2026", "note":"Optional interactive confirmation per SSH sign + max_sign_ops quota."},
        {"id":"git-signing",  "title":"GPG & git-commit signing",       "eta":"Q4 2026", "note":"Same DO custody for GPG keys; sign commits via Subumbra agent."},
        {"id":"sso",          "title":"SSO + RBAC for the console",     "eta":"Q4 2026", "note":"Org-level identity provider, role-scoped access, audit-by-user."},
        {"id":"cf-analytics", "title":"Cloudflare analytics + log tail","eta":"Q3 2026", "note":"Request volume, edge latency, deny rates surfaced from CF Logpush."},
        {"id":"import-env",   "title":"Guided import from existing .env","eta":"Q4 2026","note":"Scans config / .env, encrypts via secure-paste, shreds source."},
    ],

    "cloudflare": {
        "tunnel": {"status":"healthy","hostname":"subumbra.polysemic.dev","tunnelId":"8febbac9-2a3b-4c7d-9e0f-a1b2c3d4e5f6","tokenAge":"89d","cnameTarget":"8febbac9-2a3b-4c7d-9e0f-a1b2c3d4e5f6.cfargotunnel.com"},
        "access": {"status":"healthy","appName":"subumbra-prod-01","policyName":"service-token-only","serviceTokenAge":"63d","clientIdMasked":"4f8c…1d2e"},
        "worker": {"status":"healthy","url":"subumbra-proxy.polysemic.workers.dev","sha":"a34c3d5","deployedAt":"2026-05-24T22:14:00Z","verifyAge":"3d 4h","vaultsCount":2},
        "kv":     {"namespace":"PROVIDER_REGISTRY_KV","recordCount":14,"lastSync":"2026-05-24T22:14:00Z"},
    },

    "observability": {
        "services": [
            {"name":"subumbra-keys","status":"ok","sub":"read API","note":"demo health response"},
            {"name":"subumbra-proxy","status":"ok","sub":"transparent proxy","note":"worker_auth=ok"},
            {"name":"cf worker","status":"ok","sub":"https://subumbra-proxy.polysemic.workers.dev","note":"gate read ok"},
        ],
        "velocity": [
            {"key_id":"openai_prod","provider":"openai","request_count":62},
            {"key_id":"anthropic_prod","provider":"anthropic","request_count":41},
            {"key_id":"github_vps_test","provider":"github","request_count":3},
        ],
        "decrypt_errors": [
            {"reason_code":"key_paused","count":14},
            {"reason_code":"adapter_not_allowed","count":11},
            {"reason_code":"path_not_allowed","count":4},
            {"reason_code":"rate_limit_exceeded","count":2},
        ],
    },
}
