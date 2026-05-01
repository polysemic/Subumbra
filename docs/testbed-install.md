# Subumbra Testbed — Baseline App Install Guide

*Install LiteLLM, OpenWebUI, and N8N as independent services on the VPS.
Each app runs with raw API keys. This is the "before" state — verify everything
works clean, then Subumbra bootstraps against these `.env` files, encrypts the
keys, and the apps continue working identically.*

**Prerequisites:** VPS baseline from `docs/vps-deployment.md` complete — SSH
hardening, UFW (ports 80/443 open), nginx, certbot already installed.
Subumbra itself does not need to be running yet.

---

## Port Map

| App | Internal bind | Nginx upstream | Suggested subdomain |
|-----|--------------|---------------|-------------------|
| LiteLLM | `127.0.0.1:4000` | `http://127.0.0.1:4000` | `litellm.yourdomain.com` |
| OpenWebUI | `127.0.0.1:3000` | `http://127.0.0.1:3000` | `chat.yourdomain.com` |
| N8N | `127.0.0.1:5678` | `http://127.0.0.1:5678` | `n8n.yourdomain.com` |

All services bind to `127.0.0.1` only — nothing exposed directly to the internet.
Nginx + certbot handles TLS termination and reverse proxying.

---

## 1. Shared Docker Network

All testbed apps join a shared network. When Subumbra is added later,
it joins the same network — services can reach each other by name.

```bash
docker network create subumbra-net
```

You only run this once. All compose files reference it as an external network.

---

## 2. LiteLLM

### 2.1 Create directory and files

```bash
sudo mkdir -p /opt/litellm
sudo chown -R "$USER":"$USER" /opt/litellm
cd /opt/litellm
```

### 2.2 `.env`

This is the file Subumbra's bootstrap will later ingest and shred.
Populate it with your real keys — same ones you have on your other server.

```bash
cat > .env << 'EOF'
# Provider API keys — Subumbra will encrypt and shred these
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
DEEPSEEK_API_KEY=...
GEMINI_API_KEY=...
MISTRAL_API_KEY=...
XAI_API_KEY=...
GITHUB_TOKEN=ghp_...
SLACK_BOT_TOKEN=xoxb-...
SENDGRID_API_KEY=SG....

# LiteLLM internal master key — generate a strong random value
# This is NOT a provider key. Subumbra does not touch this.
LITELLM_MASTER_KEY=sk-master-changeme

# LiteLLM database (for spend tracking, virtual keys)
DATABASE_URL=postgresql://litellm:litellm@litellm-db:5432/litellm
EOF
```

Generate a real master key:

```bash
openssl rand -hex 32
# Replace sk-master-changeme above with the output
nano .env
```

### 2.3 `config.yaml`

LiteLLM model definitions. Each `api_key` uses `os.environ/KEY_NAME` to
read from `.env` — no raw keys in this file.

```bash
cat > config.yaml << 'EOF'
model_list:
  - model_name: claude-sonnet-4
    litellm_params:
      model: anthropic/claude-sonnet-4-5
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: claude-haiku-4
    litellm_params:
      model: anthropic/claude-haiku-4-5-20251001
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY

  - model_name: gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY

  - model_name: llama-3.3-70b
    litellm_params:
      model: groq/llama-3.3-70b-versatile
      api_key: os.environ/GROQ_API_KEY

  - model_name: deepseek-chat
    litellm_params:
      model: deepseek/deepseek-chat
      api_key: os.environ/DEEPSEEK_API_KEY

  - model_name: gemini-2.0-flash
    litellm_params:
      model: gemini/gemini-2.0-flash
      api_key: os.environ/GEMINI_API_KEY

  - model_name: mistral-large
    litellm_params:
      model: mistral/mistral-large-latest
      api_key: os.environ/MISTRAL_API_KEY

  - model_name: grok-beta
    litellm_params:
      model: xai/grok-beta
      api_key: os.environ/XAI_API_KEY

  - model_name: github-gpt-4o
    litellm_params:
      model: github/gpt-4o
      api_key: os.environ/GITHUB_TOKEN

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL

litellm_settings:
  drop_params: true
  request_timeout: 600
EOF
```

Add or remove models to match your actual keys. The important thing is
the `os.environ/KEY_NAME` pattern — never put raw keys in this file.

### 2.4 `docker-compose.yml`

```bash
cat > docker-compose.yml << 'EOF'
services:
  litellm:
    image: ghcr.io/berriai/litellm:main-stable
    container_name: litellm
    restart: unless-stopped
    ports:
      - "127.0.0.1:4000:4000"
    volumes:
      - ./config.yaml:/app/config.yaml:ro
    env_file:
      - .env
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    depends_on:
      litellm-db:
        condition: service_healthy
    networks:
      - subumbra-net

  litellm-db:
    image: postgres:16-alpine
    container_name: litellm-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: litellm
      POSTGRES_PASSWORD: litellm
      POSTGRES_DB: litellm
    volumes:
      - litellm_db_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U litellm"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - subumbra-net

volumes:
  litellm_db_data:

networks:
  subumbra-net:
    external: true
EOF
```

### 2.5 Start and verify

```bash
docker compose up -d
docker compose ps        # both services should be healthy within ~30s

# Quick health check
export LITELLM_MASTER_KEY="$(sed -n 's/^LITELLM_MASTER_KEY=//p' .env)"
curl -s -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
     http://127.0.0.1:4000/health | jq .

# Test a real completion
curl -s http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-4","messages":[{"role":"user","content":"say hi in 3 words"}],"max_tokens":20}' \
  | jq '.choices[0].message.content'
```

Expected: a short response from the model. If this works, LiteLLM is healthy
with raw keys. Move on.

---

## 3. OpenWebUI

OpenWebUI connects **directly to OpenAI API** as its baseline — no LiteLLM
in the middle. When Subumbra is added, the only change is what URL it points at.

### 3.1 Create directory

```bash
sudo mkdir -p /opt/open-webui
sudo chown -R "$USER":"$USER" /opt/open-webui
cd /opt/open-webui
```

### 3.2 `.env`

```bash
cat > .env << 'EOF'
# OpenWebUI baseline — direct OpenAI connection
# Subumbra will later replace OPENAI_API_KEY with a proxy token
# and OPENAI_API_BASE_URL with the proxy endpoint.
OPENAI_API_KEY=sk-...
OPENAI_API_BASE_URL=https://api.openai.com/v1

# Disable auth for local testing — enable before any public exposure
WEBUI_AUTH=false

# Internal secret — not a provider key, Subumbra does not touch this
WEBUI_SECRET_KEY=changeme-random-string
EOF
```

Generate a real secret key:

```bash
openssl rand -hex 32
# Replace changeme-random-string above
nano .env
```

### 3.3 `docker-compose.yml`

```bash
cat > docker-compose.yml << 'EOF'
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    container_name: open-webui
    restart: unless-stopped
    ports:
      - "127.0.0.1:3000:8080"
    volumes:
      - open_webui_data:/app/backend/data
    env_file:
      - .env
    networks:
      - subumbra-net

volumes:
  open_webui_data:

networks:
  subumbra-net:
    external: true
EOF
```

### 3.4 Start and verify

```bash
docker compose up -d
docker compose ps

# Check it's up
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:3000/
# Expected: 200
```

Then open `http://127.0.0.1:3000` in a browser (via SSH tunnel if needed)
and verify you can send a chat message. If it responds, OpenWebUI is working
with raw keys pointed at OpenAI directly.

---

## 4. N8N

N8N is a workflow automation tool. Its API credentials are stored in its
own internal database (not a flat `.env`). For the baseline we just verify
it starts and the UI is accessible. Real API integrations are configured
through the N8N UI.

### 4.1 Create directory

```bash
sudo mkdir -p /opt/n8n
sudo chown -R "$USER":"$USER" /opt/n8n
cd /opt/n8n
```

### 4.2 `.env`

```bash
cat > .env << 'EOF'
# N8N internal config
N8N_HOST=0.0.0.0
N8N_PORT=5678
N8N_PROTOCOL=https
WEBHOOK_URL=https://n8n.yourdomain.com/

# Encryption key for stored credentials — generate and save this
N8N_ENCRYPTION_KEY=changeme-random-string

# Basic auth for the UI
N8N_BASIC_AUTH_ACTIVE=true
N8N_BASIC_AUTH_USER=admin
N8N_BASIC_AUTH_PASSWORD=changeme-strong-password

# Timezone
GENERIC_TIMEZONE=America/New_York
TZ=America/New_York
EOF
```

Generate real values:

```bash
openssl rand -hex 32   # use as N8N_ENCRYPTION_KEY
openssl rand -hex 16   # use as N8N_BASIC_AUTH_PASSWORD
nano .env
```

> **Important:** Back up `N8N_ENCRYPTION_KEY` securely. If you lose it,
> all stored credentials in N8N are unrecoverable.

### 4.3 `docker-compose.yml`

```bash
cat > docker-compose.yml << 'EOF'
services:
  n8n:
    image: n8nio/n8n:latest
    container_name: n8n
    restart: unless-stopped
    ports:
      - "127.0.0.1:5678:5678"
    volumes:
      - n8n_data:/home/node/.n8n
    env_file:
      - .env
    networks:
      - subumbra-net

volumes:
  n8n_data:

networks:
  subumbra-net:
    external: true
EOF
```

### 4.4 Start and verify

```bash
docker compose up -d
docker compose ps

curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5678/
# Expected: 200 (or 401 with auth active — both mean N8N is up)
```

---

## 5. Nginx Reverse Proxy

One config block per app. Run these after replacing `yourdomain.com` with
your actual domain.

### 5.1 Create config files

Start with HTTP-only blocks. Do **not** include SSL lines yet — the cert
doesn't exist, nginx won't load, and certbot can't proceed. Certbot adds
the SSL block itself after issuing the cert.

```bash
# LiteLLM
sudo tee /etc/nginx/sites-available/litellm << 'EOF'
server {
    listen 80;
    server_name litellm.yourdomain.com;

    client_max_body_size 50M;

    location / {
        proxy_pass         http://127.0.0.1:4000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
        proxy_buffering    off;
    }
}
EOF

# OpenWebUI
sudo tee /etc/nginx/sites-available/open-webui << 'EOF'
server {
    listen 80;
    server_name chat.yourdomain.com;

    client_max_body_size 50M;

    location / {
        proxy_pass         http://127.0.0.1:3000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
        proxy_buffering    off;
        # WebSocket support (required for OpenWebUI streaming)
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
    }
}
EOF

# N8N
sudo tee /etc/nginx/sites-available/n8n << 'EOF'
server {
    listen 80;
    server_name n8n.yourdomain.com;

    client_max_body_size 50M;

    location / {
        proxy_pass         http://127.0.0.1:5678;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
        proxy_buffering    off;
        # WebSocket support (required for N8N)
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
    }
}
EOF
```

### 5.2 Enable sites and get certificates

```bash
# Enable sites
sudo ln -s /etc/nginx/sites-available/litellm    /etc/nginx/sites-enabled/
sudo ln -s /etc/nginx/sites-available/open-webui /etc/nginx/sites-enabled/
sudo ln -s /etc/nginx/sites-available/n8n        /etc/nginx/sites-enabled/

# Verify config loads clean before certbot
sudo nginx -t && sudo systemctl reload nginx

# Get TLS certificates — certbot rewrites each config to add the SSL block
# DNS A records for all three subdomains must exist and propagate first
sudo certbot --nginx -d litellm.yourdomain.com
sudo certbot --nginx -d chat.yourdomain.com
sudo certbot --nginx -d n8n.yourdomain.com
```

> **DNS prerequisite:** Create A records for `litellm`, `chat`, and `n8n`
> pointing at your VPS IP before running certbot. Certbot does an HTTP
> challenge — the domain must resolve to this server.

> **Order matters:** HTTP-only config first → `nginx -t` passes → certbot
> runs → certbot adds SSL server block → done. Adding SSL lines manually
> before the cert exists breaks nginx and blocks certbot.

If the output says the syntax is OK, reload nginx:

```bash
sudo systemctl reload nginx
```

---

## 6. Baseline Verification Checklist

Run through this before touching Subumbra. Every item should pass.

```bash
# 1. All containers running
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "litellm|open-webui|n8n"

# 2. LiteLLM health
export LITELLM_MASTER_KEY="$(sed -n 's/^LITELLM_MASTER_KEY=//p' /opt/litellm/.env)"
curl -s -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
     https://litellm.yourdomain.com/health | jq '.status'
# Expected: "healthy"

# 3. LiteLLM real completion (pick any model from your config)
curl -s https://litellm.yourdomain.com/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-4","messages":[{"role":"user","content":"ping"}],"max_tokens":5}' \
  | jq '.choices[0].message.content'

# 4. OpenWebUI reachable
curl -s -o /dev/null -w "%{http_code}" https://chat.yourdomain.com/
# Expected: 200

# 5. N8N reachable
curl -s -o /dev/null -w "%{http_code}" https://n8n.yourdomain.com/
# Expected: 200 or 401 (both mean it's up)
```

If all five pass: **baseline is confirmed.** The apps are working with raw keys.
This is the state you capture before Subumbra is involved.

---

## 7. What Subumbra Bootstrap Will Do Later

When you run Subumbra bootstrap, it will:

1. Read `/opt/litellm/.env` (or you paste keys into the wizard)
2. Encrypt each key into a Subumbra record
3. Shred the raw values from the source `.env`
4. Write runtime tokens to `/opt/subumbra/.env`

Then the apps are re-pointed:

| App | Before | After |
|-----|--------|-------|
| LiteLLM | `api_key: os.environ/ANTHROPIC_API_KEY` | `api_base: http://subumbra-proxy:8090/t/anthropic_prod` + `api_key: ${SUBUMBRA_TOKEN_LITELLM}` |
| OpenWebUI | `OPENAI_API_BASE_URL=https://api.openai.com/v1` | `OPENAI_API_BASE_URL=http://subumbra-proxy:8090/t/openai_prod/v1` |
| N8N | Credentials stored in N8N DB | HTTP/node base URL carries `<key_id>` in the path and uses the n8n adapter token |

The apps continue working identically. The keys are gone.

---

## Notes

- `WEBUI_AUTH=false` on OpenWebUI is fine for local testing. Enable it (`true`)
  before sharing the URL with anyone else.
- N8N's `N8N_ENCRYPTION_KEY` must never change after first use — back it up.
- LiteLLM's `LITELLM_MASTER_KEY` is not a provider API key. Subumbra does not
  protect inter-service credentials in this round. It stays in `.env` as-is.
- The `subumbra-net` Docker network is what allows services to reach each other
  by container name after Subumbra joins. It costs nothing to create it now.
