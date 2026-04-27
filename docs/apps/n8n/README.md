# N8N

N8N is a proven Subumbra integration with two distinct patterns.

Start here:

- [switching.md](./switching.md) — AI-node and Workflow-node integration guide (R43-6)
- [workflows/](./workflows/) — importable workflow JSON files

Integration patterns:

- **AI-Node (recommended):** use n8n's native provider credential nodes (Anthropic, OpenAI)
  with base URL pointing at `subumbra-proxy`. Node constructs provider-format requests.
  Confirmed working for Anthropic (`/t`) and OpenAI (`/t/v1`).

- **Workflow-Node (API):** HTTP Request node calling the CF Worker `/proxy` endpoint directly.
  Supports full request control and any provider. Does not go through `subumbra-proxy`.

Confirmed in R43-6 (2026-04-25):
- Anthropic AI-node: `http://subumbra-proxy:8090/t` → `complete key_id=anthropic_prod status=200`
- OpenAI AI-node: `http://subumbra-proxy:8090/t/v1` → `complete key_id=openai_prod status=200`

Scope note:

- install and takeover operator docs are not part of the proved documentation set for n8n yet
- fuller n8n operator documentation belongs to a future n8n validation round
