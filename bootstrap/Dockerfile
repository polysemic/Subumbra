# Wrangler v4.90+ requires Node 22+; image stays self-contained (no host Node).
# Pinned by digest — supply-chain protection against retagged base images.
# Refresh via: docker pull node:22-bookworm-slim && docker inspect ... | grep RepoDigests
FROM node:22-bookworm-slim@sha256:689c11043dad91472750cd824c97dd5e2318e9dd6f954e492fe7af0135d33ceb

# Install Python 3 + pip from Debian repos — no curl|bash
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install wrangler from package-lock.json — npm ci verifies SRI integrity hashes
# for wrangler and every transitive dep. Regenerate lockfile via:
#   docker run --rm -v "$PWD/bootstrap":/w -w /w node:22-bookworm-slim \
#     npm install --package-lock-only --ignore-scripts
WORKDIR /opt/wrangler
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts \
    && ln -s /opt/wrangler/node_modules/.bin/wrangler /usr/local/bin/wrangler

# ── Python deps ───────────────────────────────────────────────────────────────
WORKDIR /app

# requirements.txt is hash-locked via pip-tools (see requirements.in).
# --require-hashes fails the build if any wheel or sdist hash does not match.
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages --require-hashes -r requirements.txt

COPY subumbra-bootstrap.py .
COPY templates/ /app/templates/

# Data volume (shared with subumbra-keys)
RUN mkdir -p /app/data

# -u = unbuffered so progress lines appear immediately in docker logs
ENTRYPOINT ["python3", "-u", "subumbra-bootstrap.py"]
