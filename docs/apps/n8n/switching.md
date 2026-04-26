# N8N — Provider Switching Guide

*Tested in Round 43-6 (2026-04-25). Confirmed: Anthropic AI-node, OpenAI AI-node,
Workflow-node (API) via CF Worker.*

---

## Integration Patterns

N8N has two distinct integration patterns with Subumbra. They use different n8n node types
and different Subumbra entry points.

---

## 1. AI-Node Integration (recommended)

Uses n8n's native provider credential nodes. The credential's base URL points at
`subumbra-proxy`. The node constructs the provider-format request itself.

### Anthropic AI-node

**Credential type:** Anthropic API  
**Credential configuration:**
- **API Key:** `anthropic_prod`
- **Base URL / Custom API URL:** `http://subumbra-proxy:8090/t`

The Anthropic node appends `/v1/messages` to the base URL.
Proxy log confirms: `POST /t/v1/messages ... complete key_id=anthropic_prod status=200`

### OpenAI AI-node

**Credential type:** OpenAI API  
**Credential configuration:**
- **API Key:** `openai_prod`
- **Base URL / Organization Base URL:** `http://subumbra-proxy:8090/t/v1`

The OpenAI Responses node appends `/responses` to the base URL.
Proxy log confirms: `POST /t/v1/responses ... complete key_id=openai_prod status=200`

### Switching providers

Change the credential's base URL and API key to match the target provider:

| Provider | Base URL | API Key |
|---|---|---|
| anthropic | `http://subumbra-proxy:8090/t` | `anthropic_prod` |
| openai | `http://subumbra-proxy:8090/t/v1` | `openai_prod` |
| groq | `http://subumbra-proxy:8090/t/openai/v1` | `groq_prod` |
| deepseek | `http://subumbra-proxy:8090/t/v1` | `deepseek_prod` |
| mistral | `http://subumbra-proxy:8090/t/v1` | `mistral_prod` |
| openrouter | `http://subumbra-proxy:8090/t/api/v1` | `openrouter_prod` |
| xai | `http://subumbra-proxy:8090/t/v1` | `xai_prod` |
| cerebras | `http://subumbra-proxy:8090/t/v1` | `cerebras_prod` |

Credentials are per-workflow-node. Switching providers means changing the credential
attached to the model node, not a global setting.

---

## 2. Workflow-Node (API) Integration

Uses n8n's HTTP Request node to call the CF Worker `/proxy` endpoint directly.
This pattern does **not** go through `subumbra-proxy` — it calls the Worker directly.
Proof is in CF Worker logs / Cloudflare DO dashboard, not `subumbra-proxy` logs.

**Node type:** HTTP Request (`n8n-nodes-base.httpRequest`)  
**URL:** `https://<your-worker-url>/proxy`  
**Auth:** Generic Credential → HTTP Header Auth → `X-Subumbra-Token: <proxy-token>`  
**Method:** POST  
**Body:** canonical Subumbra `/proxy` JSON:

```json
{
  "key_id": "anthropic_prod",
  "provider": "anthropic",
  "target_url": "https://api.anthropic.com/v1/messages",
  "method": "POST",
  "headers": {
    "anthropic-version": "2023-06-01",
    "content-type": "application/json"
  },
  "body": {
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Hello"}]
  }
}
```

Provider switching: change `key_id`, `provider`, `target_url`, `headers`, and `body.model`.

See [`docs/adapter-contract.md`](../../../../adapter-contract.md) for the full canonical body spec.
See workflow JSON: [`n8n-workflow-node-api.json`](../../../docs/apps/n8n/workflows/n8n-workflow-node-api.json)

---

## 3. Via LiteLLM (AI-node pattern)

Use the OpenAI credential type with base URL pointing at LiteLLM:

- **API Key:** any non-empty value
- **Base URL:** `http://litellm:4000/v1`

Model list auto-populates from LiteLLM. All providers in `litellm/config.yaml` accessible.

---

## 4. Tested combinations

See [provider-matrix.md](../../provider-matrix.md) N8N column.

Confirmed in R43-6:
- Anthropic AI-node: ✓ (proxy log, 2026-04-25T16:58)
- OpenAI AI-node: ✓ (proxy log, 2026-04-25T16:58)
- Workflow-node (API): ✓ (existing test workflow, R43 baseline)

---

## App-Specific Notes

- Credentials in n8n are per-node, not global. Each AI node in a workflow can use a
  different credential — no global switching required.
- The AI-node and Workflow-node patterns are complementary. Use AI-node for standard
  LLM workflows; use the Workflow-node for advanced cases requiring full request control
  or when calling the Worker directly from an automation context.
- Workflow-node calls do not appear in `subumbra-proxy` logs — only in CF Worker logs.
