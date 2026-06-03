# Subumbra — Keep Your Secrets Out of Reach

[![DeepWiki](https://img.shields.io/badge/DeepWiki-polysemic%2FSubumbra-blue)](https://deepwiki.com/polysemic/Subumbra)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/polysemic/Subumbra/badge)](https://scorecard.dev/viewer/?uri=github.com/polysemic/Subumbra)
[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg)](LICENSE)

> **Alpha release** — designed for self-hosters and tinkerers who want to test this early. Not yet recommended for production use. Breaking and incompatible changes will occur, sometimes requiring manual updates to services and configs, and occasionally wiping stored data.

Subumbra is a **split-trust secret broker**. It sits between the things you run — apps, scripts, CI/CD pipelines, agents — and the services they talk to, so your real credentials never live in plaintext on your machine.

Instead of pasting an API key into a config file, an SSH key onto a build runner, or an npm token into a CI secret, you hand those secrets to Subumbra **once**. It splits them so that **no single machine you operate ever holds the whole secret**, and then releases them only to callers you've authorized, one request at a time, inside a time-boxed session.

**In plain terms:** your apps, scripts, and agents talk to Subumbra. Subumbra talks to OpenAI / GitHub / npm / whoever. Your real secrets stay locked away — and even a caller that gets compromised never sees them.

### What Subumbra can hold today

| Secret type | What it brokers | Guide |
|-------------|-----------------|-------|
| **API keys** | LLM and other provider keys (OpenAI, Anthropic, Groq, …) used by apps like LiteLLM, OpenWebUI, n8n | [Connecting apps](#connecting-your-apps) |
| **SSH keys** | ed25519 keys for `git push`, deploys, server access — signature-only, the key never leaves the vault | [docs/ssh-guide.md](docs/ssh-guide.md) |
| **npm tokens** | `npm publish` / install from your laptop or CI without the token ever touching disk | [docs/apps/npm/install.md](docs/apps/npm/install.md) |
| **Generic HTTP APIs** | Any REST service via a generic `http_rest` policy (bearer / basic / header / query auth) | [docs/integration-recipes.md](docs/integration-recipes.md) |

→ [How it works under the hood](docs/architecture.md) · [Security model & honest limits](#security-model-what-it-protects-and-what-it-doesnt) · [Planned features](ROADMAP.md)

---

## Choose your path

Subumbra scales from "I just want my API key out of a `.env` file" to "broker every credential my whole CI fleet uses." Start wherever you are:

- **🟢 Just getting started** — follow the [Quickstart](#quickstart) below. ~15 minutes, one provider key, copy-paste commands. No security background needed.
- **🟡 Adding SSH or npm** — do the Quickstart first, then jump to the [SSH guide](docs/ssh-guide.md) or [npm guide](docs/apps/npm/install.md).
- **🔴 CI/CD & automation** — [SSH in pipelines](docs/ssh-ci-cd.md) and the npm guide's [GitHub Actions section](docs/apps/npm/install.md) cover unattended flows.
- **🟣 Power user / security review** — go straight to the [Architecture deep-dive](docs/architecture.md), [Security overview](docs/security-overview.md), and [full manifest reference](manifest.example.yaml).

---

## Quickstart

> This is the green path: one Linux box, Docker, one provider key. SSH and npm come later — they build on the same setup.

### Before you start

You'll need:

- A Linux server (VPS or homelab) with **Docker** installed. Any distro with Docker should work; tested on Ubuntu 24.04 LTS.
  > Don't have Docker yet? Follow the [full install guide](docs/subumbra-install.md) first.
- A [**Cloudflare account**](https://cloudflare.com) with the **Workers Paid plan** ($5/month) — this is the second half of the split, where the part of your secret that unlocks the rest is held. A free account may work but isn't guaranteed.
- A **Cloudflare API token** (create at [dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens) — use the "Edit Cloudflare Workers" template and add `Workers KV Storage: Edit`)
- Your **Cloudflare Account ID** (shown in the dashboard URL, or after creating the token)
- At least one provider API key (e.g. an OpenAI key starting with `sk-...`)

### Step 1 — Download Subumbra

The examples use `/opt/subumbra` — any path works, just stay consistent:

```bash
git clone https://github.com/polysemic/Subumbra.git /opt/subumbra
cd /opt/subumbra
```

> If `/opt` is restricted: `sudo mkdir -p /opt/subumbra && sudo chown -R "$USER":"$USER" /opt/subumbra` first, then clone.

### Step 2 — Create a shared Docker network

Lets your Dockerized apps reach Subumbra by name. Run once:

```bash
docker network create subumbra-net
```

### Step 3 — Tell Subumbra what to hold

Subumbra reads a file called `manifest.yaml` (your **manifest**) to know which secrets you want it to broker. This file is **never committed to git** — it lives only on your server.

Start from the minimal template, which lists all supported providers (comment out the ones you don't use):

```bash
cp manifest.minimal.yaml manifest.yaml
```

Open `manifest.yaml` and:

1. **Delete or comment out** providers you don't have keys for (a leading `#` disables a line)
2. **Set `adapters`** to the names of the apps allowed to use each key — e.g. `[litellm, openwebui]`, or `[universal]` to share one token across every app. (This is how Subumbra limits *which* caller may use *which* key.)

The `secret_ref` values (like `OPENAI_KEY`) are just labels — you enter the actual secret values in the next step, not here.

The built-in signed templates ship with sensible default `velocity` and circuit-breaker limits. To change them, use a local template override or the full inline policy form.

> **Want SSH keys or npm tokens too?** They're declared in this same manifest. Get the Quickstart working first, then see the [SSH guide](docs/ssh-guide.md) and [npm guide](docs/apps/npm/install.md) — each shows the exact manifest block to add.

> **Want full control over policies or custom HTTP APIs?** Use `cp manifest.example.yaml manifest.yaml` instead — that file documents every available option. See [docs/provider-templates.md](docs/provider-templates.md).

### Step 4 — Run the setup wizard

First, a quick local trust check on the code you just cloned:

```bash
./scripts/subumbra-verify --verbose
```

> `./bootstrap.sh` runs this preflight automatically before it reads any secret. It warns on unsigned or lightweight Git tags by default; strict signed-tag enforcement is opt-in.

Then run the wizard:

```bash
./bootstrap.sh
```

It will ask for:

1. **Cloudflare API token** — not stored; used only for initial setup and Worker updates
2. **Cloudflare Account ID** — not stored; same as above
3. **Worker name** — press Enter for the default (`subumbra-proxy`)
4. **Optional Cloudflare Tunnel / Access credentials** — provide if you use them, otherwise leave blank
5. **Your secrets** — for each entry in `manifest.yaml`, it prompts for the value

> **Prefer automation?** Copy `.env.bootstrap.example` to `.env.bootstrap`, fill in your secrets and Cloudflare credentials, and run `./bootstrap.sh` — all prompts are skipped and the file is shredded after a successful run. The `secret_ref` names in `manifest.yaml` must match the variable names in `.env.bootstrap`.

That's it. The wizard automatically:

- Deploys a Cloudflare Worker (the remote half of your split-trust vault)
- Generates an RSA key pair **inside Cloudflare** — the private key never touches your server
- Encrypts your secrets and stores only the locked-up halves locally
- Writes the access tokens your apps need into `.env`
- Starts the Subumbra services

### Step 5 — Verify it's running

```bash
docker compose ps                              # services should be "up (healthy)"
curl -sS http://127.0.0.1:10199/health         # proxy health + Cloudflare link
```

You should see:

```json
{"status": "ok", "worker_auth": "ok"}
```

If `worker_auth` is `ok`, you're live. 🎉 Next: [connect an app](#connecting-your-apps), then [open a session](#sessions--opening-the-vault).

---

## Connecting your apps

After bootstrap, your `.env` holds one token per app:

```bash
grep SUBUMBRA_TOKEN .env
```

```
SUBUMBRA_TOKEN_LITELLM=3fbe4c3f...
SUBUMBRA_TOKEN_OPENWEBUI=19d1262d...
```

Each app gets its **own token** (revoke one without affecting others) and points at Subumbra instead of directly at the provider:

| Setting | Value |
|---------|-------|
| base URL (inside Docker) | `http://subumbra-proxy:8090/t/<key_id>/...` |
| base URL (host / outside Docker) | `http://127.0.0.1:10199/t/<key_id>/...` |
| `api_key` | the app's consumer token (e.g. `SUBUMBRA_TOKEN_LITELLM`) |

`<key_id>` is the identifier from your `manifest.yaml` — e.g. `openai_prod`.

App-specific guides:

- **LiteLLM:** [docs/apps/litellm/install.md](docs/apps/litellm/install.md)
- **OpenWebUI:** [docs/apps/openwebui/install.md](docs/apps/openwebui/install.md)
- **AnythingLLM:** [docs/apps/anythingllm/install.md](docs/apps/anythingllm/install.md)
- **GitHub / Bifrost / LibreChat / n8n and more:** [docs/apps/](docs/apps/)

For non-app callers — a `curl` script, a custom service, an agent — see the [integration recipes](docs/integration-recipes.md).

---

## Beyond API keys

The same vault, sessions, and per-caller tokens work for other credential types. Each links back to the manifest you already created.

### SSH keys

Subumbra can hold ed25519 SSH private keys and expose **signature-only** access through a local agent socket — the key itself never leaves the Cloudflare vault. Use it for `git push`, deploys, and server access without a private key sitting on the box.

→ [docs/ssh-guide.md](docs/ssh-guide.md) (daily use) · [docs/ssh-ci-cd.md](docs/ssh-ci-cd.md) (pipelines)

### npm tokens

Broker `npm publish` (and install) so your registry token never lands in `.npmrc`, a `.env`, or a CI secret. Policy can restrict allowed package scopes, npm operations, and tarball size, and scan publishes for accidentally-bundled secrets.

→ [docs/apps/npm/install.md](docs/apps/npm/install.md) (includes a GitHub Actions flow)

### Any HTTP API

For services without a built-in template, declare a generic `http_rest` policy with `bearer`, `basic`, `header`, or `query` auth and route requests through the same `/t/<key_id>/` path.

→ [docs/integration-recipes.md](docs/integration-recipes.md) · full reference in [manifest.example.yaml](manifest.example.yaml)

---

## Sessions — opening the vault

After setup, Subumbra starts **locked**. Apps are configured and connected, but **no secret is released until you open a session.** Think of a time-lock safe: open it for as long as you need, and it closes itself.

```bash
./bootstrap.sh --session start --ttl 8h --consumers all
```

Be more specific — only certain apps, only certain keys:

```bash
./bootstrap.sh --session start --ttl 2h --consumers litellm,openwebui --keys openai_prod
```

Check or close:

```bash
./bootstrap.sh --session status      # what's open right now
./bootstrap.sh --session end         # close the active session
./bootstrap.sh --session end --all   # close everything immediately
```

**Why locked by default?** If your server is breached or a token is stolen while no session is active, the attacker gets nothing — the vault stays shut. Sessions mean your secrets are reachable only for exactly as long as you need. There is no permanent "always open" mode; the lockdown is intentional, and sessions are how you briefly lift it.

> **High-consequence actions** (e.g. an SSH deploy, an npm publish) can additionally require a human tap-to-approve via **Janus**, Subumbra's approval layer. See [docs/gate.md](docs/gate.md).

---

## Security model — what it protects, and what it doesn't

Subumbra's core property is **split-trust**: your secret is split so the local half is useless on its own, and the half that unlocks it (an RSA private key) is generated inside Cloudflare and never reaches a machine you operate. We try to be precise about the boundaries rather than overpromising.

| What it protects | How |
|------------------|-----|
| **Secrets at rest on your server** | Only encrypted ciphertext + a wrapped key are stored locally — useless without the Cloudflare-held private key |
| **A compromised app, agent, or plugin** | The caller only ever holds a short-lived consumer token, never the real secret — so a prompt injection, plugin, or app CVE has nothing to leak |
| **Secrets in app/CI configs** | Apps and pipelines see a revocable proxy token, not your key |
| **Per-caller access control** | Each caller has its own token; policy limits which keys, paths, methods, and (for npm) operations it may use |
| **Stolen tokens while locked** | No active session ⇒ no secret released, even with a valid token |

| What it does **not** fully protect | Be aware |
|------------------------------------|----------|
| **The Cloudflare boundary itself** | The private key lives in Cloudflare — it is inside the trust boundary by design |
| **A fully compromised server *during an open session*** | An attacker with root can't extract the stored secret, but while a session is open they can *use* a consumer token to make in-policy requests. Short TTLs and Janus approvals limit this. |
| **Billing / rate limits** | Subumbra doesn't cap spend — set limits at the provider |

For the full threat model, the trust-domain reasoning, and where the plaintext briefly exists, see [docs/security-overview.md](docs/security-overview.md) and [docs/architecture.md](docs/architecture.md).

---

## Dashboard (UI)

A **read-only** dashboard at `http://127.0.0.1:6563` shows active keys, usage, sessions, and the audit log.

| Setup | Configuration |
|-------|---------------|
| Cloudflare Tunnel + Access (recommended for remote access) | Leave `UI_USERNAME` / `UI_PASSWORD` unset in `.env` |
| Simple password on localhost | Set `UI_USERNAME` and `UI_PASSWORD` in `.env`, then `docker compose up -d --force-recreate` |

---

## What's in the box

```
subumbra/
├── docker-compose.yml          ← starts the local services
├── .env.example                ← template for optional config
├── .env.bootstrap.example      ← template for automation bootstrap
├── manifest.minimal.yaml       ← copy to manifest.yaml to get started
├── manifest.example.yaml       ← full reference with every option documented
├── bootstrap/                  ← setup wizard + encryption logic
├── subumbra-keys/              ← stores encrypted secret records locally
├── subumbra-proxy/             ← the transparent proxy your callers talk to
├── subumbra-agent/             ← SSH agent (signature-only key access)
├── ui/                         ← the read-only dashboard
├── worker/                     ← the Cloudflare Worker (vault + decrypt)
└── docs/                       ← all documentation
```

---

## More docs

**Getting set up**
- [Full install guide (Docker from scratch)](docs/subumbra-install.md)
- [Cloudflare setup guide](docs/cloudflare-setup.md)
- [Cloudflare Tunnel & Access (remote UI)](docs/cloudflare-tunnel-access.md)

**Per credential type**
- [SSH guide](docs/ssh-guide.md) · [SSH in CI/CD](docs/ssh-ci-cd.md)
- [npm publishing](docs/apps/npm/install.md)
- [Integration recipes (curl per provider / generic HTTP)](docs/integration-recipes.md)
- [Provider templates reference](docs/provider-templates.md) · [provider matrix](docs/provider-matrix.md)

**Operating & understanding it**
- [Operator guide (day-2 operations, recovery)](docs/operator-guide.md)
- [Janus approvals](docs/gate.md)
- [Architecture deep-dive](docs/architecture.md)
- [Security overview](docs/security-overview.md)
- [Developer / council guide](docs/subumbra-developer.md)

---

## ⚠️ Security and data handling

This project handles sensitive credentials (API keys, SSH keys, tokens, certificates).

- **Never commit `.env` files** or anything containing secrets.
- **Back up your secrets** somewhere secure before running bootstrap.
- **Understand the risks** before relying on this in production.

See: [Security Overview](docs/security-overview.md) · [Release signing public key](docs/release-signing-key.pub) · [Attribution & Third-Party Licenses](ATTRIBUTION.md)

---

## Disclaimer

This project was built with AI coding tools ($20/mo plans). I am not a security expert, a coder, or a software engineer, and I'm not great at git, so the development history, branches, and releases may be clunky. I like to build and tinker; this is a problem I kept reading about almost daily, planned out, and had AI build. If you find it useful, use it. If not, that's fine too.

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/ericchaffey)
<!-- subumbra daily-driver push test -->
