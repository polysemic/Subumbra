FROM node:20-slim

# Install Python 3 + pip from Debian repos — no curl|bash
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Pin wrangler — v4+ required; exact pin prevents silent drift
RUN npm install -g wrangler@4.90.1

# ── Python deps ───────────────────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY subumbra-bootstrap.py .
COPY templates/ /app/templates/

# Data volume (shared with subumbra-keys)
RUN mkdir -p /app/data

# -u = unbuffered so progress lines appear immediately in docker logs
ENTRYPOINT ["python3", "-u", "subumbra-bootstrap.py"]
