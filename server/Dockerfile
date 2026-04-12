FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DATABASE_URL=sqlite:///./data/app.db \
    DNS_DOMAIN=t.example.com \
    DNS_DOMAINS= \
    DNS_PORT=5533

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt manage.py alembic.ini ./

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY app/db/alembic ./app/db/alembic
COPY app ./app
COPY docker-entrypoint.sh ./docker-entrypoint.sh
COPY .env.example ./.env.example

RUN mkdir -p /app/data \
    && chmod +x /app/docker-entrypoint.sh

EXPOSE 5533/udp 5533/tcp

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "manage.py", "dns-bridge-server"]
