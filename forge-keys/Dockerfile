FROM python:3.12-slim

# Non-root user for least-privilege execution
RUN useradd --no-create-home --shell /bin/false forge

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Data dir owned by forge user; bootstrap writes here via shared volume
RUN mkdir -p /app/data && chown forge:forge /app/data

USER forge

EXPOSE 9090

# Single worker — keys.json reads are cheap; HMAC validation is CPU-light
CMD ["gunicorn", "--bind", "0.0.0.0:9090", "--workers", "2", "--threads", "4", \
     "--timeout", "30", "--access-logfile", "-", "--error-logfile", "-", \
     "--log-level", "info", "app:app"]
