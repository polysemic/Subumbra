# npm — Install & Daily-Driver Workflows

This guide covers how to set up, operate, and maintain Subumbra as the **sole broker** for your npm package publishing workflows. By routing all publishes through Subumbra, your high-value npm registry tokens never exist in plaintext on your laptop, servers, or CI/CD pipelines—they reside securely in your encrypted Cloudflare Durable Object vault.

---

## 🧭 Layered Guide Structure
- [Prerequisites & Core Readiness](#-prerequisites--core_readiness)
- [Step 1: Get a Granular Access Token from npm](#step-1-get-a-granular-access-token-from-npm)
- [Step 2: Add the NPM Declaration to your Manifest](#step-2-add-the-npm-declaration-to-your-manifest)
- [Step 3: Provision / Update the Token Securely (RAM-Only)](#step-3-provision--update-the-token-securely-ram-only)
- [Step 4: Setup Workflow A — Local Laptop / Terminal Publishing](#step-4-setup-workflow-a--local-laptop--terminal-publishing)
- [Step 5: Setup Workflow B — Remote VPS & CI/CD Pipelines (GitHub Actions)](#step-5-setup-workflow-b--remote-vps--cicd-pipelines-github-actions)
- [🔧 Day-2 Token Rotation & Maintenance](#-day-2-token-rotation--maintenance)
- [💡 Deep-Dive: Operator Notes & Custom Tuning](#-deep-dive-operator-notes--custom-tuning)

---

## 🚦 Prerequisites & Core Readiness

Ensure Subumbra is successfully running on your host (e.g., your local development environment or VPS):

```bash
cd /opt/subumbra  # Or your Subumbra workspace directory
docker compose ps
curl -sS http://127.0.0.1:10199/health
```

**Expected Healthy Response:**
```json
{"status":"ok","worker_auth":"ok"}
```

---

## Step 1: Get a Granular Access Token from npm

Subumbra relies on npm **Granular Access Tokens (GAT)** to interact with the registry. GATs are highly secure because they are scoped to specific organizations/packages and naturally bypass interactive 2FA prompt challenges for headless automation.

1. Log into your account on [npmjs.com](https://www.npmjs.com/).
2. Click your profile avatar in the top-right corner and select **Access Tokens**.
3. Click **Generate New Token** and choose **Granular Access Token**.
4. Configure the token:
   - **Token Name**: e.g., `Subumbra Broker`
   - **Expiration**: Select your preferred TTL (e.g., 90 days, 365 days).
   - **Permissions**: Select **Read and Write** (required to publish packages).
   - **Packages and Scopes**: Select your organization or individual user scope (e.g., `@your-scope`).
5. Click **Generate Token** and copy the resulting string (looks like `npm_GAT...`). *Keep this copied in your clipboard.*

---

## Step 2: Add the NPM Declaration to your Manifest

Open `manifest.yaml` (either in `/opt/subumbra/manifest.yaml` on your VPS or in your local daily-driver `Subumbra-Local` workspace) and paste the following key declaration into your `keys:` section.

```yaml
keys:
  - key_id: npm_publish
    type: npm_token
    provider: npmjs
    secret_ref: NPM_TOKEN
    adapters: [npm]
    unique_vault: false
    policy:
      key_id: npm_publish
      policy_id: npm-publish-policy
      protocol: http_rest
      capability_class: custom_rest
      source: env
      target:
        host: registry.npmjs.org
      allow:
        adapters: [npm]
        methods: [GET, PUT, DELETE]  # DELETE is required if you want to unpublish packages
        npm_operations: [publish, query, unpublish]
        path_prefixes: [/@your-scope]   # REPLACE with your exact npm username/scope
        scopes: ["@your-scope"]        # REPLACE with your exact npm username/scope
        content_types: [application/json]
        max_body_bytes: 10485760       # Max request body size allowed (10MB)
      deny:
        max_tarball_bytes: 5242880     # Protects memory by limiting uploads to 5MB
        # Guardrails: Block accidental publishing of secrets
        publish_path_patterns: [.env, .pem, .key, .npmrc, credentials.json]
        publish_content_patterns: [AKIA, npm_, PRIVATE KEY]
```

> [!TIP]
> **Advanced Tuning:** High-level developers can customize the `deny.max_tarball_bytes` limit or expand `publish_path_patterns` to block custom internal credential paths from ever being published upstream.

---

## Step 3: Provision / Update the Token Securely (RAM-Only)

To prevent writing secrets to plaintext files on disk, we pass the token inline as a transient environment variable. 

Run the following command to provision **only this key** without starting a full bootstrap (which would regenerate key pairs and wipe other keys):

```bash
NPM_TOKEN=npm_YOUR_ACCESS_TOKEN_HERE ./bootstrap.sh --provision npm_publish
```
*(Replace `npm_YOUR_ACCESS_TOKEN_HERE` with your actual Granular Access Token generated in Step 1).*

**What this did:**
- Loaded your `manifest.yaml` definition for `npm_publish`.
- Read the token purely in RAM, encrypted it, appended it to `endpoint.json`, pushed it to Cloudflare KV, and cleared the process memory.
- **None of your other active keys or credentials were touched or altered!**

---

## Step 4: Setup Workflow A — Local Laptop / Terminal Publishing

If you publish packages directly from your terminal (`npm publish`) on your local development machine:

### 1. Print your secure `.npmrc` configuration block
Run this command inside your local Subumbra directory:
```bash
./bootstrap.sh --show npm
```

You will get a paste-ready, path-scoped output block like this:
```ini
registry=http://127.0.0.1:10199/t/npm_publish/
//127.0.0.1:10199/t/npm_publish/:_authToken=sub_tok_npm_d93f8e72c842b101c...
```

### 2. Configure your development machine
1. Open your global user config file `~/.npmrc` in your favorite editor.
2. Paste the snippet printed by `--show npm` directly into it and save.

### 3. Publish packages seamlessly
Go to your local package folder (where `package.json` matches your allowed scope, e.g., `@your-scope/my-library`) and run:
```bash
npm publish --access public
```

**How it works transparently:**
- The npm CLI reads your `~/.npmrc` and routes the request to your local Subumbra loopback endpoint.
- Subumbra inspects the package, verifies that no `.env` or credential files are in the tarball, checks the size, fetches the encrypted GAT, decrypts it in the Cloudflare Worker, and securely forwards the publish command upstream.

---

## Step 5: Setup Workflow B — Remote VPS & CI/CD Pipelines (GitHub Actions)

To enable secure automated package publishing from your repository workflows (like GitHub Actions) using the Subumbra deployment on your VPS:

### 1. Export the production consumer token
Run the show command on your VPS:
```bash
./bootstrap.sh --show npm
```

Copy the printed **Adapter Token** (the string starting with `sub_tok_npm_...`).

### 2. Save the Adapter Token in your GitHub Secrets
1. Go to your repository on GitHub.
2. Navigate to **Settings** -> **Secrets and variables** -> **Actions**.
3. Click **New repository secret**.
4. Name the secret **`SUBUMBRA_NPM_ADAPTER_TOKEN`** and paste your copied Subumbra Adapter Token as the value.

### 3. Add the GitHub Action workflow
Add this step block directly in your `.github/workflows/publish.yml` file:

```yaml
name: Publish to npm
on:
  release:
    types: [published]

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          # Tell npm to route requests to your VPS Subumbra endpoint
          registry-url: http://your-vps-ip:8090/t/npm_publish/
      - run: npm publish --access public
        env:
          # Pass the Subumbra Adapter Token as the auth credential
          NODE_AUTH_TOKEN: ${{ secrets.SUBUMBRA_NPM_ADAPTER_TOKEN }}
```

---

## 🔧 Day-2 Token Rotation & Maintenance

When your npm Granular Access Token expires or needs to be rotated, you can update it instantly in one command:

```bash
./bootstrap.sh --rotate-npm-token npm_publish
```

**Workflow:**
- You will be prompted in the terminal to enter and confirm your new `npm_GAT...` token (which is held in RAM only).
- Subumbra encrypts it under your existing vault key pair, writes the update back to `endpoint.json`, and completes the rotation.
- **Your active consumer tokens and `.npmrc` configurations on local laptops and CI pipelines remain exactly the same!** No config files need to be touched.

---

## 🛠️ Mandatory vs. Optional Manifest Fields

To give you maximum architectural control, Subumbra separates core routing constraints (which are **mandatory** to keep the zero-trust contract valid) from supplementary checks (which are entirely **optional** and can be omitted).

### 1. Top-Level Key Settings
- `key_id`, `provider`, `secret_ref`, `adapters`, `unique_vault`: **Mandatory**.

### 2. Under the `policy:` block
| Field Name | Status | Purpose / Behavior if Omitted |
|---|---|---|
| `policy_id`, `protocol`, `capability_class`, `source`, `target` | **Mandatory** | Required to identify, classify, and route calls at the Worker boundary. |
| `auth.scheme` | **Mandatory** | Declares how to inject the decrypted credential (e.g., `bearer`). |
| `allow.consumers` | **Mandatory** | Only adapter names in this list can utilize the key. |
| `allow.methods` | **Mandatory** | List of accepted HTTP verbs (must include `GET` and `PUT` for npm). |
| `allow.path_prefixes` | **Mandatory** | The URL path prefix allowed to route through (must start with `/`). |
| `allow.content_types` | **Mandatory** | Must include `application/json` for package uploads. |
| `allow.max_body_bytes` | **Mandatory** | Max allowed incoming request size. |
| `allow.npm_operations` | *Optional* | List of permitted npm commands (defaults to `publish` and `query` if omitted). |
| `allow.scopes` | *Optional* | Restricts uploads to specific scopes (e.g. `@my-org`). Omit if your package is unscoped/public. |
| `deny` (entire section) | *Optional* | **Completely Optional**. If omitted, no tarball-size or file-pattern checks will be run. |
| `deny.max_tarball_bytes`| *Optional* | Enforces max tarball upload size. |
| `deny.publish_path_patterns` | *Optional* | Safe-substring paths to reject (e.g. `.env`, `.pem`). |
| `deny.publish_content_patterns` | *Optional* | Safe-substring content matches to reject in text files. |

---

## 🛡️ Human-in-the-Loop: Adding Janus (Approval Gate) to the Workflow

**Janus** is Subumbra's per-call approval gate Durable Object (`SubumbraJanus`). When active, high-risk requests (such as package publishes) are **held in a pending state** at the Worker boundary until you review and approve them via your secure Subumbra dashboard.

For everyday development, you only want to gate **actual package releases** (HTTP `PUT` requests) while allowing metadata queries (`GET`) to run instantly without human intervention.

### How to add Janus to your manifest:
Simply append a `gate` block to your npm key policy:

```yaml
keys:
  - key_id: npm_publish
    type: npm_token
    provider: npmjs
    secret_ref: NPM_TOKEN
    adapters: [npm]
    unique_vault: false
    policy:
      key_id: npm_publish
      policy_id: npm-publish-policy
      # ... [keep your existing policy configuration here] ...
      
      # ADD THIS GATE BLOCK:
      gate:
        require_approval:
          - timeout_seconds: 120    # How long the approval window stays open (2 minutes)
            when:
              method: PUT           # GATES PUBLISHING ONLY (GET requests pass through instantly)
```

### The Janus Operational Workflow:
1. Run `./bootstrap.sh --provision npm_publish` to apply the updated policy to Cloudflare.
2. In your terminal or CI pipeline, trigger your release:
   ```bash
   npm publish --access public
   ```
3. Your terminal or CI job will appear to **pause/hang**. Under the hood, the Cloudflare Worker is holding your request in the secure Durable Object gate!
4. Open your **Subumbra Dashboard** (e.g., `https://subumbra.yourdomain.com`).
5. A vibrant **Pending Approvals** card will blink on your Overview screen, showing:
   - **Key ID**: `npm_publish`
   - **Method**: `PUT`
   - **Timeout**: Live countdown timer (2 minutes)
6. Click **Approve**.
7. Instantly, the Durable Object releases the request, decrypts your high-value npm token, publishes your package upstream, and your terminal/CI runner prints a successful release message and exits!

---

## 💡 Deep-Dive: Operator Notes & Custom Tuning


### How npm publishing works under the hood
1. Before publishing, the npm CLI sends a `GET` metadata request to query existing versions.
2. Next, the CLI packages the entire directory, encodes it as a base64 tarball, wraps it inside a JSON "packument," and sends a `PUT` request to upload the package.
3. Subumbra intercepts both calls under the same `/t/npm_publish/...` path-scoped policy.

### Expected Fail-Closed Behaviors
If someone attempts an unauthorized action, the broker will block the request and return specific `403` error codes:
- **`npm_operation_not_allowed`**: Attempted an operation not in `allow.npm_operations` (e.g. `owner` modification).
- **`publish_tarball_too_large`**: The base64 tarball size exceeded the configured `deny.max_tarball_bytes`.
- **`publish_identity_mismatch`**: The package name inside `package.json` does not match the URL path.
- **`publish_scope_not_allowed`**: The package scope is outside the declared `allow.scopes` array.
- **`publish_deny_pattern_match`**: The tarball contains forbidden files (e.g., `.npmrc`, `.env`) or matches blacklisted text strings in the codebase.
- **`publish_invalid_packument`**: The incoming HTTP body is not a valid npm publish format.
