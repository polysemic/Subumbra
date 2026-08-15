# Pinned by digest — supply-chain protection against retagged base images.
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a

WORKDIR /app

COPY agent.py requirements.txt ./

RUN pip install --no-cache-dir --require-hashes -r requirements.txt

RUN useradd --system --uid 1000 --gid 0 --home-dir /app --shell /usr/sbin/nologin subumbra \
    && chown -R subumbra:0 /app

USER subumbra

CMD ["python", "agent.py"]
