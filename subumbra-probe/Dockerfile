# Pinned by digest — supply-chain protection against retagged base images.
# Refresh via: docker pull python:3.12-slim && docker inspect ... | grep RepoDigests
FROM python:3.12-slim@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461

WORKDIR /app

# requirements.txt is hash-locked via pip-tools (see requirements.in).
# --require-hashes fails the build if any wheel or sdist hash does not match.
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY probe.py .

CMD ["python", "/app/probe.py"]
