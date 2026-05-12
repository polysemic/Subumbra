FROM python:3.12-slim

RUN useradd --no-create-home --shell /bin/false ui

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/
COPY static/ static/

USER ui

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "90", \
     "--access-logfile", "-", "--error-logfile", "-", "app:app"]
