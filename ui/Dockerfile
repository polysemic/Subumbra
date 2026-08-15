# Pinned by digest — supply-chain protection against retagged base images.
# Refresh via: docker pull python:3.13-slim && docker inspect ... | grep RepoDigests
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a

RUN useradd --no-create-home --shell /bin/false ui

WORKDIR /app

# requirements.txt is hash-locked via pip-tools (see requirements.in).
# --require-hashes fails the build if any wheel or sdist hash does not match.
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY app.py console_data.py ./
COPY _hash_utils.py .
COPY templates/ templates/
COPY static/ static/

USER ui

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "90", \
     "--access-logfile", "-", "--error-logfile", "-", "app:app"]
