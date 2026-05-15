# Pinned by digest — supply-chain protection against retagged base images.
# Refresh via: docker pull python:3.12-slim && docker inspect ... | grep RepoDigests
FROM python:3.12-slim@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461

# Non-root user for least-privilege execution
RUN useradd --no-create-home --shell /bin/false subumbra

WORKDIR /app

# requirements.txt is hash-locked via pip-tools (see requirements.in).
# --require-hashes fails the build if any wheel or sdist hash does not match.
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY app.py .

# Data dir owned by subumbra user; bootstrap writes here via shared volume
RUN mkdir -p /app/data /app/audit && chown subumbra:subumbra /app/data /app/audit

USER subumbra

EXPOSE 9090

# Two workers with four threads; keep runtime config and comment aligned.
CMD ["gunicorn", "--bind", "0.0.0.0:9090", "--workers", "2", "--threads", "4", \
     "--timeout", "30", "--access-logfile", "-", "--error-logfile", "-", \
     "--log-level", "info", "--no-control-socket", "app:app"]
