# Pinned by digest — supply-chain protection against retagged base images.
# Refresh via: docker pull python:3.13-slim && docker inspect ... | grep RepoDigests
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a

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
