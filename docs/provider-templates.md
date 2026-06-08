# Provider Templates

Subumbra ships signed built-in provider templates for common API services.
This guide explains what they are, which ones are available, and how to choose
between the three ways to configure a provider.

## Three ways to configure a provider

### 1. Built-in signed template (`template: <name>`)

The simplest option. A single field in `manifest.yaml` references a pre-written
policy from the signed catalog at `bootstrap/templates/`.

```yaml
keys:
  - key_id: openai_prod
    provider: openai
    secret_ref: OPENAI_KEY
    adapters: [universal]
    unique_vault: false
    template: openai        # ← uses bootstrap/templates/openai.yaml
```

Bootstrap verifies the SHA-256 hash and Ed25519 signature of the template file
against `bootstrap/templates/catalog.json` and `catalog.sig` before expanding
it. If the file has been tampered with, bootstrap fails closed.

Built-in signed templates now ship active default `velocity` values. Those
defaults are part of the template policy and apply once the key is published to
KV.

**Use this when:** you trust the built-in policy defaults and do not need to
change allowed paths, rate limits, or deny rules.

---

### 2. Custom local template override (`template: <name>`)

A YAML file you author in a `./templates/` directory at the repo root. It uses
the same field structure as built-in templates but is not signature-verified —
you own it completely. The manifest still says `template: <name>`; bootstrap
prefers `./templates/<name>.yaml` over the signed catalog when both share the
same name.

```yaml
keys:
  - key_id: openai_prod
    provider: openai
    secret_ref: OPENAI_KEY
    adapters: [universal]
    unique_vault: false
    template: openai        # ← same name; bootstrap checks templates/ first
```

Bootstrap looks for `templates/<name>.yaml` at the repo root before checking
the signed catalog. If a file exists there with the same name, it is used
instead (with a warning that it is not signature-verified).

To create one, copy the built-in template as a starting point:

```bash
mkdir -p templates
cp bootstrap/templates/openai.yaml templates/openai.yaml
# edit templates/openai.yaml to suit your needs
```

The `templates/` directory at the repo root is gitignored by default. Files
inside it are operator-owned and not committed.

**Use this when:** you want to change the allowed paths, `max_body_bytes`,
add `deny` rules, or change `velocity` limits — but still keep policy in a
separate file per provider.

---

### 3. Inline policy in `manifest.yaml`

The full policy block written directly inside `manifest.yaml`. No separate
file needed. `manifest.example.yaml` shows a complete inline example with every
field documented.

```yaml
keys:
  - key_id: openai_prod
    provider: openai
    secret_ref: OPENAI_KEY
    adapters: [universal]
    unique_vault: false
    policy:                             # ← inline, replaces template:
      protocol: openai_compatible
      capability_class: llm
      target:
        host: api.openai.com
        base_path: /v1
      auth:
        scheme: bearer
      allow:
        methods: [GET, POST]
        path_prefixes: [/v1/chat, /v1/completions, /v1/models]
        content_types: [application/json]
        max_body_bytes: 8388608
      # deny:
      #   path_prefixes: [/v1/fine-tuning]
      # velocity:
      #   consumer_rpm: 60
```

**Use this when:** you want everything in one file, or are configuring a
provider that does not have a built-in template.

---

## Which to use?

| Situation | Recommended approach |
|-----------|---------------------|
| Standard LLM provider, default limits | Built-in signed template |
| Need to change paths or limits for one provider | Custom local template |
| Provider not in the built-in catalog | Inline policy |
| All config visible in one file | Inline policy |
| Strict integrity verification required | Built-in signed template |

---

## Built-in provider templates

All 12 templates are in `bootstrap/templates/`. Each file documents its own
fields, active default `velocity` values, optional controls, and a day-2
update command at the top.

| Template name | Provider | Auth scheme | Capability class | `max_body_bytes` |
|---------------|----------|-------------|-----------------|-----------------|
| `anthropic` | Anthropic (Claude) | `header` (`x-api-key`) | `llm` | 8 MB |
| `deepseek` | DeepSeek | `bearer` | `llm` | 8 MB |
| `gemini` | Google Gemini | `bearer` | `llm` | 16 MB |
| `github` | GitHub REST API | `bearer` | `source_control_read` | 1 MB |
| `groq` | Groq | `bearer` | `llm` | 8 MB |
| `mistral` | Mistral AI | `bearer` | `llm` | 8 MB |
| `openai` | OpenAI | `bearer` | `llm` | 8 MB |
| `openrouter` | OpenRouter | `bearer` | `llm` | 16 MB |
| `sendgrid` | Twilio SendGrid | `bearer` | `email_send` | 10 MB |
| `slack` | Slack Web API | `bearer` | `custom_rest` | 1 MB |
| `together` | Together AI | `bearer` | `llm` | 8 MB |
| `xai` | xAI Grok | `bearer` | `llm` | 8 MB |

Gemini and OpenRouter are set to 16 MB because they route to models with
million-token context windows. All other LLM providers are 8 MB, which covers
128K context in JSON. GitHub and Slack payloads are small; 1 MB is generous.

---

## Adding a provider not in the catalog

Use an inline policy or a custom local template with `protocol: http_rest` and
set `target.host`, `auth.scheme`, `allow.path_prefixes`, and
`capability_class` to match the API you are brokering. See
[manifest.example.yaml](../manifest.example.yaml) for the full field reference.

## npm note

npm publish brokering does not use a built-in signed provider template in this
catalog. The npm path relies on an operator-authored `type: npm_token` policy
that declares package scope and publish deny fields, while the operator-facing
CLI snippet comes from adapter metadata in `bootstrap/templates/consumers/npm.yaml`.

---

## Updating policy after bootstrap

Changing `allow`, `deny`, `response`, or `velocity` fields does not require a
full re-bootstrap. Push the updated policy to Cloudflare KV with:

```bash
./bootstrap.sh --publish-policy <key_id>
```

If you upgrade to a release that changes built-in template policy defaults,
rebuild the bootstrap image first so the signed catalog inside the container is
current, then run `./bootstrap.sh --publish-policy <key_id>` for each active
template-backed key you want updated.

Changing `target.host`, `auth.scheme`, or any field that affects encryption
(the AAD binding) **does** require a full re-bootstrap, because the ciphertext
is cryptographically sealed to the policy in effect at encryption time.
