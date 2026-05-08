FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN useradd --create-home --uid 10001 subumbra \
    && chown -R subumbra:subumbra /app

USER subumbra

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
