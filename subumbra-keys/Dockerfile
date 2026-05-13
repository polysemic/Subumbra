FROM python:3.12-slim

# Non-root user for least-privilege execution
RUN useradd --no-create-home --shell /bin/false subumbra

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Data dir owned by subumbra user; bootstrap writes here via shared volume
RUN mkdir -p /app/data /app/audit && chown subumbra:subumbra /app/data /app/audit

USER subumbra

EXPOSE 9090

# Two workers with four threads; keep runtime config and comment aligned.
CMD ["gunicorn", "--bind", "0.0.0.0:9090", "--workers", "2", "--threads", "4", \
     "--timeout", "30", "--access-logfile", "-", "--error-logfile", "-", \
     "--log-level", "info", "--no-control-socket", "app:app"]
