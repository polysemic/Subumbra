FROM node:20-slim

# Install Python 3 + pip from Debian repos — no curl|bash
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Pin wrangler — v4+ required; exact pin prevents silent drift
RUN npm install -g wrangler@4.77.0

# ── Python deps ───────────────────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY keyvault-bootstrap.py .

# Data volume (shared with forge-keys)
RUN mkdir -p /app/data

# -u = unbuffered so progress lines appear immediately in docker logs
ENTRYPOINT ["python3", "-u", "keyvault-bootstrap.py"]
