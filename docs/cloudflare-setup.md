# Cloudflare Setup for Subumbra

This is the beginner-friendly Cloudflare guide for Subumbra.

It explains:

- what Cloudflare does in Subumbra
- which credentials are required and why
- how to choose between **manual / bring-your-own** setup and **bootstrap auto-provision**
- what to put in `.env.bootstrap`
- how to lock things down afterward

Subumbra does **not** require Cloudflare Tunnel or Cloudflare Access to run, but it **does** require Cloudflare Workers for the Worker/vault side of the architecture.

---

## 1. What Cloudflare Does in Subumbra

Cloudflare can play up to four separate roles:

1. **Worker runtime**
   - Required.
   - The Subumbra Worker handles vault custody, decryption control flow, and policy enforcement.

2. **Workers KV**
   - Required.
   - Stores structured registry metadata used by the Worker.

3. **Cloudflare Tunnel**
   - Optional.
   - Exposes the Subumbra UI through a Cloudflare-managed public hostname without directly opening the UI port.

4. **Cloudflare Access**
   - Optional.
   - Protects the Worker and/or UI behind Cloudflare authentication and service-token controls.

The most important distinction is:

- **`CF_API_TOKEN`** is **bootstrap/deploy authority**
- **`TUNNEL_TOKEN`** and **`CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`** are **runtime credentials**

Subumbra writes runtime credentials into `.env`, but it does **not** retain `CF_API_TOKEN` in `.env`.

---

## 2. Choose Your Setup Path

There are three practical ways to use Cloudflare with Subumbra.

### Option A - Minimal Cloudflare

Use this if you only want the required Worker + KV deployment.

You provide:

```bash
CF_API_TOKEN=...
CF_ACCOUNT_ID=...
CF_WORKER_NAME=subumbra-proxy
```

You do **not** use:

- `CF_ZONE_ID`
- `CF_TUNNEL_HOSTNAME`
- `TUNNEL_TOKEN`
- `CF_ACCESS_CLIENT_ID`
- `CF_ACCESS_CLIENT_SECRET`

This is the simplest path.

### Option B - Manual / Bring Your Own Tunnel and Access

Use this if you want Tunnel and/or Access, but you prefer to create them yourself in Cloudflare first.

You provide the normal Worker bootstrap values plus one or more runtime credentials:

```bash
CF_API_TOKEN=...
CF_ACCOUNT_ID=...
CF_WORKER_NAME=subumbra-proxy

TUNNEL_TOKEN=...
CF_ACCESS_CLIENT_ID=...
CF_ACCESS_CLIENT_SECRET=...
```

Subumbra will use those secrets, but it will **not** try to create Tunnel or Access resources for you.

#### B.1 - Provide only `TUNNEL_TOKEN`

Use this if:

- you want Cloudflare Tunnel to expose the UI or another Subumbra-facing hostname
- but you do **not** want Cloudflare Access protecting the Worker

What you provide:

```bash
CF_API_TOKEN=...
CF_ACCOUNT_ID=...
CF_WORKER_NAME=subumbra-proxy

TUNNEL_TOKEN=...
```

Purpose:

- gives the optional `cloudflared` container permission to join your existing Cloudflare Tunnel
- lets you publish the UI through a Cloudflare-managed hostname without directly exposing the UI port on the public internet

Benefits:

- simpler than full Tunnel + Access
- useful for remote access to the UI
- keeps Tunnel lifecycle under your control in Cloudflare
- no Access service-token plumbing required

Drawbacks:

- does **not** protect the Worker with Cloudflare Access
- does **not** give you service-token authentication between proxy and Worker
- if you want strong edge auth for the Worker, this path is incomplete

Best fit:

- operators who want remote UI exposure through Tunnel
- operators who are not yet using CF Access for the Worker

#### B.2 - Provide only `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`

Use this if:

- you want the Worker protected by Cloudflare Access
- but you do **not** need Cloudflare Tunnel for the UI

What you provide:

```bash
CF_API_TOKEN=...
CF_ACCOUNT_ID=...
CF_WORKER_NAME=subumbra-proxy

CF_ACCESS_CLIENT_ID=...
CF_ACCESS_CLIENT_SECRET=...
```

Purpose:

- lets the Subumbra proxy authenticate to an Access-protected Worker using a Cloudflare service token
- protects the Worker entrypoint behind Cloudflare Access checks

Benefits:

- stronger edge protection for the Worker
- machine-to-machine authentication path is explicit and scoped
- no Tunnel setup required
- good if your UI is only local or protected some other way

Drawbacks:

- does **not** help expose the UI remotely
- adds Worker-side Access configuration complexity
- if the Access app or policy is misconfigured, proxy health may show `worker_auth` problems even when the VPS stack itself is fine

Best fit:

- operators who want to harden the Worker path first
- setups where the UI stays localhost-only or is protected separately

#### B.3 - Provide both Tunnel and CF Access

Use this if:

- you already created both the Tunnel and the Access service token yourself
- and you want Subumbra to consume them without creating Cloudflare resources automatically

What you provide:

```bash
CF_API_TOKEN=...
CF_ACCOUNT_ID=...
CF_WORKER_NAME=subumbra-proxy

TUNNEL_TOKEN=...
CF_ACCESS_CLIENT_ID=...
CF_ACCESS_CLIENT_SECRET=...
```

Purpose:

- `TUNNEL_TOKEN` lets `cloudflared` expose the UI through your existing Tunnel
- `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` let the proxy reach an Access-protected Worker

Functions of each part:

- **Tunnel** handles public network ingress to the UI hostname
- **CF Access service token** handles authenticated proxy-to-Worker requests when the Worker is protected

Benefits:

- full manual control over Cloudflare objects
- avoids broad bootstrap-time Cloudflare auto-provisioning
- works well if you already manage Tunnel and Access centrally
- lets you keep Subumbra’s runtime behavior while keeping Cloudflare lifecycle manual

Drawbacks:

- highest manual setup burden in the BYOC family
- you must create and maintain multiple Cloudflare resources correctly yourself
- harder for beginners than minimal setup or single-feature BYOC paths

Best fit:

- operators already comfortable with Cloudflare
- environments where Cloudflare resources are centrally managed outside Subumbra
- users who want the functionality of full Cloudflare integration without letting bootstrap create resources

### Option C - Bootstrap Auto-Provision

Use this if you want Subumbra to create Cloudflare Tunnel / DNS / Access resources for you.

You provide:

```bash
CF_API_TOKEN=...
CF_ACCOUNT_ID=...
CF_WORKER_NAME=subumbra-proxy
CF_ZONE_ID=...
CF_TUNNEL_HOSTNAME=subumbra.example.com
```

Optional naming overrides:

```bash
CF_TUNNEL_NAME=subumbra-proxy-tunnel
CF_ACCESS_APP_NAME=subumbra-proxy-worker-access
CF_SERVICE_TOKEN_NAME=subumbra-proxy-service-token
```

If runtime credentials are absent, bootstrap may create them and then write:

- `TUNNEL_TOKEN`
- `CF_ACCESS_CLIENT_ID`
- `CF_ACCESS_CLIENT_SECRET`

#### What each required value is

##### `CF_API_TOKEN`

What it is:

- the Cloudflare API token Subumbra uses for bootstrap-time Cloudflare actions

Where to find or create it:

- Cloudflare dashboard
- `My Profile -> API Tokens`
- Cloudflare docs: https://developers.cloudflare.com/fundamentals/api/get-started/create-token/

Purpose in Option C:

- deploy the Worker
- manage Workers KV
- create Tunnel / DNS / Access resources when auto-provision is enabled

##### `CF_ACCOUNT_ID`

What it is:

- the Cloudflare account identifier for the account that owns the Worker and related resources

Where to find it:

- Cloudflare dashboard account overview / sidebar

Purpose in Option C:

- tells Subumbra which Cloudflare account to target for Worker, Tunnel, and Access API calls

##### `CF_WORKER_NAME`

What it is:

- the Cloudflare Worker script name Subumbra will deploy

Where to choose it:

- you choose this name yourself
- a common value is:

```bash
CF_WORKER_NAME=subumbra-proxy
```

Purpose in Option C:

- identifies the Worker script in Cloudflare
- forms the Worker hostname, usually something like:
  - `https://<worker-name>.<subdomain>.workers.dev`
- also acts as the naming base for auto-provisioned Tunnel / Access objects when you do not override their names

##### `CF_ZONE_ID`

What it is:

- the Cloudflare Zone ID for the DNS zone where you want Subumbra to create the Tunnel-facing hostname

Where to find it:

- open the site/zone in the Cloudflare dashboard
- the Zone ID is shown on the zone overview page

Purpose in Option C:

- required for DNS record creation
- lets bootstrap create the CNAME record that points your chosen hostname at the Cloudflare Tunnel

##### `CF_TUNNEL_HOSTNAME`

What it is:

- the public hostname you want Cloudflare to create for the Tunnel side of Subumbra

Example:

```bash
CF_TUNNEL_HOSTNAME=subumbra.example.com
```

Where it comes from:

- you choose it from a domain/zone you control in Cloudflare

Purpose in Option C:

- tells bootstrap which DNS hostname to create
- that hostname is pointed at the Tunnel target (`<tunnel_id>.cfargotunnel.com`)

#### What the optional naming overrides are

These are not secrets. They are naming controls.

##### `CF_TUNNEL_NAME`

What it is:

- the name Cloudflare should use for the created Tunnel object

Where it comes from:

- you choose it
- if omitted, Subumbra derives a default from `CF_WORKER_NAME`

Purpose:

- makes the Tunnel easier to identify later in the Cloudflare dashboard

##### `CF_ACCESS_APP_NAME`

What it is:

- the display name for the Cloudflare Access application bootstrap creates

Where it comes from:

- you choose it
- if omitted, Subumbra derives a default from `CF_WORKER_NAME`

Purpose:

- helps you recognize the Access app in the Cloudflare dashboard
- the Access app is what protects the Worker hostname when Access auto-provision is enabled

##### `CF_SERVICE_TOKEN_NAME`

What it is:

- the display name for the Cloudflare Access service token bootstrap creates

Where it comes from:

- you choose it
- if omitted, Subumbra derives a default from `CF_WORKER_NAME`

Purpose:

- helps you recognize the generated service token in Cloudflare
- the generated `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` pair comes from this service token

#### What bootstrap generates for you

If Option C creates the optional runtime credentials, it writes them into `.env`:

##### `TUNNEL_TOKEN`

What it is:

- the runtime credential used by `cloudflared`

Purpose:

- allows the `cloudflared` container to connect to the created Tunnel

##### `CF_ACCESS_CLIENT_ID`

What it is:

- the public half of the Cloudflare Access service-token pair

Purpose:

- sent by the proxy to the Worker when the Worker is protected by Access

##### `CF_ACCESS_CLIENT_SECRET`

What it is:

- the secret half of the Cloudflare Access service-token pair

Purpose:

- paired with `CF_ACCESS_CLIENT_ID` so the proxy can authenticate to the Access-protected Worker

---

## 3. Where to Click in Cloudflare

These are the most useful Cloudflare dashboard/docs starting points.

### Create or manage API tokens

- Dashboard: `My Profile -> API Tokens`
- Docs: https://developers.cloudflare.com/fundamentals/api/get-started/create-token/

### Learn how Cloudflare token scoping works

- Docs: https://developers.cloudflare.com/fundamentals/api/how-to/create-via-api/

### Find account and zone information

- Account ID: Cloudflare dashboard sidebar / account overview
- Zone ID: site overview page for the domain you want to use

### Create or inspect Tunnel tokens

- Dashboard: `Networking -> Tunnels`
- Docs: https://developers.cloudflare.com/tunnel/advanced/tunnel-tokens/

### Understand Tunnel replicas / runtime model

- Docs: https://developers.cloudflare.com/tunnel/configuration/

### Choose the right Access application type

- Docs: https://developers.cloudflare.com/cloudflare-one/access-controls/applications/choose-application-type/

For Subumbra:

- use **Workers** / self-hosted Worker protection for the Worker hostname
- use Tunnel/public-hostname style protection for the UI hostname

### Create service tokens for Access

- Docs: https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/

---

## 4. The Credentials and What They Mean

### `CF_API_TOKEN`

Purpose:

- deploy Worker code
- manage Workers KV
- optionally create Tunnel / DNS / Access resources

Danger:

- this is the most powerful Subumbra-related Cloudflare credential
- if stolen, it can modify your Cloudflare-side Subumbra infrastructure

Subumbra behavior:

- used during bootstrap and day-2 Cloudflare management commands
- **not** retained in `.env`

### `CF_ACCOUNT_ID`

Purpose:

- tells Subumbra which Cloudflare account to target

Danger:

- low by itself
- still sensitive operational context

### `CF_ZONE_ID`

Purpose:

- required only when bootstrap needs to create DNS records

Danger:

- low by itself

### `TUNNEL_TOKEN`

Purpose:

- runtime credential for the `cloudflared` container

Danger:

- anyone with this token can run a replica of that tunnel

Subumbra behavior:

- stored in `.env` when used
- can be rotated with `./bootstrap.sh --update-tunnel`

### `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`

Purpose:

- service-token pair used by the proxy to reach an Access-protected Worker

Danger:

- these are machine-to-machine auth credentials
- if stolen, they can authenticate to the Access-protected application they were issued for

Subumbra behavior:

- stored in `.env` when used
- can be rotated with `./bootstrap.sh --update-access`

---

## 5. What Goes in `.env.bootstrap`

`.env.bootstrap` is the **bootstrap-time input file**.

Use it when you want:

- non-interactive setup
- repeatable VPS setup
- one place to stage Cloudflare bootstrap values and provider secrets

Template:

- [`.env.bootstrap.example`](../.env.bootstrap.example)

### Minimal Worker/KV only example

```bash
CF_API_TOKEN=REPLACE_ME
CF_ACCOUNT_ID=REPLACE_ME
CF_WORKER_NAME=subumbra-proxy

TOKEN_TTL_DAYS=365

OPENAI_KEY=REPLACE_ME
```

### Manual Tunnel / Access example

```bash
CF_API_TOKEN=REPLACE_ME
CF_ACCOUNT_ID=REPLACE_ME
CF_WORKER_NAME=subumbra-proxy

TUNNEL_TOKEN=REPLACE_ME
CF_ACCESS_CLIENT_ID=REPLACE_ME
CF_ACCESS_CLIENT_SECRET=REPLACE_ME
```

### Auto-provision example

```bash
CF_API_TOKEN=REPLACE_ME
CF_ACCOUNT_ID=REPLACE_ME
CF_WORKER_NAME=subumbra-proxy

CF_ZONE_ID=REPLACE_ME
CF_TUNNEL_HOSTNAME=subumbra.example.com

# optional naming overrides
# CF_TUNNEL_NAME=subumbra-proxy-tunnel
# CF_ACCESS_APP_NAME=subumbra-proxy-worker-access
# CF_SERVICE_TOKEN_NAME=subumbra-proxy-service-token
```

What `.env.bootstrap` is **for**:

- bootstrap-time authority
- one-time setup input
- optional automation

What `.env.bootstrap` is **not** for:

- long-term runtime secrets storage
- a permanent place to keep your master Cloudflare API token

After a successful full bootstrap, the host wrapper shreds `.env.bootstrap`.

---

## 6. Step-by-Step Walkthrough

### Step 1 - Decide your Cloudflare path

Choose one:

- minimal Worker/KV only
- manual Tunnel/Access
- auto-provision Tunnel/Access

If you are unsure, start with:

- minimal Worker/KV only, or
- manual Tunnel/Access if you already know Cloudflare well

### Step 2 - Create a Cloudflare API token

Open:

- https://dash.cloudflare.com/profile/api-tokens

At minimum, Subumbra needs Worker/KV deploy authority.

If you want auto-provision, the token also needs:

- Tunnel write access
- DNS edit access for the chosen zone
- Access app/policy/service-token lifecycle access

### Step 3 - Gather IDs

Find:

- `CF_ACCOUNT_ID`
- `CF_WORKER_NAME`
- `CF_ZONE_ID` if using auto-provision

If using auto-provision, also decide:

- `CF_TUNNEL_HOSTNAME`

Example:

```bash
CF_TUNNEL_HOSTNAME=subumbra.example.com
```

### Step 4 - If using manual Tunnel, get `TUNNEL_TOKEN`

Open:

- `Networking -> Tunnels`

Then:

1. open the tunnel
2. choose **Add a replica**
3. copy the `cloudflared` command
4. extract the `eyJ...` token string

Reference:

- https://developers.cloudflare.com/tunnel/advanced/tunnel-tokens/

### Step 5 - If using manual Access, create a service token

Open:

- Cloudflare Zero Trust / Access application area

For Worker protection:

1. create an application protecting the Worker hostname
2. attach a policy that allows your service token
3. create the service token
4. copy the generated:
   - Client ID
   - Client Secret

Reference:

- https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/

### Step 6 - Fill `.env.bootstrap`

Copy:

```bash
cp .env.bootstrap.example .env.bootstrap
```

Then fill only the values for the path you chose.

### Step 7 - Run bootstrap

```bash
./bootstrap.sh
```

Interactive mode:

- prompts for Cloudflare values
- can collect optional Tunnel / Access runtime credentials
- can collect optional auto-provision inputs

Automation mode:

- reads `.env.bootstrap`

### Step 8 - Verify what happened

Check `.env`:

```bash
grep -E '^(CF_WORKER_URL|CF_WORKER_NAME|TUNNEL_TOKEN|CF_ACCESS_CLIENT_ID|CF_ACCESS_CLIENT_SECRET)=' .env
```

If using auto-provision, also check:

```bash
ls -l data/cf-resources.json
```

That file should contain non-secret Cloudflare resource IDs only.

---

## 7. Benefits and Dangers of Full API Access

### Benefits

- easiest operator experience
- fewer manual steps
- bootstrap can create and later tear down Cloudflare resources cleanly
- one source of authority during setup

### Dangers

- broader blast radius if the token is stolen
- more Cloudflare resources can be modified by that one token
- mistakes in the wrong account/zone are more consequential

### Good operator hygiene

- use a dedicated Subumbra Cloudflare API token
- restrict it to the intended account and zone
- do not reuse a general-purpose personal admin token if you can avoid it
- keep `.env.bootstrap` temporary
- rotate tokens if you suspect exposure

---

## 8. How to Lock It Down After Setup

After initial setup:

1. let `bootstrap.sh` shred `.env.bootstrap`
2. keep only runtime secrets in `.env`
3. store the high-authority `CF_API_TOKEN` outside the runtime host env
4. rotate service tokens and tunnel tokens periodically
5. if you used auto-provision and want to tear down managed resources:

```bash
./bootstrap.sh --nuke-cloudflare
```

If you want to reduce risk further:

- move from auto-provision to manual/BYOC for day-2 operations
- replace broad setup-time authority with a more narrowly scoped token later

---

## 9. Minimal-Access / Manual Path

If you do **not** want broad Cloudflare API authority:

- create Tunnel manually
- create Access app/policy/service token manually
- copy only the runtime credentials into bootstrap

That path means:

- more dashboard work for you
- less Cloudflare-side mutation power in `CF_API_TOKEN`
- no automatic creation/teardown of Tunnel or Access resources

This is often a good path for cautious operators.

---

## 10. Adopt-Existing Limits

Cloudflare does not re-display:

- Tunnel tokens
- Access service-token secrets

So if you already created those resources elsewhere, Subumbra can only use them if **you** supply the runtime secrets.

If the IDs exist but the secrets are gone, the practical choices are:

- provide the original secrets manually, or
- recreate the resources

---

## 11. Common Subumbra Patterns

### Pattern A - Smallest working setup

- Worker + KV only
- no Tunnel
- no Access

### Pattern B - Remote UI through Tunnel

- Worker + KV
- Tunnel for the UI
- optional Access for the UI hostname

### Pattern C - Worker protected by CF Access

- Worker + KV
- Access service token used by the proxy when calling the Worker

### Pattern D - Full Cloudflare-managed path

- Worker + KV
- Tunnel auto-provision
- DNS auto-provision
- Access auto-provision
- `data/cf-resources.json` tracks created IDs
- `./bootstrap.sh --nuke-cloudflare` tears them down

## 12. Gate approve/deny bypass

Gate approval links must stay outside the normal Worker service-token Access
policy so a browser notification can open them directly, but the bypass must be
as narrow as possible.

Subumbra's Gate day-2 path provisions path-scoped self-hosted Access apps only
for:

- `/gate/approve/*`
- `/gate/deny/*`

Those apps receive `decision: bypass` policies. All other Worker routes stay on
the existing service-token model.

Use:

```bash
./bootstrap.sh --update-gate
```

---

## 13. Related Subumbra Docs

- [Install guide](subumbra-install.md)
- [Operator guide](operator-guide.md)
- [Testing guide](subumbra-testing.md)
- [Developer guide](subumbra-developer.md)

For the older Tunnel/Access-focused doc name, see:

- [cloudflare-tunnel-access.md](cloudflare-tunnel-access.md)
