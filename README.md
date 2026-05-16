# Subumbra — Keep Your API Keys Safe

[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/polysemic/Subumbra/badge)](https://scorecard.dev/viewer/?uri=github.com/polysemic/Subumbra)
[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg)](LICENSE)

> **Alpha release** — designed for self-hosters and tinkerers who want to test this early. Not yet recommended for production use.

Subumbra is a **security proxy** that sits between your apps (like LiteLLM, OpenWebUI, AnythingLLM, n8n, etc.) and providers (like OpenAI or Anthropic). Instead of pasting your API keys directly into each app — where they can be leaked in logs, config files, or breaches — Subumbra holds them encrypted and hands them out only to apps you explicitly authorize, one request at a time.

**In plain terms:** your apps never see your real API keys. They talk to Subumbra, Subumbra talks to OpenAI (or whoever), and your keys stay locked away.

→ [How it works under the hood](docs/architecture.md) · [Planned features](ROADMAP.md)

---

## Before you start

You'll need:

- A Linux server (VPS or homelab) with **Docker** installed. Anything with Docker should work, but I have only tested on Ubuntu 24.04 LTS.
- A [**Cloudflare account**](https://cloudflare.com) with the **Workers Paid plan** ($5/month) — this is where your keys are held encrypted. It may work with a free account, but this is not guaranteed.
- A **Cloudflare API token** (created at [dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens) — use "Edit Cloudflare Workers" template, add `Workers KV Storage: Edit`)
- Your **Cloudflare Account ID** (visible in the URL when you're logged into Cloudflare or after creating your API token)
- At least one provider API key (e.g. an OpenAI key starting with `sk-...`)

> Don't have Docker yet? Follow the [full install guide](docs/subumbra-install.md) first.

---

## Quickstart

### Step 1 — Download Subumbra

The example below uses `/opt/subumbra` — you can use any path you like, just replace it consistently:

```bash
git clone https://github.com/polysemic/Subumbra.git /opt/subumbra
cd /opt/subumbra
```

> If `/opt` is restricted on your system, you may need `sudo mkdir -p /opt/subumbra && sudo chown -R "$USER":"$USER" /opt/subumbra` first, then run the clone.

### Step 2 — Create a shared Docker network

This lets your Dockerized apps talk to Subumbra by name. Run this once:

```bash
docker network create subumbra-net
```

### Step 3 — Create your provider list

Subumbra reads a file called `subumbra.yaml` to know which providers you want to use. This file is **never committed to git** — it lives only on your server.

Start from the minimal template, which includes all supported providers (just comment out the ones you don't use):

```bash
cp subumbra.minimal.yaml subumbra.yaml
```

Now open `subumbra.yaml` in a text editor and:

1. **Delete or comment out** providers you don't have keys for (put a `#` at the start of any line to disable it)
2. **Set `adapters`** to the names of your apps — for example `[litellm, openwebui]` or `[universal]` to use one adapter token for every app. This is to only allow certain apps to use certain keys.

The `secret_ref` values (like `OPENAI_KEY`, `ANTHROPIC_KEY`) are just labels — you'll enter the actual key values in the next step, not here.

> **Want full control over policies or custom providers?** Use `cp subumbra.example.yaml subumbra.yaml` instead. That file documents every available option. See [docs/provider-templates.md](docs/provider-templates.md).

> **Optional automation:** You can also use an automation file. Copy `.env.bootstrap.example` to `.env.bootstrap`, fill in your keys and Cloudflare credentials, then run `./bootstrap.sh`. The file is automatically deleted after a successful run. The `secret_ref` values from `subumbra.yaml` must match the environment variable names in `.env.bootstrap`.

### Step 4 — Run the setup wizard

```bash
./bootstrap.sh
```

The wizard will ask you for:

1. **Your Cloudflare API token** — paste it in (this isn't stored anywhere and is only used for the initial setup and to update the worker)
2. **Your Cloudflare Account ID** — paste it in (this isn't stored anywhere and is only used for the initial setup and to update the worker)
3. **A Worker name** — just press Enter to use the default (`subumbra-proxy`)
4. **Your API keys** — for each provider in your `subumbra.yaml`, it will ask for the key

**Automated Alternative:** If you filled `.env.bootstrap`, all prompts will be skipped and the wizard will use the values from the file.
 
That's it. The wizard automatically:
- Deploys a Cloudflare Worker (your encrypted key vault lives here)
- Generates a fresh RSA key pair — the private key is generated **inside Cloudflare** and never touches your server
- Encrypts your API keys and stores them
- Writes all the access tokens your apps will need into `.env`
- Starts the Subumbra services

### Step 5 — Verify everything is running

```bash
# Check all three services are up
docker compose ps

# Check the proxy is healthy and connected to Cloudflare
curl -sS http://127.0.0.1:10199/health
```

You should see something like:

```json
{"status": "ok", "worker_auth": "ok"}
```

If `worker_auth` says `ok`, you're live. 🎉

---

## Connecting your apps

After bootstrap, your `.env` file contains tokens for each app. Check them:

```bash
grep SUBUMBRA_TOKEN .env
```

You'll see lines like:
```
SUBUMBRA_TOKEN_LITELLM=3fbe4c3f...
SUBUMBRA_TOKEN_OPENWEBUI=19d1262d...
```

Each app gets its **own token** (so you can revoke one without affecting others) and points to Subumbra instead of directly to OpenAI:

| Setting | Value |
|---------|-------|
| `api_base` / base URL | `http://subumbra-proxy:8090/t/<key_id>/...` (from inside Docker) |
| `api_base` / base URL | `http://127.0.0.1:10199/t/<key_id>/...` (from the host or outside Docker) |
| `api_key` | Your app's adapter token (e.g. `SUBUMBRA_TOKEN_LITELLM` from `.env`) |

Where `<key_id>` is the identifier from your `subumbra.yaml` — for example `openai_prod`, `anthropic_prod`.

App-specific setup guides:

- **LiteLLM:** [docs/apps/litellm/install.md](docs/apps/litellm/install.md)
- **OpenWebUI:** [docs/apps/openwebui/install.md](docs/apps/openwebui/install.md)
- **AnythingLLM:** [docs/apps/anythingllm/install.md](docs/apps/anythingllm/install.md)
- **Bifrost / LibreChat / n8n:** [docs/apps/](docs/apps/)

---

## Security properties

Here's what Subumbra actually protects and what it doesn't:

| What it protects | How |
|-----------------|-----|
| API keys at rest on your server | Keys are encrypted immediately and only stored as ciphertext |
| API keys in app configs | Apps only ever see a short-lived proxy token, not your real key |
| Per-app access control | Each app has its own token — revoke one without touching others |
| Policy enforcement | You define which paths and methods each key is allowed to serve |

| What it does **not** protect | Notes |
|------------------------------|-------|
| Cloudflare itself | The private key lives in Cloudflare — Cloudflare is in the trust boundary |
| Your server if fully compromised | An attacker with root on your server can read running container memory |
| Billing/rate limits | Subumbra doesn't cap spend — set limits at the provider level |

---

## Dashboard (UI)

A read-only dashboard is available at `http://127.0.0.1:6563` showing your active keys, usage stats, and audit log.

**To access it:**

| Setup | Configuration |
|-------|---------------|
| Cloudflare Tunnel + Access (recommended for remote access) | Leave `UI_USERNAME` and `UI_PASSWORD` unset in `.env` |
| Simple password on localhost | Set `UI_USERNAME` and `UI_PASSWORD` in `.env`, then `docker compose up -d --force-recreate` |

---

## What's in the box

```
subumbra/
├── docker-compose.yml          ← starts the three local services
├── .env.example                ← template for optional config
├── .env.bootstrap.example      ← template for automation bootstrap
├── subumbra.minimal.yaml       ← copy this to subumbra.yaml to get started
├── subumbra.example.yaml       ← full reference with every option documented
├── bootstrap/                  ← setup wizard and encryption logic
├── subumbra-keys/              ← stores encrypted key records locally
├── subumbra-proxy/             ← the transparent proxy your apps talk to
├── ui/                         ← the read-only dashboard
├── worker/                     ← the Cloudflare Worker (key vault + decrypt)
└── docs/                       ← all documentation
```

---

## More docs

- [Full install guide (Docker from scratch)](docs/subumbra-install.md)
- [Provider templates reference](docs/provider-templates.md)
- [Integration recipes (curl examples per provider)](docs/integration-recipes.md)
- [Operator guide (day-2 operations, recovery)](docs/operator-guide.md)
- [Architecture deep-dive](docs/architecture.md)
- [Developer / council guide](docs/subumbra-developer.md)

---

## ⚠️ Warning: Security and Data Handling

This project handles sensitive credentials (API keys, tokens, and certificates).
* **Do not commit `.env` files** or any files containing secrets to version control.
* **Always back up your secrets** in a secure location before running bootstrap scripts.
* **Understand the risks** before using this software in production.

For detailed security guidance, see:
- [Security Overview](docs/security-overview.md)

---

## Disclaimer

This project was built with AI coding tools ($20/mo plans). I am not a security expert, a coder, or a software engineer. I am not great at git or github, so the development history, branches, and releases may be a bit clunky. I like to build and tinker with things; this is a project I built for myself to try and solve a problem I kept reading about almost every day. If you find it useful, feel free to use it. If you do not, that is fine too.

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/ericchaffey)
